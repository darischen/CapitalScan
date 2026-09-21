"""Push the serving subset to the cloud store (ADR 053, ADR 137).

**One direction, always.** Local research is the source of truth and the
cloud copy is derived. Nothing here reads a row from serving and writes it
back, and nothing on the serving side is authored — a serving database can
be dropped and rebuilt from this job at any time, which is what makes it
safe to point a public site at.

**A serving cut, never a statistical one.** The subset is smaller in
*dates*, not in *answers*: `cell_stats`, `benchmarks` and every measured
number are computed locally against full history and shipped whole. A
reader of the deployed site sees fewer sessions on a chart, never a
different hit rate.

**Why the tables are enumerated rather than discovered.** The list below
came from `pg_depend` on the serving views, but it is written down because
a table appearing in a view is not consent to publish it: `bar_rejects`,
`runs` and `quotes_live` are all local diagnostics, and a discovery-based
sync would ship whichever of them a future view happened to touch. Adding
a table here is a deliberate act.

**The live session is not in the nightly cut, and ADR 153 is why that is
still right.** `bars_live` and `quotes_live` hold today's partial candle
and last quote, rewritten every five minutes by the poller. A *nightly*
copy would give the deployed site a price frozen at whenever the sync ran
and label it live — the exact failure ADR 131 and ADR 134 fixed, and worse
remotely because nobody there can see the poller is not running.

That reasoning is about the copy **frequency**, not about the tables. So
they are absent from `_tables()` below and present in `_live_tables()`,
which `run_live_sync` pushes after every poll tick. The serving store is
then in the position the workstation is already in: ADR 131's 45-second
client poll and ADR 134's session-hours guard both live in the view and
API layers and apply unchanged, and `poller_sessions` ships as a heartbeat
so a quiet session is distinguishable from a dead poller.

Adding either table to `_tables()` would reintroduce the original bug.
`test_sync_live.py::test_the_nightly_cut_still_excludes_the_live_session`
is what holds that line.

**Conflict keys are the tables' real constraints, checked against
`pg_constraint` rather than guessed.** The first version of this file
guessed three of them wrong — `serving_config` conflicts on `only_row` not
`id`, `cell_stats` on `(cell_id, config_hash)` with `run_id` deliberately
outside it, and `predictions` on `id`. Postgres rejects an `ON CONFLICT`
that names no unique constraint, so the wrong two failed loudly; the
`cell_stats` one would not have. Adding `run_id` to that key makes every
re-run insert a second row for the same cell instead of replacing it, and
the serving store would accumulate stale statistics that look current.
`test_sync.py` now asserts each key against the live constraint.

**Ordering is a foreign-key requirement, not a preference.** `tickers`
before everything that references it, `universe` before `events` reads it
for membership. `TABLES` is applied in order and `test_sync.py` asserts
that order satisfies the real constraints rather than trusting the list.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from string import Formatter
from typing import Any

import pandas as pd
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from capitalscan.core.cells import LONG_SIGNALS, SHORT_SIGNALS
from capitalscan.core.config import ServingParams
from capitalscan.jobs import db_io
from capitalscan.jobs.ingest import run_job

# Rows read from the source per round trip. Neon is over the network rather
# than a local socket, so the trip dominates and small batches are slow.
BATCH_ROWS = 5_000

# **Postgres accepts at most 65,535 bound parameters in one statement**, and
# SQLAlchemy's `insertmanyvalues` splits a multi-row INSERT into pages of
# 1,000 rows by default. That default is a latent overflow for any table
# wider than 65 columns:
#
#     events   75 columns x 1,000 rows = 75,000 parameters   FAILS
#     bars     14 columns x 1,000 rows = 14,000              fine
#
# `events` is 75 columns, so the third sync attempt died on it after seven
# minutes of successfully copying the narrow tables. The failure is a
# driver-level `OperationalError` reading "number of parameters must be
# between 0 and 65535", which names neither the table nor the page size.
#
# Chunked here rather than through SQLAlchemy's `insertmanyvalues_page_size`,
# which an `ON CONFLICT DO UPDATE` does not honour — setting it on the
# engine changed nothing and `events` failed identically. Computing the
# batch from the frame's own width is also self-adjusting: a column added
# to `events` shrinks the batch instead of breaking the sync.
MAX_BIND_PARAMS = 60_000


def _rows_per_batch(n_columns: int) -> int:
    """How many rows fit in one statement, given the table's width.

    `MAX_BIND_PARAMS` is 60,000 against Postgres's hard 65,535, leaving
    headroom for the `ON CONFLICT DO UPDATE SET` clause, which binds no
    parameters today but is one refactor away from doing so.
    """
    if n_columns <= 0:
        return BATCH_ROWS
    return max(1, min(BATCH_ROWS, MAX_BIND_PARAMS // n_columns))


@dataclass(frozen=True)
class Remap:
    """Rewrite a surrogate reference into the *target's* id space.

    **A surrogate id does not survive this copy, and a stale one resolves.**
    `events` syncs on a natural tuple, so serving mints `events.id` from its
    own sequence and the two stores allocate independently out of one
    numeric range. A `predictions.event_id` copied verbatim therefore names
    whatever row happens to hold that integer on the other side.

    Measured on serving 2026-09-09, before this existed: of 20,200
    predictions, 7,403 had an `event_id` matching no event at all and
    **3,035 matched the wrong one** — PRGO's 2026-08-05 prediction pointed
    at an SMTC event from 2020-07-13, NRG's at PKX from 2018. Those links
    resolve, join cleanly, and are wrong, which is the failure mode that
    reports nothing.

    So the column is rewritten rather than shipped or nulled. `column` is
    resolved by looking `source_key` up in `table` on the target and taking
    its id; a row whose key finds nothing gets NULL, which is the honest
    value and the one `pull_live_records` already writes for
    `signal_reports.event_id`.
    """

    #: The frame column holding the source's id, overwritten in place.
    column: str
    #: Target table to resolve against.
    table: str
    #: Frame columns forming the natural key, paired positionally with
    #: `target_key`. Named separately because `predictions.as_of` is
    #: `events.signal_date`.
    source_key: tuple[str, ...]
    target_key: tuple[str, ...]
    #: Target column to read back. Its own surrogate, by definition.
    target_id: str = "id"
    #: The target enforces uniqueness on `column`, so a row already holding
    #: a remapped value must give way. `run_sync` (outbound) acts on this
    #: flag through `_clear_remap_collisions`, which deletes the target's
    #: colliding row -- safe there because serving is a disposable copy.
    #: `_pull_predictions` (inbound) does not read this flag; it always
    #: runs `_null_inbound_remap_collisions`, which nulls the *incoming*
    #: row's link instead, because inbound the target is research and
    #: neither row may be deleted or rewritten. See both functions.
    unique_on_target: bool = False


# **`predictions.event_id` names a different store's id on each side of the
# copy.** `_tables()` still rewrites it outbound (research -> serving, ADR
# 191, `event_id` "resolves, joins cleanly, and is wrong") through the
# natural key below -- `signal_type` IS part of a research-written
# prediction's identity, because research labels its own events and its own
# predictions the same way, so no ambiguity is possible there and this
# `Remap` resolves it correctly and unchanged.
#
# **Inbound is different since 2026-09-20 (design doc, slot-keyed
# adoption).** `pull_live_records`'s predictions step no longer resolves
# `event_id` through this natural key -- `_apply_slot_remap` does, on
# `(config_hash, ticker, signal_date, side, entry_kind)`, because the Pi's
# label and research's end-of-day label can disagree for the same slot
# (ADR 194) and the natural key below then matches nothing (measured:
# 0 of 338, 2026-09-20). This `Remap` still makes one inbound appearance,
# in `_null_inbound_remap_collisions`, which reads only `.column`
# ("event_id") and never `.source_key`/`.target_key` -- that call is not a
# second use of natural-key resolution, only a reuse of the column name.
_PREDICTIONS_EVENT_REMAP = Remap(
    column="event_id",
    table="events",
    source_key=("config_hash", "ticker", "as_of", "signal_type", "entry_kind"),
    target_key=("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
    unique_on_target=True,
)


@dataclass(frozen=True)
class SyncTable:
    """One table's subset, as a query and a conflict key.

    `sql` is a full SELECT rather than a WHERE fragment so a table that
    needs a join to be scoped — `bars` and `indicators` are scoped by
    trade-universe membership, which lives in another table — can express
    it without this module growing a query builder.
    """

    name: str
    sql: str
    key: tuple[str, ...]
    #: Surrogate references to rewrite before writing. See `Remap`.
    remaps: tuple[Remap, ...] = ()


_RESET_SEQUENCES_SQL = """
DO $$
DECLARE r record; n bigint;
BEGIN
  FOR r IN
    SELECT c.oid::regclass AS tbl, a.attname AS col,
           pg_get_serial_sequence(c.oid::regclass::text, a.attname) AS seq
      FROM pg_class c
      JOIN pg_attribute a ON a.attrelid = c.oid
       AND a.attnum > 0 AND NOT a.attisdropped
     WHERE c.relkind = 'r'
       AND pg_get_serial_sequence(c.oid::regclass::text, a.attname) IS NOT NULL
  LOOP
    IF r.tbl::text LIKE '%predictions' THEN
{predictions_branch}
    ELSE
      EXECUTE format('SELECT coalesce(max(%I),0) FROM %s', r.col, r.tbl) INTO n;
    END IF;
    IF n > 0 THEN PERFORM setval(r.seq, n); END IF;
  END LOOP;
