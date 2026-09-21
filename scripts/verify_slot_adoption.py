"""Real-Postgres check that `_apply_slot_remap` links a Pi-labelled
prediction onto a differently-labelled research event, and that the old
natural-key resolution (`_apply_remap` on `_PREDICTIONS_EVENT_REMAP`'s key)
does not (task 5, slot-keyed adoption plan).

**Why this exists.** `capitalscan/tests/unit/test_slot_remap.py` covers
every rule `_apply_slot_remap` enforces -- label mismatch links, no event
nulls, two events null -- against a `_FakeEngine` that answers
`pandas.read_sql` from an in-memory frame. `scripts/verify_id_floor.py`
exists precisely because a fake missed a real defect on this project once
(`db_io.insert_new`'s `rowcount` came back -1 against real Postgres, never
against the fake). This script is that same different-instrument check for
slot-keyed adoption: it calls the production functions, unmodified, against
a real server, on tables nothing else reads.

**Why `_apply_slot_remap` needed a one-line change to run here at all.** As
written for `_pull_predictions`, it selected from a hardcoded `"events"`.
Pointing it at a scratch table without editing `sync.py` was impossible --
unlike `_apply_remap`, whose `Remap.table` was already a field, there was
no table-name-generic way in. The alternative was to hand-copy its SQL
against a `zz_` table, which is the exact mistake `verify_id_floor.py`'s own
docstring warns against: proving the shape of the join is not proving the
function is wired correctly. So `_apply_slot_remap` gained a keyword-only
`table: str = "events"` parameter -- the same shape `predictions_max_id_sql`
already has for `_reset_sequences`, for the same reason. The default path
is untouched: every real caller (`_pull_predictions`,
`scripts/backfill_prediction_event_ids.py`) calls this positionally with
two arguments and never supplies `table`, so it still reads `"events"`.
`test_slot_remap.py::TestTheTableParameterIsAdditiveOnly` pins the emitted
SQL for both the default and a non-default call.

**Scope.** Three scratch tables, `zz_verify_slot_events`,
`zz_verify_slot_predictions` and `zz_verify_slot_serving_events`, created
by this script and dropped in its own `finally`. Nothing else reads or
writes them. Steps 1-4 hold the predictions side in memory, as
`_pull_predictions` does; steps 5 and 6 need a predictions TABLE because
the statements under test (the outbound LEFT JOIN, the backfill UPDATE)
read and write one. Both "stores" live in the one local research database
under `zz_` names. This script never opens a connection to
the serving store, the Pi, or `wivie` -- `db_io.get_engine()` resolves
`DATABASE_URL_RESEARCH` only.

**What this does and does not cover.** This proves the SQL `_apply_remap`
and `_apply_slot_remap` actually run, against a real server, resolves the
way the design doc says: the natural key finds nothing for a label
mismatch, the slot key finds it, and a two-event slot stays NULL rather
than picking one. It does not re-prove every rule already covered by
`test_slot_remap.py` against the fake (side derivation raising on an
unknown `signal_type`, entry_kind separating grains, and so on) -- that
coverage is unit-level by design and does not need a real server.

**Two more real-server checks, added by the whole-branch review
(2026-09-20).** Step 5 runs the outbound predictions SELECT from
`_tables()` (the LEFT JOIN onto research `events`) and
`_PREDICTIONS_OUTBOUND_REMAP` against scratch tables, and shows an adopted
row landing on the end-of-day event where the old own-label remap put it
on the poller's provisional event (CRITICAL 1). Step 6 runs the backfill's
real `UPDATE ... FROM unnest(CAST(:ids AS bigint[]), ...)` through
`apply_updates(..., table=)` and checks Postgres's own `rowcount` (MINOR
7). Neither statement had run against a real server before; the last time
a fake stood in for that, `insert_new`'s `rowcount` was -1 in production
and correct in every test. The SELECT is production's string with its two
table names substituted, and the substitution is asserted to hit exactly
once each, so a reworded query fails here rather than silently testing a
different one.

    uv run python scripts/verify_slot_adoption.py
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

# Run as `python scripts/verify_slot_adoption.py`, so the repo root is not on
# `sys.path` and `scripts.backfill_prediction_event_ids` would not import.
# Resolved from this file, never an absolute path (CLAUDE.md, Platform).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.sync import (  # noqa: E402
    _PREDICTIONS_EVENT_KEY_HELPERS,
    _PREDICTIONS_OUTBOUND_REMAP,
    Remap,
    _apply_remap,
    _apply_slot_remap,
    _tables,
)
from scripts import backfill_prediction_event_ids as backfill  # noqa: E402

EVENTS_TABLE = "zz_verify_slot_events"
#: Research's `predictions`, for the outbound SELECT and the backfill UPDATE.
PREDICTIONS_TABLE = "zz_verify_slot_predictions"
#: Serving's `events`, for the outbound remap.
SERVING_EVENTS_TABLE = "zz_verify_slot_serving_events"

_PREDICTIONS_DDL = """
CREATE TABLE {table} (
    id bigint PRIMARY KEY,
    event_id bigint,
    config_hash text NOT NULL,
    ticker text NOT NULL,
    as_of date NOT NULL,
    signal_type text NOT NULL,
    entry_kind text NOT NULL
);
"""

_DDL = """
CREATE TABLE {table} (
    id bigint PRIMARY KEY,
    config_hash text NOT NULL,
    ticker text NOT NULL,
    signal_date date NOT NULL,
    signal_type text NOT NULL,
    entry_kind text NOT NULL
);
"""

# The natural-key `Remap` production uses outbound (`_PREDICTIONS_EVENT_
# REMAP` in `jobs/sync.py`), rebuilt here pointed at the scratch table --
# `Remap.table` is already a field, so this needs no change to `sync.py`.
# Same columns, same order; only `table` differs from production's.
_NATURAL_KEY_REMAP = Remap(
    column="event_id",
    table=EVENTS_TABLE,
    source_key=("config_hash", "ticker", "as_of", "signal_type", "entry_kind"),
    target_key=("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
)


def _create(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_DDL.format(table=EVENTS_TABLE)))


def _drop(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in (EVENTS_TABLE, PREDICTIONS_TABLE, SERVING_EVENTS_TABLE):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


def _outbound_select_sql() -> str:
    """Production's predictions SELECT, pointed at the scratch tables.

    Each substitution must hit exactly once. A query reworded so that it
    no longer contains these fragments fails here instead of running a
    statement production never sends.
    """
    sql = next(t.sql for t in _tables(date(2026, 9, 20), "h1") if t.name == "predictions")
    for old in ("FROM predictions p", "JOIN events e"):
        if sql.count(old) != 1:
            raise RuntimeError(f"expected {old!r} once in the predictions SELECT: {sql}")
    return sql.replace("FROM predictions p", f"FROM {PREDICTIONS_TABLE} p").replace(
        "JOIN events e", f"JOIN {EVENTS_TABLE} e"
    )


def _step5_outbound(engine: Engine) -> bool:
    """CRITICAL 1: an adopted prediction resolves on serving to the
    end-of-day event, not the poller's provisional one."""
    ok = True
    with engine.begin() as conn:
        conn.execute(text(_PREDICTIONS_DDL.format(table=PREDICTIONS_TABLE)))
        conn.execute(text(_DDL.format(table=SERVING_EVENTS_TABLE)))
        # Serving: E' (end-of-day copy of research 81001), P (the Pi's
        # provisional event in the same slot), and AA's copy of research 1.
        conn.execute(
            text(
                f"INSERT INTO {SERVING_EVENTS_TABLE} "  # noqa: S608
                "(id, config_hash, ticker, signal_date, signal_type, entry_kind) VALUES "
                "(95001, 'h1', 'USB', :usb, 'bull_close_below_lower', 'touch'), "
                "(95002, 'h1', 'USB', :usb, 'bb_lower_touch', 'touch'), "
                "(95003, 'h1', 'AA', :aa, 'bb_lower_touch', 'touch')"
            ),
            {"usb": date(2026, 9, 18), "aa": date(2026, 9, 9)},
        )
        # Research: 500 adopted (live label, linked to 81001 on the slot),
        # 501 research-written (label matches its event 1), 502 unlinked.
        conn.execute(
            text(
                f"INSERT INTO {PREDICTIONS_TABLE} "  # noqa: S608
                "(id, event_id, config_hash, ticker, as_of, signal_type, entry_kind) VALUES "
                "(500, 81001, 'h1', 'USB', :usb, 'bb_lower_touch', 'touch'), "
                "(501, 1, 'h1', 'AA', :aa, 'bb_lower_touch', 'touch'), "
                "(502, NULL, 'h1', 'USB', :usb, 'bb_lower_touch', 'touch')"
            ),
            {"usb": date(2026, 9, 18), "aa": date(2026, 9, 9)},
        )

    frame = pd.read_sql(text(_outbound_select_sql()), engine)
    if len(frame) != 3:
        ok = False
        print(f"[5] FAIL: the LEFT JOIN returned {len(frame)} rows for 3 predictions")
    new = _apply_remap(
        frame, engine, dataclasses.replace(_PREDICTIONS_OUTBOUND_REMAP, table=SERVING_EVENTS_TABLE)
    ).set_index("id")
    old = _apply_remap(
        frame, engine, dataclasses.replace(_NATURAL_KEY_REMAP, table=SERVING_EVENTS_TABLE)
    ).set_index("id")
    got = {i: new.loc[i, "event_id"] for i in (500, 501, 502)}
    print(
        f"[5] outbound: adopted 500 -> {got[500]!r} (own-label remap: "
        f"{old.loc[500, 'event_id']!r}), research-written 501 -> {got[501]!r} "
        f"(own-label: {old.loc[501, 'event_id']!r}), unlinked 502 -> {got[502]!r}"
    )
    if got[500] != 95001:
        ok = False
        print(f"[5] FAIL: adopted row resolved to {got[500]!r}, expected E' 95001")
    if old.loc[500, "event_id"] != 95002:
        ok = False
        print("[5] FAIL: control -- the own-label remap should land on P 95002")
    if got[501] != old.loc[501, "event_id"] or got[501] != 95003:
        ok = False
        print("[5] FAIL: a research-written row resolved differently from today")
    if pd.notna(got[502]):
        ok = False
        print(f"[5] FAIL: a NULL research event_id resolved to {got[502]!r}")
    written = new.reset_index().drop(columns=list(_PREDICTIONS_EVENT_KEY_HELPERS))
    with engine.connect() as conn:
        table_cols = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t ORDER BY ordinal_position"
                ),
                {"t": PREDICTIONS_TABLE},
            )
        ]
    if list(written.columns) != table_cols:
        ok = False
        print(f"[5] FAIL: written columns {list(written.columns)} != table {table_cols}")
    else:
        print(f"    helper columns dropped; written columns match {PREDICTIONS_TABLE}")
    return ok


