"""Tests for `jobs/status.py` and `scheduled_runs.complete` (DESIGN §9.6,
ADR 080, ADR 083).

No live database: every query function takes an `Engine` and calls
`pd.read_sql`, so the tests stub `pandas.read_sql` at the module the
function reads it through, and assert on the derived columns rather than
on SQL text. The SQL itself is exercised by the integration tier.

The derived flags are the whole point of this module — the raw columns
already sat in `runs` and `scheduled_runs` for months with nothing reading
them — so that is where the coverage goes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from capitalscan.core.config import MonitoringThresholds
from capitalscan.jobs import scheduled_runs
from capitalscan.jobs import status as job_status

THRESHOLDS = MonitoringThresholds()


class _FakeEngine:
    """`job_summary` only uses the engine as a `connect()` context manager
    handed to `pd.read_sql`, which is stubbed, so nothing here connects."""

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _runs_frame(rows: list[dict]) -> pd.DataFrame:
    cols = ["job", "run_id", "status", "started_at", "finished_at", "rows_written", "notes"]
    return pd.DataFrame(rows, columns=cols)


class TestJobSummary:
    def test_staleness_measures_from_finished_at(self, monkeypatch):
        finished = _now() - timedelta(days=5)
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: _runs_frame(
                [
                    {
                        "job": "nightly",
                        "run_id": "r1",
                        "status": "ok",
                        "started_at": finished - timedelta(minutes=10),
                        "finished_at": finished,
                        "rows_written": 10,
                        "notes": None,
                    }
                ]
            ),
        )
        out = job_status.job_summary(_FakeEngine(), THRESHOLDS)
        assert out.loc[0, "staleness_days"] == pytest.approx(5.0, abs=0.01)
        assert bool(out.loc[0, "is_stale"]) is True

    def test_staleness_falls_back_to_started_at_when_still_running(self, monkeypatch):
        """A run with no `finished_at` must still appear. Measuring from a
        NULL finish would drop the row from a report whose entire purpose is
        surfacing jobs that stopped happening."""
        started = _now() - timedelta(days=4)
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: _runs_frame(
                [
                    {
                        "job": "poll",
                        "run_id": "r2",
                        "status": "running",
                        "started_at": started,
                        "finished_at": None,
                        "rows_written": None,
                        "notes": None,
                    }
                ]
            ),
        )
        out = job_status.job_summary(_FakeEngine(), THRESHOLDS)
        assert len(out) == 1
        assert out.loc[0, "staleness_days"] == pytest.approx(4.0, abs=0.01)
        assert bool(out.loc[0, "is_stale"]) is True

    def test_fresh_job_is_not_stale(self, monkeypatch):
        finished = _now() - timedelta(hours=3)
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: _runs_frame(
                [
                    {
                        "job": "nightly",
                        "run_id": "r3",
                        "status": "ok",
                        "started_at": finished,
                        "finished_at": finished,
                        "rows_written": 5,
                        "notes": None,
                    }
                ]
            ),
        )
        out = job_status.job_summary(_FakeEngine(), THRESHOLDS)
        assert bool(out.loc[0, "is_stale"]) is False

    def test_empty_runs_table_returns_the_derived_columns_anyway(self, monkeypatch):
        """An empty frame must still carry `is_stale`/`staleness_days`, or
        the caller's `.loc[df['is_stale']]` raises KeyError on a fresh
        database instead of reporting nothing to see."""
        monkeypatch.setattr(pd, "read_sql", lambda *a, **k: _runs_frame([]))
        out = job_status.job_summary(_FakeEngine(), THRESHOLDS)
        assert out.empty
        for col in ("last_seen", "staleness_days", "is_stale"):
            assert col in out.columns

    def test_stalest_job_sorts_first(self, monkeypatch):
        now = _now()
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: _runs_frame(
                [
                    {
                        "job": "fresh",
                        "run_id": "a",
                        "status": "ok",
                        "started_at": now,
                        "finished_at": now,
                        "rows_written": 1,
                        "notes": None,
                    },
                    {
                        "job": "ancient",
                        "run_id": "b",
                        "status": "ok",
                        "started_at": now - timedelta(days=30),
                        "finished_at": now - timedelta(days=30),
                        "rows_written": 1,
                        "notes": None,
                    },
                ]
            ),
        )
        out = job_status.job_summary(_FakeEngine(), THRESHOLDS)
        assert out.loc[0, "job"] == "ancient"


class TestScheduleSummary:
    def test_delay_past_the_threshold_flags_catch_up(self, monkeypatch):
        """ADR 080: past `catch_up_delay_seconds` the machine was off."""
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: pd.DataFrame(
                [
                    {
                        "job": "nightly",
                        "scheduled_for": _now() - timedelta(days=1),
                        "actual_start": _now(),
                        "delay_seconds": THRESHOLDS.catch_up_delay_seconds + 1,
                        "status": "ok",
                        "run_id": "r1",
                    }
                ]
            ),
        )
        out = job_status.schedule_summary(_FakeEngine(), THRESHOLDS)
        assert bool(out.loc[0, "was_caught_up"]) is True

    def test_delay_at_the_threshold_does_not_flag(self, monkeypatch):
        """Boundary: the ADR says *above* 3600 s, so exactly 3600 is not a
        catch-up."""
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: pd.DataFrame(
                [
                    {
                        "job": "poll",
                        "scheduled_for": _now(),
                        "actual_start": _now(),
                        "delay_seconds": THRESHOLDS.catch_up_delay_seconds,
                        "status": "ok",
                        "run_id": "r2",
                    }
                ]
            ),
        )
        out = job_status.schedule_summary(_FakeEngine(), THRESHOLDS)
        assert bool(out.loc[0, "was_caught_up"]) is False

    def test_null_delay_is_not_a_catch_up(self, monkeypatch):
        monkeypatch.setattr(
            pd,
            "read_sql",
            lambda *a, **k: pd.DataFrame(
                [
                    {
                        "job": "weekly",
                        "scheduled_for": _now(),
                        "actual_start": None,
                        "delay_seconds": None,
                        "status": "started",
                        "run_id": None,
                    }
                ]
            ),
        )
        out = job_status.schedule_summary(_FakeEngine(), THRESHOLDS)
        assert bool(out.loc[0, "was_caught_up"]) is False


class TestScheduledRunsComplete:
    """`complete` must target the job's newest slot, never a recomputed one.

    The bug this guards: `nightly` fires at 16:30, so a run starting 16:29
    and finishing 16:31 opens the previous day's slot. Recomputing
    `_scheduled_for` at completion time would close a *different* row,
    leaving one permanently 'started' and marking another complete that
    never ran.
    """

    def test_updates_the_max_scheduled_for_slot_only(self):
        captured = {}

        class _Conn:
            def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params

                class _R:
                    rowcount = 1

                return _R()

        class _Engine:
            def begin(self):
                return self

            def __enter__(self):
                return _Conn()

            def __exit__(self, *exc):
                return False

        n = scheduled_runs.complete(_Engine(), "nightly", "ok", run_id="r9")
        assert n == 1
        assert "max(scheduled_for)" in captured["sql"]
        assert captured["params"]["job"] == "nightly"
        assert captured["params"]["status"] == "ok"
        assert captured["params"]["run_id"] == "r9"

    def test_null_run_id_preserves_whatever_record_wrote(self):
        """`COALESCE(:run_id, run_id)`: completing without a run_id must not
        blank one `record` already stored (the poller passes one up front)."""
        captured = {}

        class _Conn:
            def execute(self, stmt, params):
                captured["sql"] = str(stmt)

                class _R:
                    rowcount = 1

                return _R()

        class _Engine:
            def begin(self):
                return self

            def __enter__(self):
                return _Conn()

            def __exit__(self, *exc):
                return False

        scheduled_runs.complete(_Engine(), "monthly", "ok")
        assert "COALESCE(:run_id, run_id)" in captured["sql"]


class TestMonitoringThresholdsStayOutOfConfigHash:
    def test_monitoring_is_not_a_field_of_config(self):
        """Invariant 9 wants the numbers out of the code, but folding them
        into `Config` would move `config_hash` for every existing config in
        service of values that cannot affect an event row. Same standing as
        `SweepParams` and `SharesPlausibility`."""
        import dataclasses

        from capitalscan.core.config import Config

        field_types = {f.name: f.type for f in dataclasses.fields(Config)}
        assert not any("Monitoring" in str(t) for t in field_types.values())