END $$;
"""

# **One template per store, rendered two ways.** `_reset_sequences`
# discovers table and column from the catalog, so it needs
# `format('...', r.col, r.tbl)`; `predictions_max_id_sql` knows its table by
# name and interpolates directly. Those were two hand-written copies until
# 2026-09-20, and the research one is the single clause keeping research's
# ids below the floor -- reversing it in the catalog branch would have left
# `scripts/verify_id_floor.py` green, because the script executes the other
# copy. Both renderings now read the same template.
#
# **Serving mints at or above the floor.** `greatest` takes the larger of
# the table's own max and the floor, so a below-floor sequence (a fresh
# serving store, or one repaired after a Pi reflash) is raised to it, and a
# sequence already past it from ordinary growth is left alone.
#
# **Research stays below the floor.** The max is computed only over ids
# under it, so an adopted (Pi-born, billion-range) row cannot push
# research's own allocation up into serving's range and recreate the
# collision. A research store holding only adopted rows returns NULL,
# coalesced to 0, which the DO block's `IF n > 0` guard skips --
# `setval(seq, 0)` is an error in Postgres.
_MAX_ID_TEMPLATES: dict[str, str] = {
    "serving": "SELECT greatest(coalesce(max({col}),0), {floor}) FROM {tbl}",
    "research": "SELECT coalesce(max({col}),0) FROM {tbl} WHERE {col} < {floor}",
}


def _predictions_branch(*, serving: bool, floor: int) -> str:
    """The catalog-driven rendering: a `format()` call for the DO block.

    `{col}` becomes `%I`, `{tbl}` `%s` and `{floor}` `%L`, and the argument
    list is built **in the order the placeholders appear**, because
    `format()` binds positionally. Deriving the order from the template is
    the point: an edit that moves a placeholder moves its argument with it,
    where a hand-written list would silently read the wrong column.
    """
    template = _MAX_ID_TEMPLATES["serving" if serving else "research"]
    fields = [name for _, name, _, _ in Formatter().parse(template) if name]
    args = {"col": "r.col", "tbl": "r.tbl", "floor": str(floor)}
    fmt = template.format(col="%I", tbl="%s", floor="%L")
    return f"      EXECUTE format('{fmt}', {', '.join(args[f] for f in fields)}) INTO n;"


def predictions_max_id_sql(table: str, floor: int, *, serving: bool) -> str:
    """The same comparison, rendered for a caller that knows its table name.

    `scripts/verify_id_floor.py` proves the floor split against `zz_`
    scratch tables rather than the real `predictions` table -- running
    `_reset_sequences` there would reset the *real* sequence. It imports
    this function and executes it, so the script tests production's
    comparison instead of a copy of it.
    """
    return _MAX_ID_TEMPLATES["serving" if serving else "research"].format(
        col='"id"', tbl=f'"{table}"', floor=floor
    )


def _reset_sequences(engine: Engine, *, serving: bool) -> None:
    """Advance every serial sequence past its table's max id.

    **An explicit-id INSERT does not advance a sequence**, so any store
    populated by copying ends up holding rows its sequences have never
    seen. Both directions need this and only one had it until 2026-09-01.

    **Serving (`run_sync`, since 2026-08-28).** Invisible until the poller
    moved to write serving (ADR 158), because until then nothing ever
    *inserted* there. On the first live session the Pi's poller inserted a
    `signal_reports` row, got id 21, and id 21 already existed: serving
    held 1,829 rows with its sequence still at 21. `events_id_seq` was at
    21 against a max of 39,167,955 -- that one would have crashed on the
    first live event rather than the first report.

    **Research (`pull_live_records`, since 2026-09-01).** The same defect
    arriving the other way. The pull copies `signal_reports` with explicit
    ids, so research's `signal_reports_id_seq` drifted 211 behind
    `max(id)`, and the 2026-08-31 fallback poll failed on it
    (`signal_reports_pkey`, id 1832).

    Derived from the catalogue rather than a hardcoded list, which would go
    stale the moment a table gains a serial. **`AND a.attnum > 0 AND NOT
    a.attisdropped` is required**, not tidiness: `pg_get_serial_sequence`
    raises on a `........pg.dropped.N........` placeholder and aborts the
    whole statement. Skipped for an empty table because `setval(seq, 0)` is
    an error in Postgres.

    Belongs in the copy path rather than in a one-off repair: every sync
    copies ids again, so a sequence fixed by hand goes stale on the next
    run.

    **`predictions` is special, per store, since forward-log adoption
    (2026-09-19).** Serving and research now both mint `predictions.id` from
    their own sequence -- the same shape of defect that broke `events` under
    ADR 158, except `predictions` conflicts on `id` itself, so the surrogate
    cannot be dropped the way `_drop_surrogate_id` drops it for `events`.
    `serving` says which side of `ServingParams.serving_id_floor` this store
    must land on; see that field's docstring for the measured collision and
    why the split lives there rather than in `Config`.
    """
    floor = ServingParams().serving_id_floor
    sql = _RESET_SEQUENCES_SQL.format(
        predictions_branch=_predictions_branch(serving=serving, floor=floor)
    )
    with engine.begin() as conn:
        conn.execute(text(sql))


# `LONG_SIGNALS` and `SHORT_SIGNALS` are `core.cells`' pairing (ADR 102),
# the one place a `signal_type` is assigned a side. `handlers.enums.
# side_for_signal_type` reads the same two tuples for the MCP surface, but
# that function lives behind `handlers/errors.py::InvalidEnum`, a wire-
# contract exception Session 16 maps to a protocol error -- the right shape
# for an LLM caller, the wrong one for a sync job to raise and catch. The
# design doc (2026-09-20) also names `core/cells.py` itself as the source,
# not the handler wrapper, so this reads the two tuples directly rather
# than importing `handlers/`, which no other module under `jobs/` does
# today. `handlers/_db.py` reaches `jobs.db_io` with a deferred, function-
# local import specifically to keep `handlers/` downstream of `jobs/`
# (ADR 118's MCP-is-a-caller-of-handlers arrangement); a module-level
# `jobs -> handlers` import would invert that without tripping a circular-
# import error, which is worse than one that fails loudly.
_SIDE_BY_SIGNAL_TYPE: dict[str, str] = {t: "long" for t in LONG_SIGNALS} | {
    t: "short" for t in SHORT_SIGNALS
}


def slot_side(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach the slot's `side`, derived from `signal_type`.

    The slot-keyed remap (design doc, 2026-09-20) resolves `event_id`
    through `(config_hash, ticker, signal_date, side, entry_kind)` rather
    than through the label a detector happened to emit, because
    `bb_lower_touch` on the Pi and `bull_close_below_lower` at end of day
    can fill the same debounce slot (ADR 194) and disagree. `side` is not a
    column the frame carries on its own; it is a property of `signal_type`
    and this is the one place it gets computed for that use.

    An unrecognised `signal_type` raises rather than defaulting to a side.
    `core.cells.LONG_SIGNALS` and `SHORT_SIGNALS` cover every `SignalType`
    member today (`core/cells.py`'s own comment says so), so a value this
    rejects is either a typo in the caller's frame or a new member added to
    one tuple and not the other -- both are bugs worth stopping on, because
    a silently wrong side would link a long prediction to a short event.

    Returns a new frame; `frame` is never mutated.
    """
    unknown = sorted(set(frame["signal_type"]) - set(_SIDE_BY_SIGNAL_TYPE))
    if unknown:
        raise ValueError(
            f"signal_type {unknown!r} has no side in core.cells.LONG_SIGNALS "
            "/ SHORT_SIGNALS; every SignalType member must resolve to a "
            "side before it can key a slot"
        )
    return frame.assign(side=frame["signal_type"].map(_SIDE_BY_SIGNAL_TYPE))


def _apply_remap(frame: pd.DataFrame, target: Engine, spec: Remap) -> pd.DataFrame:
    """Rewrite `spec.column` into the target's id space. See `Remap`.

    **Bounded by the keys actually present, not by the table.** Serving
    holds 5,413,083 events for the live generation and the predictions
    being written span 41 distinct dates, so the lookup is restricted to
    those — the alternative, reading the whole table into a dict, moves
    five million rows to resolve twenty thousand.

    Rows whose key resolves to nothing get NULL rather than keeping the
    source id. Keeping it is what produced the 3,035 wrong links: an id
    from the other store is not a worse answer than NULL, it is a
    confidently wrong one.
    """
    if frame.empty or spec.column not in frame.columns:
        return frame
    missing = [c for c in spec.source_key if c not in frame.columns]
    if missing:
        raise ValueError(
            f"remap of {spec.column!r} needs {missing!r} in the frame; "
            "widen the table's SELECT to carry the natural key"
        )

    keys = frame[list(spec.source_key)].drop_duplicates()
    if keys.empty:
        return frame

    # One bounded SELECT per key column, ANDed. Every key column here is
    # low-cardinality over the rows being written (41 dates, ~700 tickers,
    # 7 signal types), so this narrows to a few thousand candidate rows
    # before the join in pandas does the exact matching.
    where = " AND ".join(f'"{t}" = ANY(:v{i})' for i, t in enumerate(spec.target_key))
    params = {
        f"v{i}": keys[src].drop_duplicates().tolist() for i, src in enumerate(spec.source_key)
    }
    cols = ", ".join(f'"{c}"' for c in (*spec.target_key, spec.target_id))
    lookup = pd.read_sql(
        text(f'SELECT {cols} FROM "{spec.table}" WHERE {where}'),  # noqa: S608 - fixed names
        target,
        params=params,  # type: ignore[arg-type]
    )
    if lookup.empty:
        return frame.assign(**{spec.column: None})

    # The ANY() filter is a superset — it matches any combination of the
    # values, not the tuples that actually occur — so the exact pairing is
    # this merge, not the query.
    lookup = lookup.rename(
        columns=dict(zip(spec.target_key, spec.source_key, strict=True))
        | {spec.target_id: "__new_id"}
    ).drop_duplicates(subset=list(spec.source_key))

    merged = frame.drop(columns=[spec.column]).merge(lookup, on=list(spec.source_key), how="left")
    merged = merged.rename(columns={"__new_id": spec.column})
    # `merge` reorders nothing but appends; put the column back where the
    # table expects to find it so `copy_upsert`'s column list is stable.
    return merged[[c for c in frame.columns if c in merged.columns]]


# **A sibling of `Remap`/`_apply_remap`, not an extension of them.** `Remap`
# assumes every `target_key` column is a real column on the target table --
# `_apply_remap` binds each one straight into the `WHERE ... = ANY(:vN)`
# clause. `side` is not: it is computed from `signal_type` by `slot_side`,
# on both sides of the join, and Postgres has no `events.side` column to
# filter on. So the slot lookup selects on the four columns that DO exist
# (`config_hash`, `ticker`, `signal_date`, `entry_kind`), derives `side` on
# the result in pandas, and only then joins -- a shape `_apply_remap` cannot
# express without either adding a "computed key column" concept it has no
# other user for, or letting the extra column leak into `Remap.target_key`
# and silently miscompile into a `WHERE "side" = ANY(...)` that would fail
# loudly (SQLAlchemy) or, worse, silently (a hand-rolled f-string). Sibling
# over extension.
#
# `predictions` carries the slot's ticker/date/entry_kind under `as_of`;
# `events` under `signal_date` -- named separately for the same reason
# `Remap.source_key`/`target_key` are, and paired positionally below.
_SLOT_SOURCE_KEY: tuple[str, ...] = ("config_hash", "ticker", "as_of", "side", "entry_kind")
_SLOT_TARGET_KEY: tuple[str, ...] = ("config_hash", "ticker", "signal_date", "side", "entry_kind")


