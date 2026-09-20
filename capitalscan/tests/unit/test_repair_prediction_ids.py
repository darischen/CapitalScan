"""`scripts/repair_prediction_ids.py` -- the one-time repair of serving-born
`predictions.id`s (component 4, forward-log adoption, 2026-09-19).

**Why this exists.** Before `ServingParams.serving_id_floor`, serving and
research minted `predictions.id` from their own sequence over the same
numeric range: 100 rows exist only on serving (scored live by the Pi, never
seen by research) and 3 ids name a different signal on each side. This
script moves the 100 off research's range and, as a side effect, clears the
3 collisions -- they belong to serving-born rows.

No real database anywhere in this file, per the task's constraint: id
assignment and mismatch detection are pure-`pandas` functions tested
directly, and the two functions that touch an `Engine`
(`apply_reassignment`, `main`) are exercised against a fake engine/
connection recording what SQL it was asked to run, following the pattern
`test_pull_predictions.py` established for `_apply_remap`.

`scripts/` carries no `__init__.py`, so the module is loaded by file path
rather than imported by dotted name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from capitalscan.core.config import ServingParams
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH

CHASH = DEFAULT_CONFIG_HASH
FLOOR = ServingParams().serving_id_floor

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "repair_prediction_ids.py"
_spec = importlib.util.spec_from_file_location("repair_prediction_ids", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
repair = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = repair
_spec.loader.exec_module(repair)


def _predictions(rows: list[tuple]) -> pd.DataFrame:
    """`id` plus the natural key, matching `_read_predictions_keys`'s SELECT."""
    return pd.DataFrame(
        rows,
        columns=["id", "config_hash", "ticker", "as_of", "signal_type", "entry_kind"],
    )


def _row(id_: int, ticker: str, as_of: str = "2026-09-17", signal_type: str = "bb_upper_touch"):
    return (id_, CHASH, ticker, as_of, signal_type, "touch")


class TestPlanReassignment:
    def test_a_row_with_no_research_counterpart_is_selected(self):
        serving = _predictions([_row(100, "HPE")])
        research = _predictions([])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert moves["id"].tolist() == [100]
        assert moves.loc[0, "new_id"] >= FLOOR

    def test_a_row_whose_natural_key_research_also_holds_is_left_alone(self):
        """One of the 35,292 rows both stores already agree on -- even
        sharing the id, this is not a serving-born row."""
        serving = _predictions([_row(100, "AA")])
        research = _predictions([_row(100, "AA")])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert moves.empty

    def test_a_row_already_at_or_above_the_floor_is_left_alone(self):
        """`_reset_sequences` (earlier task) already put this row on the
        right side of the split; the repair must not touch it again."""
        serving = _predictions([_row(FLOOR + 5, "ZZ")])
        research = _predictions([])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert moves.empty

    def test_new_ids_are_unique_and_at_or_above_the_floor(self):
        serving = _predictions([_row(1, "AA"), _row(2, "BB"), _row(3, "CC")])
        research = _predictions([])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert len(moves) == 3
        new_ids = moves["new_id"].tolist()
        assert len(set(new_ids)) == 3
        assert all(n >= FLOOR for n in new_ids)

    def test_new_ids_never_collide_with_a_row_already_above_the_floor(self):
        """The Pi may already have minted above the floor before this repair
        runs. The assignment must start past the store's own max, not just
        past the floor."""
        serving = _predictions([_row(1, "AA"), _row(FLOOR + 2, "BB")])
        research = _predictions([])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert len(moves) == 1
        assert moves.loc[0, "id"] == 1
        assert moves.loc[0, "new_id"] > FLOOR + 2

    def test_nothing_to_move_returns_an_empty_frame_with_a_new_id_column(self):
        serving = _predictions([_row(1, "AA")])
        research = _predictions([_row(1, "AA")])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert moves.empty
        assert "new_id" in moves.columns

    def test_the_three_measured_collisions_are_all_selected(self):
        """research's JPM/MPC/ZS sit at the same ids as serving's
        HPE/ECHO/VLO. All three describe a serving-only signal, so all
        three must be in the move set."""
        serving = _predictions([_row(10, "HPE"), _row(11, "ECHO"), _row(12, "VLO")])
        research = _predictions([_row(10, "JPM"), _row(11, "MPC"), _row(12, "ZS")])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        assert sorted(moves["id"].tolist()) == [10, 11, 12]


