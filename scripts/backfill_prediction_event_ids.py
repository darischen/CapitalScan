"""One-time backfill: link the 100 adopted predictions' `event_id` on the
slot, not the label (task 4, slot-keyed-adoption plan, 2026-09-20).

**Why this exists.** The 2026-09-20 nightly adopted 100 of the Pi's
predictions into research (`jobs/sync.py::_pull_predictions`) before the
slot fix in this plan landed -- each one resolved `event_id` through the
natural key `(config_hash, ticker, as_of, signal_type, entry_kind)`, and
that key assumes the Pi and the end-of-day pass label a debounce slot the
same way. Measured that night, `bb_lower_touch` (intraday, no close to
confirm against) and `bull_close_below_lower` (end of day, ADR 194) never
agreed once, so all 100 rows adopted with `event_id = NULL`.

Earlier tasks in this plan fix the key for every pull from here forward --
`_apply_slot_remap` resolves on `(config_hash, ticker, signal_date, side,
entry_kind)` instead. **They do not repair the 100 rows already written.**
ADR 195 makes `predictions` insert-only: `_pull_predictions` writes with
`insert_new` (`ON CONFLICT (id) DO NOTHING`), so a repeat pull over the
same ids adopts nothing and touches nothing. Those rows are permanently
stuck with the `event_id` the old key gave them -- NULL -- unless something
else fills it in. This script is that something, run once.

**Expect it to link few of the 100, and to report most as `collision`
(whole-branch review, 2026-09-20).** That night's `predict` ran after the
pull, while these rows still carried NULL links, so wherever a slot
resolves research ALREADY owns the event through its own row R.
`_null_inbound_remap_collisions` then leaves the adopted row NULL rather
than touch R. For those signals the forward log keeps research's number,
not the one the reader saw live, and nothing recovers the live number: R
may already back an `outcomes` row, and ADR 195 forbids rewriting it. The
true split between linked, `collision` and the other three reasons is
known only from the dry run; nothing before it measured one. From the
first nightly after this branch merges, NEW signals are adopted before
`predict` runs, so research keeps the Pi's number. These 100 legacy rows
are the only casualties.

**What it does.** Reads every research `predictions` row that is
adopted (`id >= ServingParams.serving_id_floor`, the same test
`_pull_predictions` uses to mean "serving minted this id") and still
unresolved (`event_id IS NULL`), resolves `event_id` through the *same*
slot rule `_apply_slot_remap` applies nightly -- imported, not restated,
per this task's brief -- and writes back only the column that changed.

**Why the collision and intra-frame checks are needed here too, and are
the same two `_pull_predictions` runs.** This script writes `event_id`
onto rows that already exist in research, and `predictions_event_id` is
UNIQUE. Two ways that constraint can fire: (1) the slot a backfilled row
resolves to is already claimed by a different research row -- a row
adopted correctly on some other night, or written by `predict` --
(`_null_inbound_remap_collisions`, reused unchanged: `target` and `source`
are the same engine here, but that function only ever reads `target`,
so the equality is invisible to it); (2) two of the 100 rows resolve to the
*same* slot -- the mirror case `_null_duplicate_slot_targets` guards,
found in review of the nightly path and just as reachable here. Both are
reused directly rather than re-implemented, which is the whole point of
importing them: a second copy of either rule is the exact drift this
project has been burned by twice this week (see `jobs/sync.py`'s own
module docstring).

**Why collision-then-dedup, not the other order.** Same reasoning
`_pull_predictions` gives for its own ordering: a row this script is *not*
touching (already linked on a prior run, or linked some other way) is a
legitimate existing owner of its event and must be excluded before two
*backfilled* rows are ever compared against each other. Running dedup
first could null two rows against each other when only one of them
actually collides with anything real.

**The write touches exactly one column, and only where it is still
NULL.** `_BACKFILL_SQL` below is a single `UPDATE ... SET event_id = ...`
guarded by `WHERE p.id = m.id AND p.event_id IS NULL` -- the guard is not
redundant with scoping the read the same way: it is what makes a stale
read safe. If some other process links one of these rows between this
script's SELECT and its UPDATE, the guard makes that row's UPDATE a no-op
instead of an overwrite, and Postgres's own MVCC settles which write
happened first. No other column of any row -- linked, unlinked, above or
below the floor -- is ever named in the SET clause or touched by the
WHERE.

**The write is one statement inside one `engine.begin()`, so there is no
partial `--apply`.** Every row's `UPDATE` is part of a single transaction:
it commits whole (every row Postgres could match gets its `event_id`) or
not at all (a crash or an error before the `with` block exits rolls the
whole thing back, and nothing this script wrote survives). A rerun after
either outcome is safe for the same reason idempotency holds generally
(next paragraph) -- there is no in-between state on disk for a rerun to
have to reconcile.

**Idempotent by construction, not by a check.** A second run's read
(`event_id IS NULL`) already excludes every row the first run linked, so
there is nothing left to resolve and nothing left to write. Rerunning
after an interrupted run (a crash, a `KeyboardInterrupt`, before the
transaction committed) is safe for the same reason: an uncommitted write
never happened at all, so the next read sees exactly the rows it needs to
see, no more and no less.

**Must not run while `cscan nightly` (or any other `pull_live_records`
caller) is running against the same research database.** Nothing here
takes a lock, so a concurrent nightly pull writing `predictions.event_id`
for the same rows can race this script into `predictions_event_id`'s
UNIQUE index and one of the two transactions raises. The losing side rolls
back cleanly -- Postgres guarantees that much on its own -- and a rerun
after nightly finishes repairs whatever this script's own run did not
reach, so no data is lost or corrupted; it is simply wasted work and a
stack trace worth not triggering on purpose.

**Run it after a nightly has FINISHED and before the Pi's 06:45 PT session
starts.** Not only "not during nightly": the two readers select different
sets. This script reads research rows with `event_id IS NULL`; the nightly
pull reads every serving row at or above the floor, including rows not
adopted yet. An old NULL row here and a new serving row the next pull
adopts can resolve to one event, and whichever writes second is nulled as
a `collision`. In the window between a finished nightly and the next live
session, serving holds no row the last pull has not already adopted, so
the two sets cannot meet.

**Reports the same four reasons `pull_live_records` reports**, spelled the
same way (`no_slot`, `ambiguous`, `collision`, `duplicate_target`) --
Task 3 named exactly this drift risk: a caller reading last night's log
and this script's output must recognise the same words for the same
failure, not a second vocabulary for one rule.

**Every planned link and every unlinked row is printed individually, in
both modes.** Review (round 2) found that counts alone are not
auditable: the whole point of this repair is that a live label and an
end-of-day label can name the same slot differently (ADR 194), so a human
running this needs to see, per row, which prediction (`id`, `ticker`,
`as_of`, its own `signal_type`) links to which event (`id`, the EVENT's
`signal_type`) -- the two labels sit side by side on purpose. An unlinked
row is printed with the specific reason it did not resolve, not folded
into a count. At the ~100-row scale this repair operates at, printing
every row is cheap and a truncated list would hide exactly the case worth
seeing.

**The four-reasons-sum-to-unresolved check runs, and can abort the write,
before `--apply` touches the database.** Review (round 2) found the
original ordering checked this only inside the final report, after
`apply_updates` had already committed -- so a failed check meant the rows
were already written by the time anyone found out. `_verify_reason_
accounting` now runs directly after `resolve()`, before `apply_updates` is
ever called, and raises rather than returning a value a caller could
ignore.

    uv run python scripts/backfill_prediction_event_ids.py               # dry run
    uv run python scripts/backfill_prediction_event_ids.py --apply
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import ServingParams
from capitalscan.jobs import db_io
from capitalscan.jobs.sync import (
    _PREDICTIONS_EVENT_REMAP,
    _apply_slot_remap,
    _null_duplicate_slot_targets,
    _null_inbound_remap_collisions,
)

logger = logging.getLogger(__name__)

# See "The write touches exactly one column" above. `unnest` over two
# parallel arrays binds the whole (id -> event_id) mapping as two
# parameters regardless of row count, the same form `jobs/sync.py` and
# `scripts/repair_prediction_ids.py` already prefer for a bounded,
# dynamic-length list, rather than one bound pair of statement per row.
#
# The `event_id IS NULL` guard is load-aware, not decorative: it is the
# only thing standing between this script and overwriting a link some
# other writer set between the SELECT above and this UPDATE.
#
# **A template, so `scripts/verify_slot_adoption.py` can run the real
# statement against a `zz_` scratch table** (whole-branch review MINOR 7:
# `unnest` over a `bigint[]` cast had never run against real Postgres, the
# same gap that hid `insert_new`'s `rowcount` of -1). Only the table name
# is substituted, from a fixed identifier, never from a value.
# `_BACKFILL_SQL` is the production statement and what `apply_updates`
# sends by default.
_BACKFILL_SQL_TEMPLATE = """
UPDATE {table} p
   SET event_id = m.event_id
  FROM unnest(
      CAST(:ids AS bigint[]), CAST(:event_ids AS bigint[])
  ) AS m(id, event_id)
 WHERE p.id = m.id AND p.event_id IS NULL