def _apply_slot_remap(
    frame: pd.DataFrame, target: Engine, *, table: str = "events"
) -> tuple[pd.DataFrame, int, int]:
    """Resolve `event_id` on the debounce slot, not on the label (design
    doc, 2026-09-20).

    `table` names the events-shaped table the slot is resolved against and
    defaults to `"events"`, so every real caller (`_pull_predictions`,
    `scripts/backfill_prediction_event_ids.py`) is unaffected -- both call
    this positionally with two arguments and never see the parameter.
    It exists for the same reason `predictions_max_id_sql` takes a `table`
    argument: `scripts/verify_slot_adoption.py` points this function at a
    `zz_`-prefixed scratch table so it executes production's own SQL
    instead of a hand-copied mirror of it.

    **Why the label cannot be the key.** `breach_live` (the Pi, intraday)
    and the end-of-day pass fill the same debounce slot --
    `(ticker, signal_date, bound)`, `core/signals.py::debounce_key` -- with
    different labels when the close disagrees with the live read: the Pi
    has no close to confirm against, so it emits `bb_lower_touch` while the
    end-of-day pass sees the close still inside the band and writes
    `bull_close_below_lower` for the same bar (ADR 194). Measured
    2026-09-20: of 338 poller-written events since 2026-09-08, **zero**
    matched research's natural key, so every adopted row that night carried
    a NULL `event_id`. `side` is stable across that disagreement --
    `bb_lower_touch` and `bull_close_below_lower` are both `LONG_SIGNALS`
    -- so the slot resolves where the label cannot.

    **Ambiguity is answered with NULL, never a pick.** Measured: 15 of
    182,921 events since 2026-08-01 share a slot with a second event under
    a different `signal_type`. ADR 191 already settled this for the
    outbound remap -- a confidently wrong link is worse than an absent one
    -- and the same rule applies here: a slot resolving to more than one
    event gets `event_id = NULL`, same as a slot resolving to none, but the
    two are counted separately so a caller can tell "research never saw
    this ticker-date" from "research saw it twice".

    **Nothing is relabelled.** `frame["signal_type"]` is the value the
    reader saw live and this function never touches it; only `event_id`
    changes, to the row the *slot* points at.

    Returns `(frame, no_slot, ambiguous)`.
    """
    if frame.empty:
        return frame, 0, 0
    needed = ("config_hash", "ticker", "as_of", "signal_type", "entry_kind")
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise ValueError(f"slot remap needs {missing!r} in the frame; widen the query's SELECT")
    frame = slot_side(frame)

    keys = frame[list(_SLOT_SOURCE_KEY)].drop_duplicates()
    # `side` is excluded here -- it is not a database column, so it plays
    # no part in the SELECT's WHERE; `zip` pairs each source column with
    # its target name so `keys[src]` reads the frame's own column (`as_of`)
    # while the clause filters on the target's (`signal_date`).
    select_pairs = [
        (s, t) for s, t in zip(_SLOT_SOURCE_KEY, _SLOT_TARGET_KEY, strict=True) if t != "side"
    ]
    where = " AND ".join(f'"{t}" = ANY(:v{i})' for i, (_s, t) in enumerate(select_pairs))
    params = {f"v{i}": keys[s].drop_duplicates().tolist() for i, (s, _t) in enumerate(select_pairs)}
    events = pd.read_sql(
        text(
            "SELECT id, config_hash, ticker, signal_date, signal_type, entry_kind "  # noqa: S608
            f'FROM "{table}" WHERE {where}'
        ),
        target,
        params=params,  # type: ignore[arg-type]
    )
    if events.empty:
        return frame.drop(columns=["side"]).assign(event_id=None), len(frame), 0

    events = slot_side(events)
    lookup = events[[*_SLOT_TARGET_KEY, "id"]]

    # `duplicated(keep=False)` marks BOTH rows of a two-event slot, not
    # just the second -- the usual `drop_duplicates` "keep one" behaviour
    # is exactly what ADR 191 forbids here. What survives the `~mask` is
    # only the slots that resolve to exactly one event.
    ambiguous_mask = lookup.duplicated(subset=list(_SLOT_TARGET_KEY), keep=False)
    ambiguous_slots = (
        lookup.loc[ambiguous_mask, list(_SLOT_TARGET_KEY)]
        .drop_duplicates()
        .rename(columns=dict(zip(_SLOT_TARGET_KEY, _SLOT_SOURCE_KEY, strict=True)))
        .assign(__ambiguous=True)
    )
    unique_lookup = (
        lookup.loc[~ambiguous_mask]
        .drop_duplicates(subset=list(_SLOT_TARGET_KEY))
        .rename(
            columns=dict(zip(_SLOT_TARGET_KEY, _SLOT_SOURCE_KEY, strict=True)) | {"id": "__new_id"}
        )
    )

    merged = frame.drop(columns=["event_id"]).merge(
        unique_lookup, on=list(_SLOT_SOURCE_KEY), how="left"
    )
    merged = merged.merge(ambiguous_slots, on=list(_SLOT_SOURCE_KEY), how="left")
    merged = merged.rename(columns={"__new_id": "event_id"})
    is_ambiguous = merged["__ambiguous"].fillna(False).astype(bool)
    ambiguous = int(is_ambiguous.sum())
    no_slot = int(merged["event_id"].isna().sum()) - ambiguous

    merged = merged.drop(columns=["__ambiguous", "side"])
    # Same reasoning as `_apply_remap`: put `event_id` back where the frame
    # expects it and drop the columns this function added.
    out = merged[[c for c in frame.columns if c in merged.columns]]
    return out, no_slot, ambiguous


def _null_duplicate_slot_targets(
    frame: pd.DataFrame, column: str = "event_id"
) -> tuple[pd.DataFrame, int]:
    """Null `column` on every row of THIS frame whose value is claimed by
    more than one row in it. Found in review, 2026-09-20 -- not caught by
    anything upstream.

    **The slot is coarser than the natural key it replaced, on the SOURCE
    side too.** `_apply_slot_remap` already forbids one prediction from
    picking among several matching *events*. What it does not forbid --
    because it resolves one row at a time -- is two DIFFERENT predictions
    resolving to the SAME event. That happens whenever two serving events
    sit in one debounce slot under different labels, the same phenomenon
    measured as 15 slots in research (design doc, "Ambiguity"): each
    serving event has its own prediction, because
    `predictions_event_id` is unique per *serving* event, not per slot, so
    the Pi can legitimately hold two predictions that both resolve to the
    one research event research recorded for that slot.

    **Why this is not survivable at the write.** `predictions_event_id` is
    also unique on research. `insert_new`'s `ON CONFLICT (id) DO NOTHING`
    covers `id`, not `event_id`, so two rows carrying the same `event_id`
    make the INSERT itself raise -- and because `_pull_predictions`'s
    selection is floor-scoped with no date bound (by design, so a
    once-off adoption is never missed), an unguarded pair would raise on
    the same two rows every night forever, exactly the failure shape this
    module's own docstrings warn about elsewhere.

    **Never pick one of the pair to keep.** Same ADR 191 rule
    `_apply_slot_remap` applies on the events side: nulling both rows is
    the only answer that is not a guess about which prediction the
    research event "really" belongs to. Both still adopt -- unresolved,
    not dropped.

    **Callers must run `_null_inbound_remap_collisions` first.** If one
    member of a pair was already adopted on a prior night, it is a
    legitimate owner of the event on the target, not a peer this function
    should null alongside its newer sibling -- `_pull_predictions` runs
    the collision check first for exactly that reason, so by the time
    this function sees the frame, an already-owned row has already been
    excluded from the pair (see `_pull_predictions`'s docstring, "This
    runs AFTER the collision check").

    Returns `(frame, duplicate_target)`. Counted separately from
    `no_slot`/`ambiguous` (which describe the slot lookup itself) and from
    `_null_inbound_remap_collisions`'s count (an incoming row colliding
    with a row already on the target) -- this is a third, distinct reason
    a row ends up unresolved: two incoming rows colliding with EACH OTHER.
    """
    if frame.empty or column not in frame.columns:
        return frame, 0
    counts = frame[column].value_counts(dropna=True)
    duplicated_values = set(counts[counts > 1].index)
    if not duplicated_values:
        return frame, 0
    mask = frame[column].isin(duplicated_values)
    frame = frame.copy()
    frame.loc[mask, column] = None
    return frame, int(mask.sum())


def _clear_remap_collisions(
    frame: pd.DataFrame,
    target: Engine,
    table: str,
    key: tuple[str, ...],
    spec: Remap,
) -> int:
    """Delete target rows that hold a remapped value the incoming rows claim.

    **Two writers reach serving and they identify a prediction
    differently.** `jobs/predict.py` upserts on `event_id`; this sync
    upserts on `id`. Before the remap they never collided, because the ids
    they carried were from different stores and never matched — which is
    the same reason the links were wrong. Making `event_id` correct makes
    the collision real: the Pi's row and research's row for one event now
    claim the same `event_id`, and `predictions_event_id` is UNIQUE.

    Research is the authority for a row it has scored, so its row wins and
    the other is deleted.

    **The "identical probabilities" claim this docstring used to make is
    false, and was measured false on 2026-09-20.** It said the pairs carry
    the same numbers, so nothing is lost but a duplicate. Across the 18
    pairs then live, *every* pair disagreed: the serving copy came from the
    artifact that was live when the signal fired (`924235e`, `fda17f9`) and
    research's from the weekly refit that rewrote it (`0e83149`), differing
    by up to **12.3 points** on `p_touch_3` (CRS 2026-09-14: 0.823 live
    against 0.700). So this delete does discard the number a reader saw,
    and replaces it with a later model's.

    Kept deliberately, on the owner's call (2026-09-20): the newer fit is
    the better estimate, and the pairs only exist for signals scored before
    `pull_live_records` began adopting the Pi's rows ahead of `predict`.
    Once adoption runs first, research holds the live row itself and there
    is no second copy to choose between.

    **Scoped to values this chunk actually claims**, and never touching a
    row the incoming set also identifies by `key`. A prediction the Pi
    wrote for an event research has not scored yet is not a collision and
    is left alone.

    A foreign key pointing at a deleted row raises rather than cascades,
    which is the failure worth having: `outcomes.prediction_id` is the
    forward log, and a sync silently deleting evidence is worse than a sync
    that stops.
    """
    if frame.empty or spec.column not in frame.columns:
        return 0
    claimed = frame[spec.column].dropna().unique().tolist()
    if not claimed:
        return 0
    keep = frame[list(key)].drop_duplicates()
    if len(key) != 1:
        raise ValueError(f"collision clearing needs a single-column key, got {key!r}")
    keep_ids = keep[key[0]].dropna().tolist()

    with target.begin() as conn:
        result = conn.execute(
            text(
                f'DELETE FROM "{table}" '  # noqa: S608 - fixed names
                f'WHERE "{spec.column}" = ANY(:claimed) AND "{key[0]}" <> ALL(:keep)'
            ),
            {"claimed": claimed, "keep": keep_ids or [None]},
        )
        return int(result.rowcount or 0)


