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

**Scope.** One scratch table, `zz_verify_slot_events`, created and dropped
by this script alone. It is never read from or written to outside this
script's own `finally`. Predictions are never written to a table at all --
`_apply_slot_remap` and `_apply_remap` both take the predictions side as an
in-memory `pandas.DataFrame`, exactly as `_pull_predictions` already holds
it (already pulled from serving before either remap runs), so building it
in Python rather than seeding a second scratch table is not a shortcut, it
is the same shape production uses. This script never opens a connection to
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

    uv run python scripts/verify_slot_adoption.py
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.jobs import db_io
from capitalscan.jobs.sync import Remap, _apply_remap, _apply_slot_remap

EVENTS_TABLE = "zz_verify_slot_events"

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
        conn.execute(text(f"DROP TABLE IF EXISTS {EVENTS_TABLE}"))


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

    finally:
        _drop(engine)
        print(f"dropped {EVENTS_TABLE}")

    if ok:
        print(
            "PASS: natural-key resolution finds nothing across a label mismatch, "
            "_apply_slot_remap links it without relabelling, and a two-event slot "
            "stays NULL rather than picking one"
        )
        return 0
    print("FAIL: see above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