"""
_BACKFILL_SQL = _BACKFILL_SQL_TEMPLATE.format(table="predictions")

#: The reason names, in the order `pull_live_records` reports them. Kept as
#: a tuple (not re-typed at each call site) so the report and the tests
#: agree on the four labels by construction.
REASONS: tuple[str, ...] = ("no_slot", "ambiguous", "collision", "duplicate_target")

#: The prediction-side columns every printed row (linked or unlinked)
#: carries, beyond `id`/`event_id` -- enough for a human to recognise the
#: slot without a second query.
_DETAIL_COLS: tuple[str, ...] = ("ticker", "as_of", "signal_type")


def read_unresolved(engine: Engine, floor: int) -> pd.DataFrame:
    """Adopted rows (`id >= floor`) still carrying a NULL `event_id`.

    Scoped to `event_id IS NULL` on the read, not only on the write, so a
    row a prior `--apply` already linked never enters the resolution frame
    at all -- the write's own guard would no-op on it regardless, but
    filtering here means the report describes only the rows this run
    actually looked at, which is what makes "a second run changes nothing"
    visible in the printed counts rather than only in the database.
    """
    return pd.read_sql(
        text(
            "SELECT * FROM predictions "  # noqa: S608 - fixed table name
            "WHERE id >= :floor AND event_id IS NULL"
        ),
        engine,
        params={"floor": floor},
    )


def _resolve_with_row_reasons(
    frame: pd.DataFrame, engine: Engine
) -> tuple[pd.DataFrame, dict[str, int], dict[int, str]]:
    """`resolve()`'s work, plus a per-row reason for every row that ends up
    unresolved. Kept separate from `resolve()` so that function's return
    shape -- already relied on by tests and by the report's aggregate
    counts -- does not have to change to get row-level detail.

    **`collision` and `duplicate_target` need no extra lookup**: a row
    that went from having a resolved `event_id` to NULL at exactly one of
    those two steps is, by construction, a row that step nulled -- read
    directly off the pipeline's own before/after state, not re-derived.

    **`no_slot` vs `ambiguous` are conflated in `_apply_slot_remap`'s own
    aggregate return**, and distinguishing them per row without
    re-implementing the matching rule (forbidden -- see the module
    docstring) means calling that SAME function again, once per
    still-unresolved row. This is safe, not a second copy of the rule:
    the slot lookup is a pure function of one row's own key against
    `events`, independent of which other rows share the batch (ambiguity
    is a property of how many events share that ONE key, never of how
    many predictions were resolved alongside it), so a single-row replay
    reproduces exactly the classification the batched call already made
    for that row. At the ~100-row scale this script operates at, the
    handful of extra round trips this costs is not worth avoiding at the
    price of a second matching rule.
    """
    if frame.empty:
        return frame, dict.fromkeys(REASONS, 0), {}

    entry = frame.copy()
    frame, no_slot, ambiguous = _apply_slot_remap(frame, engine)
    slot_null_ids = set(frame.loc[frame["event_id"].isna(), "id"])

    frame, collision = _null_inbound_remap_collisions(
        frame, engine, "predictions", ("id",), _PREDICTIONS_EVENT_REMAP
    )
    collision_null_ids = set(frame.loc[frame["event_id"].isna(), "id"]) - slot_null_ids

    frame, duplicate_target = _null_duplicate_slot_targets(frame)
    duplicate_null_ids = (
        set(frame.loc[frame["event_id"].isna(), "id"]) - slot_null_ids - collision_null_ids
    )

    reasons = {
        "no_slot": no_slot,
        "ambiguous": ambiguous,
        "collision": collision,
        "duplicate_target": duplicate_target,
    }

    row_reasons: dict[int, str] = dict.fromkeys(collision_null_ids, "collision")
    row_reasons.update(dict.fromkeys(duplicate_null_ids, "duplicate_target"))

    for row_id in slot_null_ids:
        single = entry.loc[entry["id"] == row_id]
        _, row_no_slot, _row_ambiguous = _apply_slot_remap(single, engine)
        row_reasons[row_id] = "no_slot" if row_no_slot else "ambiguous"

    return frame, reasons, row_reasons


def resolve(frame: pd.DataFrame, engine: Engine) -> tuple[pd.DataFrame, dict[str, int]]:
    """Resolve `event_id` for every row in `frame` against `engine`,
    reusing `_pull_predictions`'s own three-step sequence in its own order
    (slot lookup, then the collision check, then the intra-frame dedup --
    see the module docstring for why that order matters).

    `engine` plays both roles `_pull_predictions` splits into `source` and
    `target`: these rows already live in research, so the only "pull"
    happening is filling in one column already-resident rows arrived
    without.

    Returns the frame with `event_id` resolved where it could be, and the
    four reason counts, keyed by `REASONS`. See `_resolve_with_row_reasons`
    for the per-row detail this wraps.
    """
    frame, reasons, _row_reasons = _resolve_with_row_reasons(frame, engine)
    return frame, reasons


def plan_updates(frame: pd.DataFrame) -> pd.DataFrame:
    """The rows that resolved to a real event, as `(id, event_id)` plus
    enough of the prediction's own identity (`ticker`, `as_of`,
    `signal_type`) to print an auditable row -- not just the pair the
    write needs. Rows still NULL after `resolve` are not writes at all --
    there is nothing to set them to, and the row already reads NULL, so
    touching them again would violate "only where it changes" for no
    gain.
    """
    cols = ("id", "event_id", *_DETAIL_COLS)
    if frame.empty:
        return pd.DataFrame(columns=list(cols))
    linked = frame.loc[frame["event_id"].notna(), list(cols)]
    return linked.reset_index(drop=True)


def unlinked_rows(frame: pd.DataFrame, row_reasons: dict[int, str]) -> pd.DataFrame:
    """Every row that did NOT resolve, with its own reason attached --
    the unlinked counterpart of `plan_updates`, for the same auditability
    review asked for.
    """
    cols = ("id", *_DETAIL_COLS)
    if frame.empty:
        return pd.DataFrame(columns=[*cols, "reason"])
    unresolved = frame.loc[frame["event_id"].isna(), list(cols)].copy()
    unresolved["reason"] = unresolved["id"].map(row_reasons)
    return unresolved.reset_index(drop=True)


def event_labels(engine: Engine, event_ids: list[int]) -> pd.DataFrame:
    """`id`/`signal_type` for the resolved events, so the printed plan can
    show the event's own label next to the prediction's. Showing both is
    the point of this repair: the whole reason `event_id` resolves
    through the slot rather than the label is that the two can disagree
    (ADR 194), and a human running this should see that, per row, not
    just infer it from a count.
    """
    if not event_ids:
        return pd.DataFrame(columns=["event_id", "event_signal_type"])
    return pd.read_sql(
        text(
            "SELECT id AS event_id, signal_type AS event_signal_type "  # noqa: S608
            "FROM events WHERE id = ANY(:ids)"
        ),
        engine,
        params={"ids": [int(v) for v in event_ids]},  # type: ignore[arg-type]
    )


def apply_updates(engine: Engine, updates: pd.DataFrame, *, table: str = "predictions") -> int:
    """Write `updates` with `_BACKFILL_SQL`, in one transaction. Returns the
    number of rows Postgres actually updated -- which can be less than
    `len(updates)` if the `event_id IS NULL` guard caught a row linked by
    something else since the read (see the module docstring). One
    statement inside one `engine.begin()`: this either commits every row
    it touches or none of them (see the module docstring's "no partial
    --apply" note) -- there is no in-between state.

    `table` defaults to `"predictions"` and `main` never passes it. It is
    keyword-only and exists for `scripts/verify_slot_adoption.py`, the same
    seam `_apply_slot_remap`'s `table=` is.
    """
    if updates.empty:
        return 0
    sql = _BACKFILL_SQL_TEMPLATE.format(table=table)
    ids = [int(v) for v in updates["id"].tolist()]
    event_ids = [int(v) for v in updates["event_id"].tolist()]
    with engine.begin() as conn:
        result = conn.execute(text(sql), {"ids": ids, "event_ids": event_ids})
        return int(result.rowcount or 0)


def _verify_reason_accounting(*, total: int, planned: int, unresolved: int) -> None:
    """The same ground-truth check `test_pull_predictions.py` runs on the
    nightly path: the four reasons must sum to "not resolved", or a fifth,
    uncounted nulling step crept in somewhere. Raises rather than
    returning a value a caller could ignore, and is called BEFORE
    `apply_updates` (review, round 2: the original ordering let a failed
    check fire only after the write had already committed).
    """
    if planned + unresolved != total:
        raise RuntimeError(
            f"resolved ({planned}) + unresolved ({unresolved}) != examined ({total}); "
            "a nulling step is uncounted, investigate before trusting this report "
            "-- nothing has been written"
        )


def _print_plan(updates: pd.DataFrame, labels: pd.DataFrame, unlinked: pd.DataFrame) -> None:
    """Print every planned link and every unlinked row individually, in
    both `--dry-run` and `--apply` -- the audit trail review asked for.
    """
    print(f"\nresolved links ({len(updates)}):")
    if updates.empty:
        print("  (none)")
    else:
        detailed = updates.merge(labels, on="event_id", how="left")
        for row in detailed.itertuples(index=False):
            print(
                f"  id={row.id} {row.ticker} {row.as_of} {row.signal_type} "
                f"-> event {int(row.event_id)} {row.event_signal_type}"  # type: ignore[arg-type]
            )

    print(f"\nunlinked rows ({len(unlinked)}):")
    if unlinked.empty:
        print("  (none)")
    else:
        for row in unlinked.itertuples(index=False):
            print(
                f"  id={row.id} {row.ticker} {row.as_of} {row.signal_type} "
                f"-> UNLINKED ({row.reason})"
            )


def _print_summary(
    *, total: int, planned: int, reasons: dict[str, int], applied: int | None
) -> None:
    print(f"\nadopted rows examined (id >= floor, event_id IS NULL): {total}")
    print(f"resolved to exactly one event: {planned}")
    for name in REASONS:
        print(f"  {name}: {reasons[name]}")
    unresolved = sum(reasons[name] for name in REASONS)
    print(f"unresolved total: {unresolved}")
    if applied is None:
        print("\ndry run, nothing written. Re-run with --apply to perform it.")
    else:
        print(f"\nrows updated: {applied}")
        if applied != planned:
            logger.warning(
                "planned %d update(s) but Postgres reported %d -- some row's "
                "event_id was set by something else between the read and the "
                "write; its link is left as whatever that write set, untouched "
                "by this run",
                planned,
                applied,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the backfill. Default is a dry run: print the plan, write nothing.",
    )
    args = parser.parse_args(argv)

    floor = ServingParams().serving_id_floor
    engine = db_io.get_engine()

    frame = read_unresolved(engine, floor)
    total = len(frame)
    frame, reasons, row_reasons = _resolve_with_row_reasons(frame, engine)
    updates = plan_updates(frame)
    planned = len(updates)
    unresolved = sum(reasons[name] for name in REASONS)

    # Must raise, and must run, before any write -- see the module
    # docstring and `_verify_reason_accounting`'s own docstring.
    _verify_reason_accounting(total=total, planned=planned, unresolved=unresolved)

    labels = event_labels(engine, updates["event_id"].tolist())
    unlinked = unlinked_rows(frame, row_reasons)
    _print_plan(updates, labels, unlinked)

    applied = apply_updates(engine, updates) if args.apply else None
    _print_summary(total=total, planned=planned, reasons=reasons, applied=applied)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