def _null_inbound_remap_collisions(
    frame: pd.DataFrame,
    target: Engine,
    table: str,
    key: tuple[str, ...],
    spec: Remap,
) -> tuple[pd.DataFrame, int]:
    """Null `spec.column` on an incoming row whose remapped value already
    belongs to a DIFFERENT row already on the target.

    **The inbound counterpart of `_clear_remap_collisions`, and it must do
    the opposite thing.** Outbound (`run_sync`), research is the authority:
    a colliding serving row is disposable and `_clear_remap_collisions`
    deletes it. Inbound (`_pull_predictions`), the target IS research —
    the row already there may be exactly what `outcomes.prediction_id`
    references, and the incoming row is itself evidence (what the Pi
    scored live), not a copy. Neither row may be deleted or rewritten.

    So the incoming row's own link is dropped instead: it still adopts,
    with `event_id = NULL`, the same honest value `_apply_remap` already
    gives a key that resolves to no research event at all. This is what
    keeps `predictions_event_id`'s UNIQUE constraint from raising on a
    write that `copy_upsert`/`insert_new` would otherwise send straight
    through — reachable whenever research already holds its own row for
    the remapped event under a different `id`: a prior pull that failed
    non-fatally followed by that night's `predict`, or `weekly`'s refit,
    which calls `run_predict` with no pull ahead of it at all.

    **A row re-adopting itself is not a collision.** A repeated pull sends
    the same `id` again; when that `id` is the one already holding the
    `event_id` on the target, it is excluded — nulling it would undo a
    previous, correct adoption.
    """
    if frame.empty or spec.column not in frame.columns:
        return frame, 0
    claimed = frame[spec.column].dropna().unique().tolist()
    if not claimed:
        return frame, 0
    if len(key) != 1:
        raise ValueError(f"collision clearing needs a single-column key, got {key!r}")

    with target.connect() as conn:
        existing = pd.read_sql(
            text(
                f'SELECT "{key[0]}" AS __owner_id, "{spec.column}" AS __val '  # noqa: S608
                f'FROM "{table}" WHERE "{spec.column}" = ANY(:claimed)'
            ),
            conn,
            params={"claimed": claimed},  # type: ignore[arg-type]
        )
    if existing.empty:
        return frame, 0

    owners = existing.groupby("__val")["__owner_id"].agg(set)
    collides = [
        (not pd.isna(val)) and bool(owners.get(val, set()) - {own_id})
        for val, own_id in zip(frame[spec.column], frame[key[0]], strict=True)
    ]
    count = int(sum(collides))
    if count:
        frame = frame.copy()
        frame.loc[collides, spec.column] = None
    return frame, count


def _drop_surrogate_id(frame: pd.DataFrame, key: tuple[str, ...]) -> pd.DataFrame:
    """Drop a surrogate `id` the conflict key does not name, so the target
    assigns its own.

    **`events.id` is local to its store (ADR 163).** Since the poller began
    writing serving natively, serving mints ids from its own sequence while
    research mints from its own, so the two allocate independently out of
    one numeric range and the same integer names a different event on each
    side.

    **The 2026-09-01 nightly sync is what this closes:**

        duplicate key value violates unique constraint "events_pkey"
        DETAIL:  Key (id)=(61797210) already exists.

    On serving 61797210 was ADM `bb_upper_touch`, written by that day's
    poller; on research it was AA `stoch_oversold`, written by that night's
    `run_events`.

    **Excluding `id` from the `DO UPDATE SET` is not enough, and the first
    fix stopped there.** That removes the overwrite of an existing row's
    id, but the collision has two halves and the insert is the other one: a
    source row that is *new* to the target under the natural key raises no
    conflict at all, so `ON CONFLICT` never fires and the INSERT proceeds
    carrying the source's id straight into the target's primary key. The
    re-run failed identically on the same id, which is what proved it.

    So the column is not shipped. `events.id` on both stores defaults to
    `nextval('events_id_seq')`, so an INSERT that omits it gets the
    target's own id, and an UPDATE through the natural key leaves the
    existing id untouched. Neither statement carries an id across the
    boundary any more.

    **Nothing downstream depended on the ids matching.** `run_sync` already
    resolves `events` by its natural key `(config_hash, ticker,
    signal_date, signal_type, entry_kind)`, which is correct on both sides.
    The one cross-store reader was `signal_reports.event_id`, which
    `pull_live_records` now nulls on arrival for this reason -- and which
    ADR 150's sweep already nulled nightly, and `v_screen_live` stopped
    joining on in `d5e91a7c3b48`.

    **A table whose conflict key *is* `id` is untouched** --
    `signal_reports`, `predictions` and `positions` all key on it, and
    there the id is the identity the upsert resolves by rather than a
    value being carried along.
    """
    if "id" not in frame.columns or "id" in key:
        return frame
    return frame.drop(columns=["id"])


