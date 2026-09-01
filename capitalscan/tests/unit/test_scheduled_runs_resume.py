"""`scheduled_runs.resume_decision` — the gate the `run_job` wrappers call
on a timer or boot trigger.

`systemd`'s `Persistent=true` re-fires a run missed while the machine was
off; `OnBootSec=` re-fires one that crashed mid-run. Neither knows whether
the chain has since completed, so a wrapper that just ran the job on every
trigger would redo a finished nightly after any reboot. `resume_decision`
answers "does this period still need a run?" from the `scheduled_runs`
record, and fails toward running.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from capitalscan.jobs import scheduled_runs

LA = ZoneInfo("America/Los_Angeles")
# A fixed Wednesday afternoon. Nightly period start -> 09-02 00:00,
# weekly period start -> Sunday 08-30 00:00, monthly -> 09-01 00:00.
WED = datetime(2026, 9, 2, 15, 0, tzinfo=LA)


class _FakeResult:
    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def first(self) -> tuple | None:
        return self._row


class _FakeConn:
    def __init__(self, row: tuple | None) -> None:
        self._row = row
        self.executed: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):  # noqa: ANN001, ANN201
        self.executed.append((str(statement), dict(params or {})))
        return _FakeResult(self._row)


class _FakeEngine:
    """Returns one canned `(status, actual_start)` row (or None) for the
    single SELECT `resume_decision` runs."""

    def __init__(self, row: tuple | None) -> None:
        self.conn = _FakeConn(row)

    @contextmanager
    def begin(self):  # noqa: ANN201
        yield self.conn


def _decide(job: str, row: tuple | None, now: datetime = WED):
    return scheduled_runs.resume_decision(_FakeEngine(row), job, now)


# --- no record / crashed / failed -> run -----------------------------------


def test_no_prior_run_means_run() -> None:
    decision, detail = _decide("nightly", None)
    assert decision == "run"
    assert "nightly" in detail


def test_started_but_never_finished_means_run() -> None:
    """A `status='started'` row with no terminal write is a crash or a
    kill — the exact case `OnBootSec` exists to recover."""
    decision, detail = _decide("nightly", ("started", WED.replace(hour=13, minute=20)))
    assert decision == "run"
    assert "never finished" in detail


def test_failed_run_means_run() -> None:
    decision, detail = _decide("nightly", ("failed", WED.replace(hour=13, minute=20)))
    assert decision == "run"
    assert "failed" in detail


# --- ok this period -> already_complete -----------------------------------


def test_nightly_ok_today_is_already_complete() -> None:
    decision, detail = _decide("nightly", ("ok", WED.replace(hour=13, minute=20)))
    assert decision == "already_complete"
    assert "nothing to do" in detail


def test_nightly_ok_yesterday_means_run() -> None:
    decision, detail = _decide(
        "nightly", ("ok", WED.replace(hour=13, minute=20) - timedelta(days=1))
    )
    assert decision == "run"
    assert "previous period" in detail


def test_weekly_ok_since_sunday_is_already_complete() -> None:
    sunday_run = datetime(2026, 8, 30, 3, 30, tzinfo=LA)  # Sunday 03:30, after the 02:00 slot
    decision, _ = _decide("weekly", ("ok", sunday_run), now=WED)
    assert decision == "already_complete"


def test_weekly_ok_last_week_means_run() -> None:
    prev_sunday = datetime(2026, 8, 23, 3, 30, tzinfo=LA)
    decision, _ = _decide("weekly", ("ok", prev_sunday), now=WED)
    assert decision == "run"


def test_weekly_crashed_this_week_means_run() -> None:
    sunday_run = datetime(2026, 8, 30, 2, 5, tzinfo=LA)
    decision, _ = _decide("weekly", ("started", sunday_run), now=WED)
    assert decision == "run"


def test_monthly_ok_this_month_is_already_complete() -> None:
    first_run = datetime(2026, 9, 1, 3, 10, tzinfo=LA)
    decision, _ = _decide("monthly", ("ok", first_run), now=WED)
    assert decision == "already_complete"


def test_monthly_ok_last_month_means_run() -> None:
    last_month = datetime(2026, 8, 1, 3, 10, tzinfo=LA)
    decision, _ = _decide("monthly", ("ok", last_month), now=WED)
    assert decision == "run"


# --- boundaries and tz handling -----------------------------------------


def test_period_start_is_naive_wall_clock() -> None:
    """`_period_start` drops tzinfo: the only thing compared against it is
    `actual_start`, whose stored digits are a Pacific wall clock whatever
    tzinfo they read back with."""
    assert scheduled_runs._period_start("nightly", WED) == datetime(2026, 9, 2, 0, 0)
    assert scheduled_runs._period_start("nightly", WED).tzinfo is None


def test_period_start_weekly_lands_on_the_preceding_sunday() -> None:
    # Wednesday -> the Sunday three days back.
    assert scheduled_runs._period_start("weekly", WED) == datetime(2026, 8, 30, 0, 0)
    # A Sunday afternoon -> that same day's midnight, not seven days back.
    sunday_pm = datetime(2026, 8, 30, 14, 0, tzinfo=LA)
    assert scheduled_runs._period_start("weekly", sunday_pm) == datetime(2026, 8, 30, 0, 0)


def test_weekly_sunday_0200_run_counts_this_week() -> None:
    """The tz-skew bug: a Sunday 02:00 run stored as tz-aware UTC digits,
    compared naively, must still land inside this week's period. Trusting
    the (wrong) UTC tzinfo would push it before the boundary and re-run a
    completed weekly."""
    sunday_0200 = datetime(2026, 8, 30, 2, 0, tzinfo=ZoneInfo("UTC"))
    decision, _ = _decide("weekly", ("ok", sunday_0200), now=WED)
    assert decision == "already_complete"


def test_naive_and_aware_now_agree() -> None:
    aware_start = datetime(2026, 9, 2, 13, 20, tzinfo=LA)
    naive = scheduled_runs.resume_decision(
        _FakeEngine(("ok", aware_start)), "nightly", datetime(2026, 9, 2, 15, 0)
    )
    aware = scheduled_runs.resume_decision(_FakeEngine(("ok", aware_start)), "nightly", WED)
    assert naive[0] == aware[0] == "already_complete"


def test_run_when_ok_row_sits_exactly_on_the_period_boundary() -> None:
    """`actual_start >= period_start` is inclusive, so a run stamped at
    local midnight counts as this period."""
    decision, _ = _decide("nightly", ("ok", datetime(2026, 9, 2, 0, 0, tzinfo=LA)))
    assert decision == "already_complete"


def test_unknown_job_raises() -> None:
    with pytest.raises(ValueError, match="unknown job"):
        _decide("hourly", ("ok", WED))