class TestMismatchedIds:
    def test_an_id_naming_different_tickers_on_each_side_is_reported(self):
        serving = _predictions([_row(10, "HPE")])
        research = _predictions([_row(10, "JPM")])
        assert repair.mismatched_ids(serving, research) == [10]

    def test_an_id_naming_the_same_signal_on_each_side_is_not_reported(self):
        serving = _predictions([_row(10, "AA")])
        research = _predictions([_row(10, "AA")])
        assert repair.mismatched_ids(serving, research) == []

    def test_an_id_present_on_one_side_only_is_not_a_mismatch(self):
        serving = _predictions([_row(100, "HPE")])
        research = _predictions([])
        assert repair.mismatched_ids(serving, research) == []

    def test_empty_inputs_report_no_mismatches(self):
        assert repair.mismatched_ids(_predictions([]), _predictions([])) == []

    def test_this_is_the_repairs_own_postcondition(self):
        """After `plan_reassignment` moves the collision off the shared id,
        re-checking against the *new* id must find nothing -- the same
        computation the script re-runs after `apply_reassignment`."""
        serving = _predictions([_row(10, "HPE")])
        research = _predictions([_row(10, "JPM")])
        moves = repair.plan_reassignment(serving, research, FLOOR)
        moved_serving = serving.copy()
        moved_serving.loc[moved_serving["id"] == 10, "id"] = moves.loc[0, "new_id"]
        assert repair.mismatched_ids(moved_serving, research) == []


class _FakeConnection:
    """Records every statement it is asked to execute and answers the one
    `SELECT count(*)` `apply_reassignment` issues before the write."""

    def __init__(self, outcomes_count: int) -> None:
        self.outcomes_count = outcomes_count
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params or {}))

        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar_one(self):
                return self._value

        if "SELECT count(*)" in sql:
            return _Result(self.outcomes_count)
        return _Result(None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, outcomes_count: int = 0) -> None:
        self.conn = _FakeConnection(outcomes_count)
        self.committed = False

    def begin(self):
        return self.conn


class TestPredictedSequenceValue:
    def test_no_rows_and_no_moves_returns_the_floor(self):
        assert repair.predicted_sequence_value(_predictions([]), _predictions([]), FLOOR) == FLOOR

    def test_an_existing_max_id_above_the_floor_wins_over_the_floor(self):
        """Possible if the Pi has already minted above the floor since it
        was deployed, ahead of this repair running."""
        serving = _predictions([_row(FLOOR + 9, "ZZ")])
        moves = repair.plan_reassignment(serving, _predictions([]), FLOOR)
        assert repair.predicted_sequence_value(serving, moves, FLOOR) == FLOOR + 9

    def test_a_moves_new_id_max_wins_when_higher_than_the_existing_max(self):
        serving = _predictions([_row(1, "AA"), _row(2, "BB")])
        moves = repair.plan_reassignment(serving, _predictions([]), FLOOR)
        predicted = repair.predicted_sequence_value(serving, moves, FLOOR)
        assert predicted == moves["new_id"].max()
        assert predicted >= FLOOR