def _tables(cutoff: date, config_hash: str) -> tuple[SyncTable, ...]:
    """The serving subset, in foreign-key order.

    `cutoff` bounds the three large tables and nothing else. Reference data
    — tickers, the calendar, the universe evaluations — is small enough
    that trimming it would trade a rounding error in size for a class of
    bug where a chart resolves a date the calendar no longer knows.
    """
    return (
        SyncTable("tickers", "SELECT * FROM tickers", ("ticker",)),
        SyncTable("trading_days", "SELECT * FROM trading_days", ("d",)),
        SyncTable("market_days", "SELECT * FROM market_days", ("ts",)),
        # **Keyed on the hash too, and scoped to it.** `universe` gained
        # `config_hash` in d4a17c93f60b so ablation arms can coexist locally.
        # Serving reads exactly one generation, so shipping the others would
        # copy rows no query there can reach -- and shipping them under the
        # old two-column key would collapse them onto each other.
        SyncTable(
            "universe",
            "SELECT * FROM universe WHERE config_hash = :config_hash",
            ("ticker", "as_of", "config_hash"),
        ),
        # `serving_config` used to sit here, fifth. It is now **last** --
        # see the comment above it at the end of this tuple. Moving it is
        # the fix for a repeat outage, not a tidy-up.
        #
        # Scoped by trade-*or-watch*-universe membership rather than by
        # ticker list. **Widened 2026-09-03** (user's finding): this used
        # to read `u.in_trade` alone, on the stated theory that "the
        # deployed chart only ever draws a name the screener can show" --
        # false since before this line was written. The ticker page's own
        # search explicitly includes names outside the trade universe
        # (`screen.ts`'s `SEARCH_SQL`: "the ticker page is where you go to
        # look at a name you are not trading"), and ADR 149 says a watch
        # row gets "everything computed -- events, indicators, entries,
        # exits", which is a claim about what serving shows, not just what
        # research holds. `in_trade`-only sync made every watch name a
        # blank chart on the site regardless -- found via ELPC and ULS,
        # both real bars and indicators on research (671 and 600 daily
        # rows respectively), zero on serving, both `in_watch` and never
        # `in_trade`. ADR 168's `near_trade` route widened who qualifies
        # for `in_watch`, which is what surfaced this, not what caused it.
        #
        # `GREATEST(:cutoff, :bars_from)` -- the cutoff is the history
        # boundary and `bars_from` is the incremental one. Whichever is
        # later wins, so a NULL `bars_from` (an empty or unknown target)
        # degrades to the full cutoff pass rather than to nothing.
        SyncTable(
            "bars",
            "SELECT b.* FROM bars b WHERE b.interval = '1d' "
            "AND b.ts >= GREATEST(:cutoff, COALESCE(CAST(:bars_from AS date), :cutoff)) "
            "AND EXISTS (SELECT 1 FROM universe u WHERE u.ticker = b.ticker "
            "AND (u.in_trade OR u.in_watch) "
            "AND u.config_hash = :config_hash)",
            ("ticker", "ts", "interval"),
        ),
        SyncTable(
            "indicators",
            "SELECT i.* FROM indicators i WHERE i.interval = '1d' "
            "AND i.ts >= GREATEST(:cutoff, COALESCE(CAST(:indicators_from AS date), :cutoff)) "
            "AND EXISTS (SELECT 1 FROM universe u WHERE u.ticker = i.ticker "
            "AND (u.in_trade OR u.in_watch) "
            "AND u.config_hash = :config_hash)",
            ("ticker", "ts", "interval"),
        ),
        # **`runs`, narrowed to what the foreign key needs.** ADR 053 keeps
        # this table local and that is still right: 1,057 rows of job
        # params, most of them ingests the serving store has no use for.
        # But `events.run_id` references it, so shipping zero rows makes
        # every event insert fail the constraint.
        #
        # Six rows, measured — the backtests and event runs that produced
        # the synced window. That is what invariant 6 asks for: "every
        # generated row carries `run_id` and `git_sha`", which is only true
        # if the id resolves. Dropping the FK on serving instead would make
        # the two schemas differ, and ADR 053's "same migrations applied to
        # both" is the property `test_schema_drift.py` checks.
        #
        # Ordered before `events` because that is what a foreign key means.
        SyncTable(
            "runs",
            "SELECT * FROM runs WHERE run_id IN ("
            "  SELECT DISTINCT run_id FROM events"
            "   WHERE config_hash = :config_hash AND entry_kind IN ('next_open', 'touch')"
            "     AND signal_date >= :cutoff AND run_id IS NOT NULL)",
            ("run_id",),
        ),
        # **One config, two grains.** `v_screen` reads `next_open` and
        # `v_screen_live` reads `touch`; shipping one would leave a route
        # silently empty. The other 21 config hashes are the sweep and stay
        # local.
        SyncTable(
            "events",
            "SELECT * FROM events WHERE config_hash = :config_hash "
            "AND entry_kind IN ('next_open', 'touch') "
            "AND signal_date >= GREATEST(:cutoff, COALESCE(CAST(:events_from AS date), :cutoff))",
            ("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
        ),
        SyncTable(
            "signal_reports",
            "SELECT * FROM signal_reports WHERE "
            "fired_at >= GREATEST(:cutoff, COALESCE(CAST(:reports_from AS date), :cutoff))",
            ("id",),
        ),
        # Scoped to the config being served. Unfiltered, these shipped every
        # hash the research store had ever held, and `run_sync` never
        # deletes -- so each rebuild left another generation on serving
        # forever. That is what filled the 512 MB free tier on 2026-08-21,
        # where 90% of the events belonged to a hash nothing reads.
        #
        # Safe because the serving store reads exactly one hash:
        # `serving_config` pins it and `web/lib/db.ts` sets it on every
        # connection (ADR 115), so other generations are unreachable by any
        # query the site makes.
        SyncTable(
            "cell_stats",
            "SELECT * FROM cell_stats WHERE config_hash = :config_hash",
            ("cell_id", "config_hash"),
        ),
        SyncTable(
            "benchmarks",
            "SELECT * FROM benchmarks WHERE config_hash = :config_hash",
            ("id",),
        ),
        # **`event_id` is rewritten into serving's id space (ADR 191).**
        #
        # Copied verbatim it named a different event on the other side:
        # measured 2026-09-09, 7,403 of 20,200 pointed at nothing and
        # 3,035 pointed at the *wrong* event. `predictions` still keys on
        # `("id",)`, which is what lets `outcomes` below key on
        # `prediction_id` — only the reference is remapped, not the
        # identity.
        SyncTable(
            "predictions",
            "SELECT * FROM predictions",
            ("id",),
            remaps=(_PREDICTIONS_EVENT_REMAP,),
        ),
        # **After `predictions`, and that order is load-bearing.**
        # `outcomes.prediction_id` references it, so copying outcomes first
        # would fail the foreign key on a fresh serving store.
        #
        # Safe to key on `prediction_id` only because `predictions` syncs on
        # `("id",)` and therefore keeps its research ids on serving. Events
        # do not -- they key on a natural tuple, which is what made
        # `predictions.event_id` useless across the copy (migration
        # `e4b19c86d275`). Check that before adding any table that
        # references another by surrogate id.
        #
        # Carried so the reliability table can be computed where it is
        # displayed (ADR 182). The forward log is the only clean evidence
        # the project has, and a serving store that cannot see it can only
        # show the model's claims, never how they turned out.
        SyncTable("outcomes", "SELECT * FROM outcomes", ("prediction_id",)),
        SyncTable("positions", "SELECT * FROM positions", ("id",)),
        # **Last, and the position is the whole point.**
        #
        # This one row is what the site reads a generation *through*: ADR
        # 115 has `web/lib/db.ts` set `capitalscan.default_config_hash`
        # per connection from this table, and every serving view filters
        # on that setting. So the instant this row names a generation,
        # every page answers from that generation -- whether or not its
        # rows have arrived.
        #
        # It sat fifth in this tuple, ahead of `bars`, `indicators`,
        # `runs` and `events`. Across a `config_hash` change that made a
        # full sync blank the live site for its entire duration: measured
        # 2026-09-10, serving read `f183b0f5209a4677` while `events` still
        # held only the 5,413,295 rows of the previous generation. Nothing
        # errored. The home page returned 200 with no rows, twice in one
        # day, the second time for 26 minutes.
        #
        # Ordered last, the old generation stays live until the new one is
        # completely present, and the pin is the single write that cuts
        # over. `run_sync` never deletes, so the old rows are still there
        # to serve throughout -- that is what makes this safe rather than
        # merely later.
        #
        # Nothing upstream depends on it: every `sql` above runs against
        # **research**, and the `:config_hash` they filter on is the
        # parameter, not this row.
        SyncTable("serving_config", "SELECT * FROM serving_config", ("only_row",)),
    )


#: Rows per streamed read. 500k of `events`' 85 columns is a few hundred
#: MB, well inside any sane headroom, while keeping the number of `COPY`
#: round trips per table in the tens rather than the thousands.
_CHUNK_ROWS = 500_000

logger = logging.getLogger(__name__)


def serving_engine() -> Engine:
    """An engine against `DATABASE_URL_SERVING`.

    Raises rather than falling back to the research URL. Every other
    resolver in this codebase falls back with a warning, and that is right
    for a read: the worst case is a developer reading local data. Here the
    worst case is a *write* — a sync that silently upserted the serving
    subset back into the research store, on top of the rows it just read.
    """
    import os

    db_io._load_env()
    url = os.environ.get("DATABASE_URL_SERVING", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL_SERVING is not set. `cscan sync` writes to the cloud "
            "serving store (ADR 053) and will not fall back to the research "
            "database, because the fallback would write research rows onto "
            "themselves. Set it in .env.local."
        )
    return db_io.get_engine(url)


def _refuse_self_sync(source: Engine, target: Engine) -> None:
    """Raise if the serving URL resolves to the research database.

    `serving_engine()` already refuses to *fall back* to research. It cannot
    tell that an explicitly-set `DATABASE_URL_SERVING` happens to point
    there — and on a workstation that also hosts research, the natural typo
    is `localhost`, which is a valid URL to the wrong database.

    The failure is silent in the worst way. Every row upserts onto itself,
    every tick reports success, and the deployed site simply never changes.
    ADR 153 makes this a per-tick operation, so a wrong URL would look
    healthy 78 times a session.

    **Host and database together.** Either alone gives a false positive:
    research and serving legitimately share a database *name* on different
    hosts, and a single host legitimately carries both under different
    names.

    Loopback spellings are normalised because `localhost` and `127.0.0.1`
    are the same server and a guard that can be defeated by spelling is
    not a guard. Beyond that this stays deliberately literal — resolving
    DNS to compare addresses would put a network call in a boot path to
    catch a case that has never occurred.
    """
    loopback = {"localhost", "127.0.0.1", "::1", None}

    def _key(engine: Engine) -> tuple[str, str]:
        url = engine.url
        host = "localhost" if url.host in loopback else str(url.host)
        return host, str(url.database)

    if _key(source) == _key(target):
        host, database = _key(target)
        raise RuntimeError(
            f"DATABASE_URL_SERVING resolves to the research database "
            f"({host}/{database}). A sync writes the serving subset onto its "
            "own source: every row upserts onto itself, every run reports "
            "success, and the deployed site never changes. Point it at the "
            "serving host (ADR 153)."
        )


def cutoff_date(sp: ServingParams | None = None, today: date | None = None) -> date:
    """The oldest date the serving store carries.

    Calendar years rather than trading days: this bounds a *download*, not
    a measurement, and a reader asking for "three years" means the calendar
    kind. Nothing statistical is computed from the result.
    """
    sp = sp or ServingParams()
    return (today or date.today()) - timedelta(days=365 * sp.history_years)


@dataclass
class SyncReport:
    rows: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.rows.values())


# Days re-shipped below the target's own watermark. The sync upserts, so an
# overlap costs bandwidth and nothing else, and it absorbs the cases a bare
# watermark misses: a row corrected after the fact, a night that failed
# halfway, a bar restated by the vendor.
SYNC_OVERLAP_DAYS = 7


def _incremental_bounds(
    target: Engine, config_hash: str, overlap_days: int = SYNC_OVERLAP_DAYS
) -> dict[str, date | None]:
    """How far back each large table needs to be re-shipped.

    **Derived from the target, not from a fixed window.** A constant
    "last 7 days" is wrong exactly when it matters -- a Pi that has been
    off for a fortnight would get seven days of rows and a permanent hole,
    with no error. Reading the target's own newest row means the window is
    however far behind it actually is, plus the overlap.

    `None` means "no incremental bound": the table is empty on the target,
    or holds nothing for this config, so the full `cutoff` pass is the only
    correct answer. That is also what makes a first sync, a rebuilt serving
    store and a config change work without a flag.

    A failure to read is `None` too. Being slow is recoverable; guessing a
    watermark and shipping a subset is not.
    """
    queries = {
        "bars_from": "SELECT max(ts)::date FROM bars WHERE interval = '1d'",
        "indicators_from": "SELECT max(ts)::date FROM indicators WHERE interval = '1d'",
        "events_from": ("SELECT max(signal_date) FROM events WHERE config_hash = :config_hash"),
        "reports_from": "SELECT max(fired_at)::date FROM signal_reports",
    }
    out: dict[str, date | None] = {}
    for name, sql in queries.items():
        try:
            with target.connect() as conn:
                got = conn.execute(text(sql), {"config_hash": config_hash}).scalar_one_or_none()
        except SQLAlchemyError:
            logger.warning("could not read the %s watermark; falling back to a full pass", name)
            got = None
        out[name] = (got - timedelta(days=overlap_days)) if got is not None else None
    return out


# The poller's **durable** output, as opposed to its provisional output.
# `_sweep_provisional_poll_rows` deletes only `events` rows whose `run_id`
# begins `poll`, and explicitly preserves `signal_reports`; it never touches
# `poller_sessions`. These two are the record a past date's fired-at
# timestamps come from, and ADR 084 has Phase 6 reading
# `poller_sessions.coverage_pct` to tell "no coverage" from "no signals".
#
# **`runs` is here because `events.run_id` is a foreign key.** A poller run
# row is written on serving by `run_job` when the poller starts there, and
# research needs it before any later job can reference that run. Audited
# 2026-08-28: `poll.py` writes six tables, not the four first recorded --
# `signal_reports` goes through `db_io.append` rather than `upsert`, and
# `runs` through the `run_job` context manager rather than a direct call,
# so a grep for `upsert` finds neither.
_LIVE_DURABLE_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("runs", "started_at >= :since AND job = 'poll'", ("run_id",)),
    ("signal_reports", "fired_at >= :since", ("id",)),
    ("poller_sessions", "session_date >= :since", ("session_date",)),
)