def _step6_backfill(engine: Engine) -> bool:
    """MINOR 7: the backfill's real UPDATE, and Postgres's real rowcount."""
    ok = True
    # 502 is NULL (the backfill's target); 501 is already linked to 1, so
    # the `event_id IS NULL` guard must refuse to overwrite it.
    updates = pd.DataFrame({"id": [502, 501], "event_id": [81001, 99]})
    applied = backfill.apply_updates(engine, updates, table=PREDICTIONS_TABLE)
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(f"SELECT id, event_id FROM {PREDICTIONS_TABLE} WHERE id IN (501, 502)")  # noqa: S608
            ).all()
        )
    print(f"[6] backfill UPDATE: rowcount={applied}, 502 -> {rows[502]!r}, 501 -> {rows[501]!r}")
    if applied != 1:
        ok = False
        print(f"[6] FAIL: expected rowcount 1 (the guard skips 501), got {applied}")
    if rows[502] != 81001:
        ok = False
        print(f"[6] FAIL: 502 should be linked to 81001, got {rows[502]!r}")
    if rows[501] != 1:
        ok = False
        print(f"[6] FAIL: the NULL guard let 501 be overwritten to {rows[501]!r}")
    return ok


def _count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(text(f"SELECT count(*) FROM {EVENTS_TABLE}")).scalar_one()  # noqa: S608
        )


