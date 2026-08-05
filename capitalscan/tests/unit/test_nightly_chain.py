"""BUILD.md §9.0 prerequisite: `run_bars_hourly` had exactly one caller (the
`bars` CLI command), so nothing kept the hourly table current. A stale
hourly table makes `core.returns.entry_price_for` return NaN instead of
raising, which would silently drop two of four entry kinds in Task 5.

This test asserts `nightly()` calls `ingest.run_bars_hourly` with the same
`(tickers, start, end)` window it passes to `ingest.run_bars_daily`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from capitalscan.jobs import cli, compute, db_io, ingest, scheduled_runs
from capitalscan.research import path_backfill as path_backfill_mod


@pytest.fixture(autouse=True)
def _no_real_nightly_io(monkeypatch):
    """Nightly touches the database directly (engine + ticker resolution)
    before it ever reaches the job functions under test. Stub those two
    entry points so this stays a unit test — none of the assertions below
    care what engine or ticker list is used, only that the same window
    reaches both bars calls.
    """
    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(cli, "_resolve_tickers", lambda tickers: ["AAPL", "MSFT"])
    monkeypatch.setattr(scheduled_runs, "record", lambda engine, job: None)
    monkeypatch.setattr(path_backfill_mod, "run_path_capture", lambda *args, **kwargs: None)


def _record_call(calls: list, name: str):
    def _fake(*args, **kwargs):
        calls.append({"name": name, "args": args, "kwargs": kwargs})

    return _fake


def test_nightly_calls_run_bars_hourly_with_daily_window(monkeypatch):
    calls: list = []

    # Every ingest.run_* and compute.run_* function nightly() invokes,
    # patched on the module object per the caller's note: nightly() imports
    # `compute, db_io, ingest, scheduled_runs` inside the function body, so
    # patching the module attribute (not a name bound at import time) is
    # what actually takes effect.
    for name in ["run_bars_daily", "run_bars_hourly", "run_actions", "run_market", "run_shares", "run_earnings"]:
        monkeypatch.setattr(ingest, name, _record_call(calls, name))
    for name in ["run_indicators", "run_events"]:
        monkeypatch.setattr(compute, name, _record_call(calls, name))

    cli.nightly()

    names = [c["name"] for c in calls]
    assert "run_bars_hourly" in names, "nightly() never called run_bars_hourly"

    daily_call = next(c for c in calls if c["name"] == "run_bars_daily")
    hourly_call = next(c for c in calls if c["name"] == "run_bars_hourly")

    # tickers, start, end are passed positionally (`ingest.run_bars_daily(tickers,
    # start, end, engine=engine)`); args[1]/args[2] are start/end.
    daily_start, daily_end = daily_call["args"][1], daily_call["args"][2]
    hourly_start, hourly_end = hourly_call["args"][1], hourly_call["args"][2]

    assert hourly_start == daily_start
    assert hourly_end == daily_end

    # Pin the actual window too, so a future refactor that changes the
    # lookback silently can't slip through just because start == end holds.
    end = date.today()
    assert daily_start == end - timedelta(days=5)
    assert daily_end == end


def test_nightly_calls_run_path_capture_after_run_events(monkeypatch):
    # Task 10.6: `cscan nightly` is what Task Scheduler actually runs
    # (scripts/nightly.bat -> `cscan nightly`) — a path-capture job that
    # only exists as a standalone CLI command never runs on a schedule.
    # Must fire after run_events: a signal fired tonight needs its events
    # row to exist before it's selectable as an incomplete-window event.
    calls: list = []

    for name in ["run_bars_daily", "run_bars_hourly", "run_actions", "run_market", "run_shares", "run_earnings"]:
        monkeypatch.setattr(ingest, name, _record_call(calls, name))
    for name in ["run_indicators", "run_events"]:
        monkeypatch.setattr(compute, name, _record_call(calls, name))
    monkeypatch.setattr(path_backfill_mod, "run_path_capture", _record_call(calls, "run_path_capture"))

    cli.nightly()

    names = [c["name"] for c in calls]
    assert "run_path_capture" in names, "nightly() never called run_path_capture"
    assert names.index("run_path_capture") > names.index("run_events")