def _pull_predictions(source: Engine, target: Engine) -> tuple[int, int, int, int, int, int]:
    """Adopt serving-born predictions into research (forward-log adoption).

    **Not a fourth `_LIVE_DURABLE_TABLES` entry.** The other three are
    scoped by date, because a poller session belongs to one day; a
    serving-born prediction's identity is which side of
    `ServingParams.serving_id_floor` minted it, so this reads a floor
    rather than a lookback window. It also needs the slot remap, which the
    generic loop in `pull_live_records` does not run. Both differences are
    permanent, not accidents of a first draft, so this stays its own
    function rather than bending the loop to fit it.

    **Selection is the floor, nothing else.** Serving mints at or above it
    and research stays below it (`_reset_sequences`), so `id >= floor` is
    exactly the Pi-born set once the one-time repair (component 4) has run.

    **Resolved on the debounce slot, not the label, since 2026-09-20.**
    `event_id` names a row in *serving's* id space, and the natural key
    that used to resolve it -- `(..., signal_type, entry_kind)` -- assumed
    the Pi and the end-of-day pass label a slot the same way. Measured that
    day: of 338 poller-written events since 2026-09-08, **zero** matched
    research's natural key, because `bb_lower_touch` (intraday, no close to
    confirm against) and `bull_close_below_lower` (end of day, ADR 194) can
    fill the same slot with different labels. `_apply_slot_remap` resolves
    `(config_hash, ticker, signal_date, side, entry_kind)` instead, and
    that function's own docstring carries the rest of the reasoning,
    including the two ways a miss happens: no event shares the slot
    (`no_slot`), or more than one does (`ambiguous`, never a pick -- ADR
    191). Either way `event_id` is NULL and the row still adopts, honestly
    unresolved rather than dropped.

    **A resolved `event_id` can also collide with a DIFFERENT research
    row, and that is checked before the write too.**
    `predictions_event_id` is UNIQUE on both stores, and research can
    already hold its own row for the resolved event -- a prior pull that
    failed non-fatally followed by that night's `predict`, or `weekly`'s
    refit, which calls `run_predict` with no pull ahead of it. Selection
    here is floor-scoped with no date bound, so an unhandled collision
    would raise on the very same row every night forever.
    `_null_inbound_remap_collisions` is `_clear_remap_collisions`'s inbound
    counterpart: it never deletes or rewrites the research row --
    `outcomes.prediction_id` may reference it -- it nulls the *incoming*
    row's `event_id` instead. Passing it `_PREDICTIONS_EVENT_REMAP` reuses
    only that `Remap`'s `.column` ("event_id"); the function never reads
    `.source_key`/`.target_key`, so this is not a reuse of the natural-key
    resolution retired above.

    **The slot is coarser than the natural key it replaced, and that cuts
    both ways.** The events-side ambiguity above is the well-known one;
    `_null_duplicate_slot_targets` guards the mirror case, found in review
    (2026-09-20): two DIFFERENT incoming predictions can resolve to the
    SAME research event, because `predictions_event_id` is unique per
    *serving* event and two serving events can share one research slot
    under different labels -- the same 15-slots-in-research phenomenon,
    seen from the source side. Both rows are nulled, never one kept, and
    counted under their own reason (`duplicate_target`), separate from
    `no_slot`/`ambiguous`.

    **This runs AFTER the collision check, not before -- order found
    wrong in review, round 2 (2026-09-20).** A row research already
    adopted on a prior night (`A`, `event_id = E`) is a legitimate,
    permanent owner of `E`; a later pull that reaches a second serving
    event in the same slot sends a NEW row `B` that also resolves to `E`.
    Deduping first would null BOTH `A` and `B` in the frame before the
    collision check ever saw `A` sitting on the target, so `unmapped`
    would report 2 when only `B` actually lands NULL -- research's link
    from `A` to `E` is untouched (`ON CONFLICT (id) DO NOTHING`, and ADR
    195 forbids rewriting it regardless). Running the collision check
    first excludes `A` as its own owner (the "re-adopting itself" rule),
    nulls `B` against `A`, and leaves the dedup step nothing left to do.

    **`unmapped` is read from the frame after every nulling step, not
    summed from the individual reasons.** Four different steps can null
    `event_id` before the write -- the slot lookup itself (`no_slot`,
    `ambiguous`), the inbound collision check (`collision`), and the
    intra-frame duplicate check (`duplicate_target`) -- and a caller that
    wants "how many rows landed with no event to point at" needs the true
    count, not a sum that silently drops whichever reason it forgot to add
    in. Task 3 (2026-09-20) returns all four reasons, not just the two the
    slot lookup produces -- the 2026-09-20 nightly run that printed "100
    adopted, 100 unmapped" told nobody which of the four this was, and
    `collision`/`duplicate_target` are exactly as diagnosable as `no_slot`/
    `ambiguous`: a caller deciding whether last night's adoption gap is a
    slot problem, a re-adoption problem, or a source-side dupe needs all
    four, not two. The test below asserts the four reasons sum to
    `unmapped` -- that equality is the check that a fifth, uncounted
    nulling step was never added silently.

    **Keyed on `id`, never on `event_id`.** A repeated pull must insert
    nothing. NULLs are distinct in a unique index, so keying on `event_id`
    would insert a fresh duplicate for every unmapped row on every nightly
    run -- `id` is the one column serving and research now agree names the
    same row (that agreement is the whole point of the floor split).

    **Written with `insert_new` (`DO NOTHING`), not `copy_upsert`
    (`DO UPDATE`).** ADR 195 chose insert-only for `predictions` precisely
    so a row already resolved by an outcome is never rewritten into a
    fitted number, and this adoption path is not exempt from that. One
    consequence: `adopted` on a *second* pull over the same rows is 0, not
    `len(frame)` -- `insert_new` counts rows actually inserted
    (`RETURNING`), so a repeat pull reporting zero is the write correctly
    doing nothing, not a sign the pull found nothing to adopt.

    Returns `(adopted, no_slot, ambiguous, collision, duplicate_target,
    unmapped)`.
    """
    floor = ServingParams().serving_id_floor
    frame = pd.read_sql(
        text("SELECT * FROM predictions WHERE id >= :floor"),
        source,
        params={"floor": floor},
    )
    if frame.empty:
        return 0, 0, 0, 0, 0, 0
    frame, no_slot, ambiguous = _apply_slot_remap(frame, target)
    # **Collision-against-the-target FIRST, intra-frame dedup SECOND --
    # order found wrong in review (2026-09-20 round 2).** A row already
    # adopted on a prior night (research's own A, `event_id = E`) is a
    # legitimate, permanent owner of E; a later pull that reaches a second
    # serving event in the same slot sends a NEW row B that also resolves
    # to E. Deduping first would null BOTH A and B in the frame -- A never
    # gets compared against the target at all, so `unmapped` would count 2
    # when only B actually lands NULL (A's `id` collides with `ON CONFLICT
    # (id) DO NOTHING`, so research's own link to E is untouched and
    # correctly still E). Running the collision check first excludes A as
    # its own owner (the "re-adopting itself" rule), nulls B against A,
    # and leaves dedup nothing left to do -- `unmapped` then equals the
    # one row that is actually NULL.
    frame, collided = _null_inbound_remap_collisions(
        frame, target, "predictions", ("id",), _PREDICTIONS_EVENT_REMAP
    )
    if collided:
        logger.warning(
            "%d adopted prediction(s) collided on event_id with a different research "
            "row; event_id left NULL rather than overwriting research's row",
            collided,
        )
    frame, duplicate_target = _null_duplicate_slot_targets(frame)
    if duplicate_target:
        logger.warning(
            "%d adopted prediction(s) shared a research event_id with another "
            "adopted prediction in the same pull; event_id left NULL on both "
            "rather than picking one",
            duplicate_target,
        )
    # The ground truth, not a sum of the individual reasons above -- see
    # the docstring. Computed after every nulling step has run. Also
    # asserted equal to the four reasons summed, in
    # test_pull_predictions.py -- that equality is what makes `unmapped`
    # trustworthy as "the true count" rather than a fifth number that
    # could silently drift from the sum of the other four.
    unmapped = int(frame["event_id"].isna().sum())
    adopted = db_io.insert_new(target, "predictions", frame, ["id"])
    return adopted, no_slot, ambiguous, collided, duplicate_target, unmapped


