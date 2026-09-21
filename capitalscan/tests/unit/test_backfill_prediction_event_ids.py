"""`scripts/backfill_prediction_event_ids.py` -- the one-time repair for the
100 rows the 2026-09-20 nightly adopted with `event_id = NULL` (task 4,
slot-keyed-adoption plan).

**What this file is not.** `_apply_slot_remap`, `_null_inbound_remap_
collisions` and `_null_duplicate_slot_targets` are `jobs/sync.py`'s own
functions, already tested there (`test_slot_remap.py`,
`test_pull_predictions.py`). This file tests the script's own concerns
layered on top: it reads only NULL, adopted rows; it writes exactly one
column, guarded by `event_id IS NULL`; a second run changes nothing; and
an ambiguous slot stays NULL rather than picking one.

No real database anywhere in this file -- a single fake engine stands in
for research (source and target are the same store here, unlike the
nightly pull), following the pattern `test_pull_predictions.py` and
`test_sync_remap.py` established for `pd.read_sql` and `Engine.execute`.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd
import pytest
from sqlalchemy import Engine

from capitalscan.core.config import ServingParams
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH
from scripts import backfill_prediction_event_ids as backfill

CHASH = DEFAULT_CONFIG_HASH
FLOOR = ServingParams().serving_id_floor

# Same order `_apply_slot_remap`'s WHERE binds them in: `side` is skipped
# because it is not a database column (computed from `signal_type`).
_SLOT_SELECT_COLS = ("config_hash", "ticker", "signal_date", "entry_kind")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeEngine:
    """One store standing in for research. `predictions` and `events` are
    held as frames and mutated in place by `execute`, so a second read
    through the same fixture sees whatever the first `--apply` wrote --
    that mutation is what makes the idempotency test meaningful rather
    than a tautology.
    """

    def __init__(self, predictions: pd.DataFrame, events: pd.DataFrame) -> None:
        self.predictions = predictions
        self.events = events
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def connect(self):
        return self

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, stmt: object, params: dict[str, Any] | None = None) -> _FakeResult:
        params = params or {}
        self.executed.append((str(stmt), params))
        ids = params.get("ids", [])
        event_ids = params.get("event_ids", [])
        updated = 0
        for row_id, event_id in zip(ids, event_ids, strict=True):
            mask = (self.predictions["id"] == row_id) & self.predictions["event_id"].isna()
            if mask.any():
                self.predictions.loc[mask, "event_id"] = event_id
                updated += int(mask.sum())
        return _FakeResult(updated)


def _predictions(rows: list[tuple]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows,
        columns=["id", "event_id", "config_hash", "ticker", "as_of", "signal_type", "entry_kind"],
    )
    frame["event_id"] = frame["event_id"].astype("Float64")
    return frame


def _event_id_of(predictions: pd.DataFrame, row_id: int) -> Any:
    return predictions.loc[predictions["id"] == row_id, "event_id"].iloc[0]


def _events(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind"],
    )


@pytest.fixture
def patched_read_sql(monkeypatch):
    """Route the script's own outer read AND `jobs.sync`'s internal reads
    (the slot lookup, the collision check) through the one fake engine's
    frames.

    **One function, not two.** `scripts.backfill_prediction_event_ids` and
    `capitalscan.jobs.sync` both do `import pandas as pd`, so `backfill.pd`
    and `sync_job.pd` are the *same* module object -- patching
    `read_sql` through either name replaces the one attribute both call.
    Dispatch on which params the caller bound instead: the outer read
    binds `:floor`, the slot lookup binds `:v0`..., the collision check
    binds `:claimed`.
    """

    def fake_read_sql(sql, con, params=None):
        params = params or {}
        if "floor" in params:
            frame = con.predictions
            mask = (frame["id"] >= params["floor"]) & frame["event_id"].isna()
            return frame[mask].reset_index(drop=True)

        if "claimed" in params:
            frame = con.predictions
            if frame.empty:
                return pd.DataFrame(columns=["__owner_id", "__val"])
            matched = frame[frame["event_id"].isin(params["claimed"])]
            return matched.rename(columns={"id": "__owner_id", "event_id": "__val"})[
                ["__owner_id", "__val"]
            ].reset_index(drop=True)

        if "ids" in params:
            # `event_labels`'s own lookup -- events by id, for the printed
            # plan's side-by-side labels.
            frame = con.events
            matched = frame[frame["id"].isin(params["ids"])]
            return matched.rename(columns={"id": "event_id", "signal_type": "event_signal_type"})[
                ["event_id", "event_signal_type"]
            ].reset_index(drop=True)

        frame = con.events
        for i, col in enumerate(_SLOT_SELECT_COLS):
            frame = frame[frame[col].isin(params[f"v{i}"])]
        cols = ["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind"]
        return frame[cols].reset_index(drop=True)

    monkeypatch.setattr(backfill.pd, "read_sql", fake_read_sql)


def _run(engine: _FakeEngine, *, apply: bool) -> tuple[int, int, dict[str, int], int | None]:
    """Drive the script's own pipeline (not `main`, so the engine stays a
    fake rather than going through `db_io.get_engine`)."""
    frame = backfill.read_unresolved(cast(Engine, engine), FLOOR)
    total = len(frame)
    frame, reasons = backfill.resolve(frame, cast(Engine, engine))
    updates = backfill.plan_updates(frame)
    planned = len(updates)
    applied = backfill.apply_updates(cast(Engine, engine), updates) if apply else None
    return total, planned, reasons, applied


class TestOnlyNullRowsAreTouched:
    def test_a_row_that_already_has_an_event_id_is_never_examined(self, patched_read_sql):
        rows = _predictions(
            [
                (FLOOR, 7, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch"),
                (FLOOR + 1, None, CHASH, "BB", "2026-09-18", "bb_lower_touch", "touch"),
            ]
        )
        events = _events([(9, CHASH, "BB", "2026-09-18", "bb_lower_touch", "touch")])
        engine = _FakeEngine(rows, events)
        total, planned, reasons, applied = _run(engine, apply=True)
        assert total == 1, "only the NULL row should reach the resolution frame"
        assert planned == 1
        assert applied == 1
        # The already-linked row's own event_id (7) must be untouched.
        assert int(_event_id_of(engine.predictions, FLOOR)) == 7
        assert int(_event_id_of(engine.predictions, FLOOR + 1)) == 9

    def test_a_below_floor_row_is_never_examined(self, patched_read_sql):
        rows = _predictions(
            [(FLOOR - 1, None, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")]
        )
        engine = _FakeEngine(rows, _events([]))
        total, planned, reasons, applied = _run(engine, apply=True)
        assert (total, planned, applied) == (0, 0, 0)
        msg = "a research-born row must not be touched"
        assert pd.isna(engine.predictions.loc[0, "event_id"]), msg


class TestSecondRunChangesNothing:
    def test_a_repeat_run_writes_nothing_and_reports_nothing(self, patched_read_sql):
        rows = _predictions(
            [(FLOOR, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)

        first = _run(engine, apply=True)
        assert first[1] == 1 and first[3] == 1
        assert int(engine.predictions.loc[0, "event_id"]) == 42

        before = engine.predictions.copy()
        second = _run(engine, apply=True)
        total, planned, reasons, applied = second
        assert (total, planned, applied) == (0, 0, 0)
        assert all(v == 0 for v in reasons.values())
        pd.testing.assert_frame_equal(engine.predictions, before)

    def test_dry_run_writes_nothing_at_all(self, patched_read_sql):
        rows = _predictions(
            [(FLOOR, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)
        total, planned, reasons, applied = _run(engine, apply=False)
        assert (total, planned, applied) == (1, 1, None)
        assert engine.executed == [], "a dry run must never call execute()"
        assert pd.isna(engine.predictions.loc[0, "event_id"]), "dry run must not write"


class TestAnAmbiguousSlotIsLeftNull:
    def test_two_events_in_one_slot_leave_event_id_null(self, patched_read_sql):
        rows = _predictions([(FLOOR, None, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch")])
        # Two events share the slot under different labels (ADR 194) --
        # both LONG_SIGNALS, so `side` agrees and the slot still resolves
        # to two candidates.
        events = _events(
            [
                (1, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
                (2, CHASH, "ZZ", "2026-09-19", "bull_close_below_lower", "touch"),
            ]
        )
        engine = _FakeEngine(rows, events)
        total, planned, reasons, applied = _run(engine, apply=True)
        assert total == 1
        assert planned == 0
        assert applied == 0
        assert reasons["ambiguous"] == 1
        assert reasons["no_slot"] == 0
        msg = "an ambiguous slot must never pick one"
        assert pd.isna(engine.predictions.loc[0, "event_id"]), msg


class TestACollisionOrIntraFrameDuplicateIsNulledNotRaised:
    def test_a_collision_with_an_already_linked_row_is_left_null(self, patched_read_sql):
        # Research already holds its own row for event 42 (a prior correct
        # adoption, or `predict`'s own write). The backfilled row resolves
        # to the same event and must not steal the link.
        rows = _predictions(
            [
                (FLOOR, 42, CHASH, "AA", "2026-09-01", "bb_upper_touch", "touch"),
                (FLOOR + 1, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch"),
            ]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)
        total, planned, reasons, applied = _run(engine, apply=True)
        assert total == 1, "the already-linked row must not even be read"
        assert planned == 0
        assert applied == 0
        assert reasons["collision"] == 1
        assert pd.isna(_event_id_of(engine.predictions, FLOOR + 1))
        # The pre-existing owner's link is completely untouched.
        assert int(_event_id_of(engine.predictions, FLOOR)) == 42

    def test_two_backfilled_rows_resolving_to_one_event_are_both_left_null(self, patched_read_sql):
        rows = _predictions(
            [
                (FLOOR, None, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
                (FLOOR + 1, None, CHASH, "ZZ", "2026-09-19", "bull_close_below_lower", "touch"),
            ]
        )
        # One event covers the slot for BOTH labels below (both resolve on
        # `side`, entry_kind, ticker, signal_date -- the label difference
        # is exactly what the slot key is blind to).
        events = _events([(9, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch")])
        engine = _FakeEngine(rows, events)
        total, planned, reasons, applied = _run(engine, apply=True)
        assert total == 2
        assert planned == 0
        assert applied == 0
        assert reasons["duplicate_target"] == 2
        assert engine.predictions["event_id"].isna().all()


class TestTheGeneratedUpdateNamesExactlyOneColumn:
    def test_set_clause_names_exactly_one_column(self):
        sql = backfill._BACKFILL_SQL
        set_clause = sql.split("SET", 1)[1].split("FROM", 1)[0]
        columns = [part.strip().split("=")[0].strip() for part in set_clause.split(",")]
        assert columns == ["event_id"], f"SET clause must name exactly one column, got {columns}"

    def test_where_clause_carries_the_null_guard(self):
        sql = backfill._BACKFILL_SQL
        where_clause = sql.split("WHERE", 1)[1]
        assert "event_id IS NULL" in where_clause

    def test_apply_updates_sends_exactly_this_sql(self, patched_read_sql):
        rows = _predictions(
            [(FLOOR, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)
        _run(engine, apply=True)
        assert len(engine.executed) == 1
        sent_sql, _ = engine.executed[0]
        assert sent_sql.strip() == backfill._BACKFILL_SQL.strip()


class TestTheTableSeamIsAdditiveOnly:
    """`apply_updates(..., table=)` exists so `scripts/verify_slot_adoption.py`
    can run the real UPDATE against a `zz_` scratch table (whole-branch
    review MINOR 7). The production default must stay `predictions`.
    """

    def test_the_production_statement_updates_predictions(self):
        assert backfill._BACKFILL_SQL.strip().startswith("UPDATE predictions p")

    def test_the_default_sends_the_production_statement(self):
        engine = _FakeEngine(_predictions([]), _events([]))
        updates = pd.DataFrame({"id": [FLOOR], "event_id": [42]})
        backfill.apply_updates(cast(Engine, engine), updates)
        sent_sql, params = engine.executed[0]
        assert sent_sql.strip() == backfill._BACKFILL_SQL.strip()
        assert params == {"ids": [FLOOR], "event_ids": [42]}

    def test_a_scratch_table_changes_only_the_table_name(self):
        engine = _FakeEngine(_predictions([]), _events([]))
        updates = pd.DataFrame({"id": [FLOOR], "event_id": [42]})
        backfill.apply_updates(cast(Engine, engine), updates, table="zz_scratch")
        sent_sql, _ = engine.executed[0]
        assert sent_sql.strip() == backfill._BACKFILL_SQL.strip().replace(
            "UPDATE predictions p", "UPDATE zz_scratch p"
        )

    def test_the_table_cannot_be_passed_positionally(self):
        engine = _FakeEngine(_predictions([]), _events([]))
        updates = pd.DataFrame({"id": [FLOOR], "event_id": [42]})
        with pytest.raises(TypeError):
            backfill.apply_updates(cast(Engine, engine), updates, "zz_scratch")


class TestReportedReasonsSumToUnresolved:
    def test_planned_plus_unresolved_equals_total(self, patched_read_sql):
        rows = _predictions(
            [
                (FLOOR, None, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch"),
                (FLOOR + 1, None, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        events = _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        engine = _FakeEngine(rows, events)
        total, planned, reasons, applied = _run(engine, apply=False)
        unresolved = sum(reasons.values())
        assert planned + unresolved == total
        assert reasons["no_slot"] == 1


class TestPerRowReasonAttribution:
    """Review (round 2), IMPORTANT 2: an unlinked row must carry its OWN
    reason, not just contribute to an aggregate count."""

    def test_no_slot_and_ambiguous_are_distinguished_per_row(self, patched_read_sql):
        rows = _predictions(
            [
                (FLOOR, None, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch"),
                (FLOOR + 1, None, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        # AA's slot matches nothing; ZZ's slot matches two events under
        # different labels (ADR 194).
        events = _events(
            [
                (10, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
                (11, CHASH, "ZZ", "2026-09-19", "bull_close_below_lower", "touch"),
            ]
        )
        engine = _FakeEngine(rows, events)
        frame = backfill.read_unresolved(cast(Engine, engine), FLOOR)
        frame, reasons, row_reasons = backfill._resolve_with_row_reasons(
            frame, cast(Engine, engine)
        )
        assert row_reasons[FLOOR] == "no_slot"
        assert row_reasons[FLOOR + 1] == "ambiguous"
        assert reasons["no_slot"] == 1
        assert reasons["ambiguous"] == 1

    def test_collision_and_duplicate_target_are_attributed_per_row(self, patched_read_sql):
        rows = _predictions(
            [
                (FLOOR, 42, CHASH, "AA", "2026-09-01", "bb_upper_touch", "touch"),
                (FLOOR + 1, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch"),
                (FLOOR + 2, None, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
                (FLOOR + 3, None, CHASH, "ZZ", "2026-09-19", "bull_close_below_lower", "touch"),
            ]
        )
        events = _events(
            [
                (42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch"),
                (9, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        engine = _FakeEngine(rows, events)
        frame = backfill.read_unresolved(cast(Engine, engine), FLOOR)
        frame, reasons, row_reasons = backfill._resolve_with_row_reasons(
            frame, cast(Engine, engine)
        )
        assert row_reasons[FLOOR + 1] == "collision"
        assert row_reasons[FLOOR + 2] == "duplicate_target"
        assert row_reasons[FLOOR + 3] == "duplicate_target"


class TestUnlinkedRowsReportsReason:
    def test_each_unlinked_row_carries_ticker_asof_signal_type_and_reason(self, patched_read_sql):
        rows = _predictions([(FLOOR, None, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch")])
        engine = _FakeEngine(rows, _events([]))
        frame = backfill.read_unresolved(cast(Engine, engine), FLOOR)
        frame, _reasons, row_reasons = backfill._resolve_with_row_reasons(
            frame, cast(Engine, engine)
        )
        unlinked = backfill.unlinked_rows(frame, row_reasons)
        assert len(unlinked) == 1
        row = unlinked.iloc[0]
        assert row["id"] == FLOOR
        assert row["ticker"] == "ZZ"
        assert row["as_of"] == "2026-09-19"
        assert row["signal_type"] == "bb_lower_touch"
        assert row["reason"] == "no_slot"


class TestEventLabels:
    def test_the_events_own_signal_type_is_returned_alongside_the_prediction_labeled_set(
        self, patched_read_sql
    ):
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(_predictions([]), events)
        labels = backfill.event_labels(cast(Engine, engine), [42])
        assert list(labels["event_id"]) == [42]
        assert list(labels["event_signal_type"]) == ["bear_close_above_upper"]

    def test_an_empty_id_list_needs_no_query(self, patched_read_sql):
        engine = _FakeEngine(_predictions([]), _events([]))
        labels = backfill.event_labels(cast(Engine, engine), [])
        assert labels.empty


class TestTheAccountingCheckAbortsBeforeAnyWrite:
    """Review (round 2), IMPORTANT 1: a failed reason-accounting check must
    fire before `apply_updates`, not only inside the final report after
    the write already committed."""

    def test_a_mismatch_raises_and_nothing_is_written(self, patched_read_sql, monkeypatch):
        rows = _predictions(
            [(FLOOR, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)

        # Force the accounting to disagree, the way an uncounted fifth
        # nulling step would: the real `_resolve_with_row_reasons` cannot
        # produce this on its own, so the test breaks the seam directly.
        real_resolve = backfill._resolve_with_row_reasons

        def lying_resolve(frame, target):
            resolved_frame, reasons, row_reasons = real_resolve(frame, target)
            reasons = dict(reasons)
            reasons["no_slot"] += 1  # inflate the unresolved count by one
            return resolved_frame, reasons, row_reasons

        monkeypatch.setattr(backfill, "_resolve_with_row_reasons", lying_resolve)
        monkeypatch.setattr(backfill.db_io, "get_engine", lambda: engine)

        with pytest.raises(RuntimeError, match="a nulling step is uncounted"):
            backfill.main(["--apply"])

        assert engine.executed == [], "the write must never run once the check fails"
        assert pd.isna(engine.predictions.loc[0, "event_id"]), "nothing may be written on failure"


class TestDryRunIsTheDefault:
    """MINOR, review round 2: nothing previously drove `main()` itself, so
    flipping the `--apply` default in `argparse` would have passed every
    other test in this file."""

    def test_main_with_no_args_performs_no_writes(self, patched_read_sql, monkeypatch, capsys):
        rows = _predictions(
            [(FLOOR, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)
        monkeypatch.setattr(backfill.db_io, "get_engine", lambda: engine)

        exit_code = backfill.main([])

        assert exit_code == 0
        assert engine.executed == [], "the default invocation must never call execute()"
        assert pd.isna(engine.predictions.loc[0, "event_id"]), "a dry run must not write"
        out = capsys.readouterr().out
        assert "dry run, nothing written" in out
        # The audit trail (IMPORTANT 2) must appear even without --apply.
        assert "-> event 42 bear_close_above_upper" in out

    def test_main_with_apply_writes(self, patched_read_sql, monkeypatch):
        rows = _predictions(
            [(FLOOR, None, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        events = _events([(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")])
        engine = _FakeEngine(rows, events)
        monkeypatch.setattr(backfill.db_io, "get_engine", lambda: engine)

        exit_code = backfill.main(["--apply"])

        assert exit_code == 0
        assert len(engine.executed) == 1
        assert int(engine.predictions.loc[0, "event_id"]) == 42