def _predictions(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["id", "event_id", "config_hash", "ticker", "as_of", "signal_type", "entry_kind"],
    )


def main() -> int:
    engine = db_io.get_engine()
    ok = True

    try:
        # --- Step 1: seed one event under the end-of-day label, and one
        # Pi-labelled prediction in the same debounce slot -- the USB
        # 2026-09-18 shape the design doc measures. ---
        _create(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {EVENTS_TABLE} "  # noqa: S608
                    "(id, config_hash, ticker, signal_date, signal_type, entry_kind) VALUES "
                    "(81001, 'h1', 'USB', :d, 'bull_close_below_lower', 'touch')"
                ),
                {"d": date(2026, 9, 18)},
            )
        n_events = _count(engine)
        predictions = _predictions(
            [(500, None, "h1", "USB", date(2026, 9, 18), "bb_lower_touch", "touch")]
        )
        print(
            f"[1] seeded {n_events} research event ('bull_close_below_lower') and "
            f"{len(predictions)} live prediction ('bb_lower_touch') in the same slot"
        )
        if n_events != 1:
            ok = False
            print(f"[1] FAIL: expected 1 seeded event, got {n_events}")

        # --- Step 2: the old natural-key resolution -- `signal_type` is
        # part of the key -- finds nothing. The 2026-09-20 failure,
        # reproduced against a real server: 0 of 338 that night. ---
        natural = _apply_remap(predictions, engine, _NATURAL_KEY_REMAP)
        natural_linked = natural["event_id"].notna().sum()
        print(f"[2] natural-key resolution: {natural_linked} of {len(natural)} row(s) linked")
        if natural_linked != 0:
            ok = False
            print(
                f"[2] FAIL: natural-key resolution linked {natural_linked} row(s); "
                "expected 0 -- the label mismatch should defeat it"
            )

        # --- Step 3: the slot resolution links it, and the prediction's
        # own `signal_type` is unchanged (adoption relabels nothing). ---
        slotted, no_slot, ambiguous = _apply_slot_remap(predictions, engine, table=EVENTS_TABLE)
        linked_id = slotted.loc[0, "event_id"]
        linked_label = slotted.loc[0, "signal_type"]
        print(
            f"[3] slot resolution: event_id={linked_id!r}, "
            f"signal_type stayed {linked_label!r}, no_slot={no_slot}, ambiguous={ambiguous}"
        )
        if linked_id != 81001:
            ok = False
            print(f"[3] FAIL: expected event_id 81001, got {linked_id!r}")
        if linked_label != "bb_lower_touch":
            ok = False
            print(f"[3] FAIL: adoption relabelled the row to {linked_label!r}")
        if (no_slot, ambiguous) != (0, 0):
            ok = False
            print(
                f"[3] FAIL: expected (no_slot, ambiguous) == (0, 0), got ({no_slot}, {ambiguous})"
            )

        # --- Step 4: a second event lands in ONE slot (same ticker, date,
        # side, entry_kind as the first, different label) and the
        # prediction resolving to that slot stays NULL rather than
        # picking one (ADR 191). ---
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {EVENTS_TABLE} "  # noqa: S608
                    "(id, config_hash, ticker, signal_date, signal_type, entry_kind) VALUES "
                    "(1, 'h1', 'AA', :d, 'bb_lower_touch', 'touch'), "
                    "(2, 'h1', 'AA', :d, 'confluence_low', 'touch')"
                ),
                {"d": date(2026, 9, 9)},
            )
        n_events_after = _count(engine)
        print(f"    added a second slot holding two events; {n_events_after} event(s) total")
        ambiguous_predictions = _predictions(
            [(600, None, "h1", "AA", date(2026, 9, 9), "bb_lower_touch", "touch")]
        )
        combined = pd.concat([predictions, ambiguous_predictions], ignore_index=True)
        out, no_slot2, ambiguous2 = _apply_slot_remap(combined, engine, table=EVENTS_TABLE)
        ambiguous_row = out.loc[out["id"] == 600].iloc[0]
        clean_row = out.loc[out["id"] == 500].iloc[0]
        print(
            f"[4] two-event slot: prediction id=600 event_id={ambiguous_row['event_id']!r}, "
            f"no_slot={no_slot2}, ambiguous={ambiguous2}; "
            f"unrelated prediction id=500 still resolves to event_id={clean_row['event_id']!r}"
        )
        if pd.notna(ambiguous_row["event_id"]):
            ok = False
            print(
                f"[4] FAIL: the two-event slot resolved to event_id="
                f"{ambiguous_row['event_id']!r} instead of staying NULL"
            )
        if ambiguous2 != 1:
            ok = False
            print(f"[4] FAIL: expected ambiguous count 1, got {ambiguous2}")
        if clean_row["event_id"] != 81001:
            ok = False
            print(
                "[4] FAIL: the unrelated single-match slot was disturbed by the ambiguous "
                f"one: event_id={clean_row['event_id']!r}"
            )

        ok = _step5_outbound(engine) and ok
        ok = _step6_backfill(engine) and ok

    finally:
        _drop(engine)
        print(f"dropped {EVENTS_TABLE}, {PREDICTIONS_TABLE}, {SERVING_EVENTS_TABLE}")

    if ok:
        print(
            "PASS: natural-key resolution finds nothing across a label mismatch, "
            "_apply_slot_remap links it without relabelling, a two-event slot "
            "stays NULL rather than picking one, the outbound sync lands an adopted "
            "row on the end-of-day event, and the backfill UPDATE runs with a "
            "correct rowcount"
        )
        return 0
    print("FAIL: see above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