def pull_live_records(
    source: Engine | None = None,
    target: Engine | None = None,
    since: date | None = None,
    lookback_days: int = 7,
) -> dict[str, int]:
    """Copy the poller's durable rows **serving -> research** (ADR 158).

    The reverse of `run_sync`, and deliberately narrow.

    **Why it is needed.** With the poller writing serving directly, its two
    permanent tables are *born* there. Research is where analysis happens,
    so without this pull it quietly stops accumulating them -- and the gap
    is invisible until someone queries data that was never written, which
    is the worst shape a data defect can take.

    **Only the durable two, plus `predictions` (forward-log adoption,
    2026-09-19).** `events` rows from the poller are provisional and the
    nightly sweep removes them; pulling those back would resurrect exactly
    what nightly just judged unreliable. `bars_live` and `quotes_live` are
    per-tick scratch that research has no reader for. `predictions` is
    different: the Pi's live `predict --serving` pass scores the number a
    reader actually saw, and without adopting that row research's forward
    log scores whatever its own nightly refit says instead -- a different
    number for the same signal. See `_pull_predictions`.

    **Upsert, not replace.** Research may already hold rows for a date --
    from before the poller moved, or from a re-run. An insert would raise on
    the key and a delete-then-insert would lose anything the pull did not
    cover.

    Bounded to `lookback_days` because this runs nightly and the whole
    history is neither needed nor cheap; the overlap absorbs a night that
    failed. `since` overrides it for a manual catch-up. **`predictions` is
    the exception** -- floor-scoped rather than date-scoped, because a
    serving-born prediction's identity is which id range minted it, not
    when; see `_pull_predictions`.
    """
    source = source or serving_engine()
    target = target or db_io.get_engine()
    floor = since or (date.today() - timedelta(days=lookback_days))

    pulled: dict[str, int] = {}
    for name, predicate, key in _LIVE_DURABLE_TABLES:
        frame = pd.read_sql(
            text(f"SELECT * FROM {name} WHERE {predicate}"),  # noqa: S608 - fixed names
            source,
            params={"since": floor},
        )
        # **`event_id` does not survive the crossing (2026-09-01).** It
        # names a row in *serving's* id space, and since ADR 158 the two
        # stores mint `events.id` independently -- so the same integer is a
        # different event on each side. Copying it would produce a link
        # that resolves, points at the wrong ticker, and looks correct.
        #
        # Nulling is the honest value, not a loss: ADR 150's nightly sweep
        # already nulls this column by design for every provisional row,
        # and `v_screen_live` stopped joining on it in `d5e91a7c3b48`
        # precisely because the sweep made it unreliable. Nothing reads it.
        #
        # The report itself is self-contained -- `ticker`, `fired_at` and
        # `state_json` are all NOT NULL -- which is the same argument ADR
        # 150 makes for its own null.
        if name == "signal_reports" and "event_id" in frame.columns:
            frame = frame.assign(event_id=None)
        pulled[name] = db_io.copy_upsert(target, name, frame, list(key)) if not frame.empty else 0

    # **`predictions`, floor-scoped rather than date-scoped.** See
    # `_pull_predictions` for why it is not a fourth `_LIVE_DURABLE_TABLES`
    # entry. The three tables above keep their existing behaviour and
    # order; this runs after them and reports under its own key.
    #
    # **Wrapped so a raise here still reaches `_reset_sequences` below,
    # via `finally`, and is never swallowed.** This step used to run before
    # the reset with nothing between them, so a raise inside adoption --
    # the `predictions_event_id` collision this task's other fix removes,
    # or any future defect in the same spot -- skipped the reset along with
    # it and reinstated the sequence drift that failed the 2026-08-31 poll
    # (see the reset's own comment below). `pull_live_records`'s only
    # caller (`cli.nightly`) already wraps this whole function in a broad
    # `except Exception` that reports and continues, so letting the
    # exception propagate past this `finally` does not fail the night --
    # it is reported exactly as before, just with the reset still applied.
    try:
        # `_pull_predictions` returns `unmapped` as the ground truth --
        # every row whose `event_id` is NULL after every nulling step,
        # not a sum of `no_slot` and `ambiguous` alone. Two more nulling
        # reasons exist below the slot lookup (an intra-frame duplicate
        # target, an inbound collision with a row already on research) and
        # a sum that omitted them would under-report what an operator
        # reading this number actually needs to know: how many rows landed
        # with no event to point at, full stop. Task 3 (2026-09-20) reports
        # all four reasons under their own keys -- the 2026-09-20 nightly
        # printed "100 adopted, 100 unmapped" and named none of them.
        # `predictions_unmapped` stays the one key a caller reads for "how
        # many are unlinked"; it REPLACES what used to be the only unmapped
        # key, not a fifth number alongside the same total under a
        # different name.
        adopted, no_slot, ambiguous, collision, duplicate_target, unmapped = _pull_predictions(
            source, target
        )
        pulled["predictions"] = adopted
        if unmapped:
            logger.warning(
                "%d adopted predictions could not be matched to a research event "
                "(%d no matching slot, %d ambiguous slot, %d collided with an "
                "existing research row, %d duplicated another adopted row's target; "
                "event_id left NULL)",
                unmapped,
                no_slot,
                ambiguous,
                collision,
                duplicate_target,
            )
        pulled["predictions_unmapped_no_slot"] = no_slot
        pulled["predictions_unmapped_ambiguous"] = ambiguous
        pulled["predictions_unmapped_collision"] = collision
        pulled["predictions_unmapped_duplicate"] = duplicate_target
        pulled["predictions_unmapped"] = unmapped
    finally:
        # **Reset the target's sequences, mirroring `run_sync`
        # (2026-09-01).** Every row above was copied with its own id, and
        # an explicit-id INSERT does not advance a sequence -- so research
        # ends a pull holding rows its sequences have never seen. `run_sync`
        # has done this for serving since 2026-08-28; the reverse direction
        # was missed, and research's `signal_reports_id_seq` drifted 211
        # behind `max(id)` by 2026-09-01.
        #
        # That drift is what failed the 2026-08-31 fallback poll
        # (`signal_reports_pkey`, id 1832). `cscan poll` now refuses to
        # start against it, which is the right failure and still a failure
        # -- the operator has to `setval` by hand before polling. Fixing
        # the cause makes that guard a backstop rather than a gate.
        _reset_sequences(target, serving=False)
    return pulled


def run_sync(
    source: Engine | None = None,
    target: Engine | None = None,
    sp: ServingParams | None = None,
    today: date | None = None,
    config_hash: str | None = None,
    incremental: bool = False,
) -> SyncReport:
    """Copy the serving subset from `source` to `target`.

    Upserts rather than truncating. A truncate-and-reload would leave the
    served site empty for the duration of the load — brief, but the failure
    mode is that an interrupted sync leaves it empty *until the next one*,
    and a public page showing no signals is indistinguishable from a day
    when nothing fired.

    **Never deletes.** A row that leaves the subset — an event ageing past
    the cutoff — stays in the serving store until someone prunes it
    deliberately. That is the safe direction: the alternative is a bug in
    the cutoff arithmetic silently emptying the served history.

    **Applies `serving_id_floor` to `target` at both ends, not only the
    end.** `_reset_sequences(target, serving=True)` used to run once, after
    the copy. A store reflashed or `pg_restore`d below the floor sits that
    way until the *next* sync — and between a reflash and that sync the
    poller runs a whole live session, minting `predictions.id` below the
    floor the whole time. Those rows are then invisible to the next
    nightly's floor-scoped `_pull_predictions` (`id >= floor`), silently.
    Applying the floor here too, before anything else runs, closes that
    window: `greatest(max(id), floor)` is idempotent, so calling it twice
    in one run — once here, once at the end — costs nothing when the
    sequence is already at or above the floor.
    """
    source = source or db_io.get_engine()
    target = target or serving_engine()
    _refuse_self_sync(source, target)
    _reset_sequences(target, serving=True)
    cutoff = cutoff_date(sp, today)

    with run_job(source, "sync", {"cutoff": str(cutoff)}) as report:
        if config_hash is None:
            with source.connect() as conn:
                config_hash = conn.execute(
                    text("SELECT current_setting('capitalscan.default_config_hash', true)")
                ).scalar_one()

        # **Full by default; `incremental=True` is the nightly path.**
        # `cscan sync` means "copy the serving subset", and that is the
        # command you reach for after a rebuild, a reflash or a config
        # change -- it must not quietly ship a window.
        #
        # Nightly is the case that cannot afford it. A full pass shipped
        # 7,469,519 rows in 114.2 minutes on 2026-08-26 to deliver ~3,875
        # that had changed: a 1,900x amplification and two thirds of the
        # job. `cutoff` stopped bounding anything when
        # `ServingParams.history_years` went from 3 to 30, which was right
        # on its own and turned this into a whole-table copy.
        #
        # Even incremental, an empty table or an unseen `config_hash`
        # produces NULL bounds and falls back to the full `cutoff` pass, so
        # the fast path cannot leave a new serving store half-populated.
        bounds: dict[str, date | None] = (
            _incremental_bounds(target, str(config_hash))
            if incremental
            else {k: None for k in ("bars_from", "indicators_from", "events_from", "reports_from")}
        )
        logger.info(
            "sync mode=%s bounds=%s",
            "incremental" if incremental else "full",
            {k: str(v) for k, v in bounds.items()},
        )

        rows: dict[str, int] = {}

        # **One snapshot for every table (2026-08-25).** Each `read_sql`
        # against the Engine used to open its own connection, so a sync
        # that ran 1h45m read fourteen tables at fourteen different
        # moments. Measured on the Pi right after one: VOO and IBIT had
        # indicators, no bars, and no `in_trade` universe row -- `universe`
        # was copied at ~03:05 before ADR 154 made ETFs eligible, `bars` at
        # ~03:30 with an `EXISTS (... u.in_trade)` filter evaluated against
        # the *source*, and `indicators` at ~04:12 after ADR 154 landed.
        #
        # **That state never existed in research.** The copy manufactured
        # it, and a ticker with indicators but no bars is incoherent rather
        # than merely stale -- no downstream query can tell.
        #
        # REPEATABLE READ, not the default READ COMMITTED, which takes a
        # fresh snapshot per *statement*: sharing the connection alone
        # would fix nothing. The snapshot is established by the first
        # statement in the transaction and every later read sees it.
        # Postgres needs no locks for this -- readers never block writers
        # under MVCC -- so the cost is one long-lived connection.
        #
        # READ ONLY because a sync must never write to research; an
        # accidental write then fails at the database instead of
        # succeeding quietly. The target is written outside this
        # transaction, which is the point of holding it open.
        # **`stream_results` is what makes `chunksize` mean anything.**
        #
        # `pd.read_sql(chunksize=N)` chunks DataFrame *construction*, not
        # the fetch. Without a server-side cursor psycopg buffers the whole
        # result set client-side first, so a chunked read of 5.4M rows still
        # materialises 5.4M rows of Python tuples before pandas builds its
        # first chunk. Adding `chunksize` alone changed nothing on
        # 2026-09-09 -- the reaper killed the sync again, at the same point.
        #
        # `stream_results=True` gives psycopg a named cursor and the rows
        # arrive in batches. Together with `chunksize` the frame and the
        # fetch are both bounded; either one alone is not enough.
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as snapshot:
            snapshot.execute(text("SET TRANSACTION READ ONLY"))
            # **Applied after the SET, not on the connection.** With
            # `stream_results` set at connect time psycopg wraps *every*
            # statement in a named cursor, including this one, and
            # `DECLARE ... CURSOR FOR SET TRANSACTION READ ONLY` is a
            # syntax error. The read-only declaration has to land on a
            # plain cursor first.
            snapshot = snapshot.execution_options(stream_results=True)
            for table in _tables(cutoff, str(config_hash)):
                # pandas-stubs types `params` values as non-optional; a NULL
                # bound is exactly how "no incremental floor" is expressed and
                # psycopg binds it fine. The mismatch is the stub, not the call
                # -- same as `_read_corporate_actions`' list binding.
                # **Streamed, not materialised.** `events` is 5.4M rows of
                # 85 columns for the live generation, and reading it whole
                # was killed by the Windows low-memory reaper on 2026-09-09
                # -- twice, the second time with nothing else running. The
                # same read succeeded on 2026-09-08 because the commit
                # ceiling was 114 GB; Windows had since shrunk the pagefile
                # to put it at 67.7 GB.
                #
                # Raising the pagefile would hide it. A 5.4M-row frame does
                # not need tens of gigabytes of address space, and the next
                # generation's growth would find the new ceiling too.
                #
                # `_CHUNK_ROWS` at a time bounds the frame regardless of
                # table size. Chunks are large enough that the per-chunk
                # `COPY` round trip stays a rounding error against the
                # transfer itself.
                chunks = pd.read_sql(
                    text(table.sql),
                    snapshot,
                    params={"cutoff": cutoff, "config_hash": config_hash, **bounds},  # type: ignore[arg-type]
                    chunksize=_CHUNK_ROWS,
                )
                # **`COPY` into a staging table, not row dicts.** Profiled
                # during a full sync on 2026-08-26: the Pi was 76% idle (load
                # 1.11 of four cores, SD card 15% utilised) while this process
                # held 894 MB and 53.8% of one core. The constraint was
                # `to_dict("records")` building 7.4M Python dicts and SQLAlchemy
                # re-binding each one -- three full representations of every row
                # to move it between two databases.
                #
                # `_rows_per_batch` chunking is gone with it: `COPY` streams, so
                # there are no bind parameters to stay under `MAX_BIND_PARAMS`,
                # and the whole table lands in one transaction instead of one
                # per 1,000 rows.
                #
                # Writes the *target* from inside the source's read
                # transaction, deliberately: the snapshot must outlive
                # every read, and serving is a different database.
                # **One `COPY` per chunk, and the count accumulates.**
                # This reintroduces one transaction per chunk rather than
                # one per table, which the note above gave up deliberately
                # when it moved off row dicts. It is acceptable here for a
                # reason that did not apply then: every table syncs by
                # upsert on its own key, so a partially applied table is
                # already the failure mode of an interrupted sync and a
                # re-run converges. Bounded memory is worth that.
                copied = 0
                for chunk in chunks:
                    # **Remap before the write, per chunk.** The lookup is
                    # bounded by the keys in this chunk, so it stays small
                    # regardless of how large the table is.
                    for spec in table.remaps:
                        chunk = _apply_remap(chunk, target, spec)
                        if spec.unique_on_target:
                            dropped = _clear_remap_collisions(
                                chunk, target, table.name, table.key, spec
                            )
                            if dropped:
                                logger.info(
                                    "sync %s: cleared %d row(s) colliding on %s",
                                    table.name,
                                    dropped,
                                    spec.column,
                                )
                    copied += db_io.copy_upsert(
                        target,
                        table.name,
                        _drop_surrogate_id(chunk, table.key),
                        list(table.key),
                    )
                rows[table.name] = copied

        # Sequences, in the direction this function copies. See
        # `_reset_sequences` for why an explicit-id INSERT leaves them
        # behind and what it cost on both stores.
        _reset_sequences(target, serving=True)

        # Assigned *before* the pin, which can raise. Measured 2026-08-21:
        # a pin failure discarded the count and recorded rows_written = 0
        # for a sync that had committed ~100,000 rows.
        report.rows_written = sum(rows.values())

        # The pin is a convenience and must not fail a sync that worked.
        # ADR 115 moved the serving views onto the `serving_config` table,
        # written above; the GUC only helps a human in `psql`. Neon and
        # other managed Postgres refuse `ALTER DATABASE ... SET` to
        # non-superuser roles, and that is not a reason to report failure.
        #
        # Only this error is tolerated. `_pin_config_hash` raises
        # `ValueError` when an identifier does not look like one, and
        # swallowing everything here would hide that guard.
        # SQLAlchemy wraps the driver error, so the psycopg class arrives on
        # `.orig` rather than as the raised type. Catching
        # `InsufficientPrivilege` directly never fires.
        try:
            _pin_config_hash(target, str(config_hash))
        except ProgrammingError as err:
            if not isinstance(err.orig, InsufficientPrivilege):
                raise
            logger.warning(
                "could not pin capitalscan.default_config_hash on the serving "
                "database: the role lacks ALTER DATABASE. The sync itself "
                "succeeded and the serving views read the `serving_config` "
                "table (ADR 115), not this GUC, so nothing is broken. Set it "
                "by hand if you want `psql` sessions to default to %s.",
                config_hash,
            )
    return SyncReport(rows=rows)