class _FakeReadConnection:
    """Answers `_read_sequence_value`'s single `SELECT last_value` read."""

    def __init__(self, value: int) -> None:
        self.value = value
        self.executed: list[str] = []

    def execute(self, stmt, params=None):
        self.executed.append(str(stmt))

        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar_one(self):
                return self._value

        return _Result(self.value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeReadEngine:
    def __init__(self, value: int) -> None:
        self.conn = _FakeReadConnection(value)

    def connect(self):
        return self.conn


class TestReadSequenceValue:
    def test_reads_last_value_off_the_sequence(self):
        engine = _FakeReadEngine(224_862)
        assert repair._read_sequence_value(engine) == 224_862
        assert "predictions_id_seq" in engine.conn.executed[0]


class TestApplyReassignment:
    def test_an_empty_move_set_writes_nothing(self):
        engine = _FakeEngine()
        reidentified, outcomes_repointed = repair.apply_reassignment(
            engine, repair.plan_reassignment(_predictions([]), _predictions([]), FLOOR)
        )
        assert (reidentified, outcomes_repointed) == (0, 0)
        assert engine.conn.executed == []

    def test_the_combined_statement_carries_both_id_arrays(self):
        moves = repair.plan_reassignment(_predictions([_row(1, "AA")]), _predictions([]), FLOOR)
        engine = _FakeEngine(outcomes_count=2)
        reidentified, outcomes_repointed = repair.apply_reassignment(engine, moves)
        assert reidentified == 1
        assert outcomes_repointed == 2
        # First statement reads the count; second is the combined reassignment.
        assert len(engine.conn.executed) == 2
        count_sql, count_params = engine.conn.executed[0]
        assert "outcomes" in count_sql
        assert count_params["old_ids"] == [1]
        move_sql, move_params = engine.conn.executed[1]
        assert "predictions" in move_sql and "outcomes" in move_sql
        assert move_params["old_ids"] == [1]
        assert move_params["new_ids"] == moves["new_id"].tolist()

    def test_the_statement_updates_predictions_before_being_checked(self):
        """Guards the FK-ordering reasoning in the module docstring: the
        reassignment must be one statement, not two, or a real database
        would raise on the `outcomes` foreign key. This only proves the
        *shape* against a fake -- there is no constraint to violate here --
        but a caller who split it into two `conn.execute` calls would fail
        this count assertion."""
        moves = repair.plan_reassignment(_predictions([_row(1, "AA")]), _predictions([]), FLOOR)
        engine = _FakeEngine(outcomes_count=0)
        repair.apply_reassignment(engine, moves)
        assert len(engine.conn.executed) == 2, "expected one read plus one combined write"


class TestDryRunIsTheDefaultAndWritesNothing:
    def test_main_with_no_flags_does_not_call_apply_reassignment(self, monkeypatch):
        moves = repair.plan_reassignment(_predictions([_row(1, "AA")]), _predictions([]), FLOOR)
        monkeypatch.setattr(repair, "serving_engine", lambda: object())
        monkeypatch.setattr(repair.db_io, "get_engine", lambda: object())
        monkeypatch.setattr(repair, "_read_predictions_keys", lambda engine: pd.DataFrame())
        monkeypatch.setattr(repair, "_read_sequence_value", lambda engine: 0)
        monkeypatch.setattr(repair, "plan_reassignment", lambda *a, **k: moves)
        monkeypatch.setattr(repair, "mismatched_ids", lambda *a, **k: [])

        calls: list[Any] = []
        monkeypatch.setattr(repair, "apply_reassignment", lambda *a, **k: calls.append(a) or (0, 0))
        monkeypatch.setattr(repair, "_reset_sequences", lambda *a, **k: calls.append("reset"))

        code = repair.main([])
        assert code == 0
        assert calls == [], "a dry run (no --apply) must not write anything"

    def test_apply_flag_calls_apply_reassignment_and_resets_sequences(self, monkeypatch):
        moves = repair.plan_reassignment(_predictions([_row(1, "AA")]), _predictions([]), FLOOR)
        monkeypatch.setattr(repair, "serving_engine", lambda: object())
        monkeypatch.setattr(repair.db_io, "get_engine", lambda: object())
        monkeypatch.setattr(repair, "_read_predictions_keys", lambda engine: pd.DataFrame())
        monkeypatch.setattr(repair, "_read_sequence_value", lambda engine: 0)
        monkeypatch.setattr(repair, "plan_reassignment", lambda *a, **k: moves)
        monkeypatch.setattr(repair, "mismatched_ids", lambda *a, **k: [])

        calls: list[Any] = []
        monkeypatch.setattr(
            repair, "apply_reassignment", lambda *a, **k: (calls.append("apply"), (1, 0))[1]
        )
        monkeypatch.setattr(repair, "_reset_sequences", lambda *a, **k: calls.append("reset"))

        code = repair.main(["--apply"])
        assert code == 0
        assert calls == ["apply", "reset"]

    def test_apply_reports_failure_when_mismatches_remain_after_repair(self, monkeypatch):
        """The postcondition check: if a mismatch survives the repair, the
        script must say so and exit non-zero rather than declare success."""
        moves = repair.plan_reassignment(_predictions([_row(1, "AA")]), _predictions([]), FLOOR)
        monkeypatch.setattr(repair, "serving_engine", lambda: object())
        monkeypatch.setattr(repair.db_io, "get_engine", lambda: object())
        monkeypatch.setattr(repair, "_read_predictions_keys", lambda engine: pd.DataFrame())
        monkeypatch.setattr(repair, "_read_sequence_value", lambda engine: 0)
        monkeypatch.setattr(repair, "plan_reassignment", lambda *a, **k: moves)
        # Pre-repair check clean, post-repair check still finds one -- an
        # impossible state in practice, but it is exactly the branch this
        # test exercises.
        results = iter([[], [10]])
        monkeypatch.setattr(repair, "mismatched_ids", lambda *a, **k: next(results))
        monkeypatch.setattr(repair, "apply_reassignment", lambda *a, **k: (1, 0))
        monkeypatch.setattr(repair, "_reset_sequences", lambda *a, **k: None)

        code = repair.main(["--apply"])
        assert code == 1

    def test_nothing_to_reassign_with_apply_still_calls_reset_sequences(self, monkeypatch):
        """Not a no-op: `_reset_sequences` is owed on every `--apply`, not
        only when this run found rows to move. `apply_reassignment` must
        stay untouched (there is nothing to write), but the sequence check
        must still run -- see the next test for why this matters."""
        empty = repair.plan_reassignment(_predictions([]), _predictions([]), FLOOR)
        monkeypatch.setattr(repair, "serving_engine", lambda: object())
        monkeypatch.setattr(repair.db_io, "get_engine", lambda: object())
        monkeypatch.setattr(repair, "_read_predictions_keys", lambda engine: pd.DataFrame())
        monkeypatch.setattr(repair, "_read_sequence_value", lambda engine: 0)
        monkeypatch.setattr(repair, "plan_reassignment", lambda *a, **k: empty)
        monkeypatch.setattr(repair, "mismatched_ids", lambda *a, **k: [])

        calls: list[Any] = []
        monkeypatch.setattr(
            repair, "apply_reassignment", lambda *a, **k: calls.append("apply") or (0, 0)
        )
        monkeypatch.setattr(repair, "_reset_sequences", lambda *a, **k: calls.append("reset"))

        code = repair.main(["--apply"])
        assert code == 0
        assert calls == ["reset"], "nothing to move, but the reset is still owed"

    def test_a_rerun_after_an_interrupted_apply_still_repairs_the_sequence(self, monkeypatch):
        """Reproduces the reported gap: a prior `--apply` reassigned its
        rows and committed (durable), then the process died before
        `_reset_sequences` ran. Rerunning finds those rows already at or
        above the floor -- `plan_reassignment` (the REAL function, not
        mocked) correctly recomputes an empty move set for them, since
        they are no longer serving-born by this run's definition. The old
        code took that as "nothing to do" and skipped the reset it still
        owed; the fixed `main()` must call `_reset_sequences` regardless."""
        already_moved_id = FLOOR + 42
        serving = _predictions([_row(already_moved_id, "HPE")])
        research = _predictions([])  # not yet adopted -- pull_live_records hasn't run
        monkeypatch.setattr(repair, "serving_engine", lambda: "serving-engine")
        monkeypatch.setattr(repair.db_io, "get_engine", lambda: "research-engine")

        def fake_read_keys(engine):
            return serving if engine == "serving-engine" else research

        monkeypatch.setattr(repair, "_read_predictions_keys", fake_read_keys)
        # The sequence is still behind -- exactly the interrupted state.
        monkeypatch.setattr(repair, "_read_sequence_value", lambda engine: already_moved_id - 1)

        reset_calls: list[Any] = []
        monkeypatch.setattr(
            repair, "_reset_sequences", lambda engine, serving=False: reset_calls.append(engine)
        )
        apply_calls: list[Any] = []
        monkeypatch.setattr(
            repair, "apply_reassignment", lambda *a, **k: apply_calls.append(a) or (0, 0)
        )

        code = repair.main(["--apply"])
        assert code == 0
        assert apply_calls == [], "nothing left for THIS run to reassign"
        assert reset_calls == ["serving-engine"], "the sequence reset must still run on a rerun"


class TestNaturalKeyIsSharedWithSync:
    def test_natural_key_matches__PREDICTIONS_EVENT_REMAPs_source_key(self):
        """The script must not carry its own copy of the natural key --
        the nightly pull (`_pull_predictions`) and this repair have to
        agree on what identifies a prediction, or a row this script moves
        could still fail to adopt."""
        from capitalscan.jobs.sync import _PREDICTIONS_EVENT_REMAP

        assert repair.NATURAL_KEY == _PREDICTIONS_EVENT_REMAP.source_key