def _pin_config_hash(target: Engine, config_hash: str) -> None:
    """Set `capitalscan.default_config_hash` on the serving database.

    **Without this a freshly synced store serves zero rows**, silently.
    Every screener and statistics view filters on
    `current_setting('capitalscan.default_config_hash', true)`, and the
    `true` makes it return NULL rather than raising when unset — so
    `config_hash = NULL` matches nothing and `/` renders an empty screener
    that looks exactly like a quiet day.

    ADR 100 records the GUC as a manual step: "must be re-pinned by hand —
    no migration records it." That is right for the research database,
    where a human decides when a new config becomes the default. It is
    wrong here: the sync has just copied rows *for one specific hash*, so
    it already knows the only answer that makes the copy readable, and
    leaving a human to remember it is leaving the serving store broken by
    default.

    `ALTER DATABASE` applies to new connections, not the one issuing it,
    which is why the caller verifies through a fresh connect rather than
    reading it back here.
    """
    with target.begin() as conn:
        database = conn.execute(text("SELECT current_database()")).scalar_one()
        # Identifier, so it cannot be a bound parameter. `database` comes
        # from the server itself and the hash is 16 hex characters from
        # `jobs.config.config_hash`; both are checked before interpolation.
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise ValueError(f"refusing to ALTER DATABASE {database!r}: unexpected name")
        if not re.fullmatch(r"[0-9a-f]{16}", config_hash):
            raise ValueError(f"refusing to pin {config_hash!r}: not a config hash")
        conn.execute(
            text(
                f'ALTER DATABASE "{database}" SET capitalscan.default_config_hash = :h'.replace(
                    ":h", f"'{config_hash}'"
                )
            )
        )


@dataclass(frozen=True)
class LiveWatermark:
    """How far the live sync has already pushed.

    `events` and `signal_reports` have no `updated_at`, but both `id`
    columns are bigint sequences and the poller never rewrites a key it has
    already fired (`_already_fired`). So a high-water id is an exact
    "everything below this is already on serving" marker, and each tick
    ships only what that tick produced rather than re-uploading the session
    so far.

    `bars_live` is excluded: it is keyed `(ticker, session_date)`, so a tick
    *overwrites* each ticker's row and the whole table has to ship each time
    (~450 rows). A watermark there would send each ticker once and never
    again, freezing the deployed candle at the day's first tick.

    `quotes_live` is keyed `(ticker, ts)` and therefore append-only, so it
    takes a clock watermark rather than an id one -- checked against
    `pg_constraint`, not assumed from its neighbour.
    """

    event_id: int = 0
    report_id: int = 0
    quote_ts: str | None = None


def _live_tables(chash: str, d: date, run_id: str, since: LiveWatermark) -> tuple[SyncTable, ...]:
    """The poller's own footprint, for one session, in foreign-key order.

    Deliberately not a subset of `_tables()`. That list is the *nightly*
    cut and excludes the live session for a reason its own docstring gives:
    a once-a-day copy of a five-minute table is a frozen price wearing a
    live label. This list exists because that reasoning is about the copy
    *frequency*, not about the table -- a per-tick copy puts the serving
    store in the same position the workstation is already in, with ADR
    131's 45-second client poll and ADR 134's session-hours guard applying
    unchanged because both live in the view and API layers.

    `poller_sessions` is the heartbeat and ships first, so a reader that
    sees no signals can still tell a quiet session from a dead poller.
    """
    return (
        SyncTable(
            "poller_sessions",
            "SELECT * FROM poller_sessions WHERE session_date = :d",
            ("session_date",),
        ),
        # `events.run_id` is a foreign key; `run_job` inserts this row on
        # entry with status 'running', so it resolves mid-session. The
        # status is corrected by the nightly's full sync.
        SyncTable("runs", "SELECT * FROM runs WHERE run_id = :run_id", ("run_id",)),
        SyncTable(
            "events",
            "SELECT * FROM events WHERE config_hash = :chash AND signal_date = :d "
            "AND entry_kind = 'touch' AND id > :since_event",
            ("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
        ),
        SyncTable(
            "signal_reports",
            "SELECT * FROM signal_reports WHERE fired_at >= :d AND id > :since_report",
            ("id",),
        ),
        SyncTable(
            "bars_live",
            "SELECT * FROM bars_live WHERE session_date = :d",
            ("ticker", "session_date"),
        ),
        SyncTable(
            "quotes_live",
            "SELECT * FROM quotes_live WHERE ts >= :d "
            "AND (CAST(:since_quote AS timestamptz) IS NULL "
            "OR ts > CAST(:since_quote AS timestamptz))",
            ("ticker", "ts"),
        ),
    )


def run_live_sync(
    chash: str,
    d: date,
    run_id: str,
    since: LiveWatermark | None = None,
    source: Engine | None = None,
    target: Engine | None = None,
) -> tuple[SyncReport, LiveWatermark]:
    """Push one poll tick's output to serving (ADR 153).

    **No `run_job` wrapper.** The poll's own run row already accounts for
    the work, and ~78 ticks a session would otherwise write 78 rows to
    `runs` describing a copy rather than a computation.

    **No `_pin_config_hash`.** The GUC does not change intraday and the pin
    is an `ALTER DATABASE` per tick for nothing.

    Idempotent: re-running a tick upserts the same rows to the same keys and
    the watermark advances past them, so a repeat is a no-op and a missed
    tick is repaired by the next one rather than needing a catch-up path.

    Returns the report and the advanced watermark. The caller holds the
    watermark across ticks; it deliberately does not live in this module,
    because a module-level one would leak between sessions in a process
    that polls two days in a row.
    """
    since = since or LiveWatermark()
    source = source or db_io.get_engine()
    target = target or serving_engine()
    _refuse_self_sync(source, target)

    params: dict[str, Any] = {
        "chash": chash,
        "d": d,
        "run_id": run_id,
        "since_event": since.event_id,
        "since_report": since.report_id,
        "since_quote": since.quote_ts,
    }

    rows: dict[str, int] = {}
    high = {"events": since.event_id, "signal_reports": since.report_id}
    quote_ts = since.quote_ts
    for table in _live_tables(chash, d, run_id, since):
        frame = pd.read_sql(text(table.sql), source, params=params)
        written = 0
        per_batch = _rows_per_batch(len(frame.columns))
        for start in range(0, len(frame), per_batch):
            batch = frame.iloc[start : start + per_batch]
            # Same surrogate-id rule as `run_sync` (ADR 163). This path is
            # the research poller's per-tick push, so it writes serving
            # from the other direction and can collide identically.
            written += db_io.upsert(
                target,
                table.name,
                _drop_surrogate_id(batch, table.key).to_dict("records"),
                list(table.key),
            )
        rows[table.name] = written
        if table.name in high and not frame.empty:
            high[table.name] = max(high[table.name], int(frame["id"].max()))
        if table.name == "quotes_live" and not frame.empty:
            quote_ts = str(frame["ts"].max())

    return SyncReport(rows), LiveWatermark(high["events"], high["signal_reports"], quote_ts)


def describe(sp: ServingParams | None = None, today: date | None = None) -> dict[str, Any]:
    """What a sync would carry, without doing it. Used by `--dry-run`."""
    return {"cutoff": cutoff_date(sp, today), "tables": [t.name for t in _tables(date.min, "")]}
