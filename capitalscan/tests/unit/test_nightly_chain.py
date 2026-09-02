"""BUILD.md §9.0 prerequisite: `run_bars_hourly` had exactly one caller (the
`bars` CLI command), so nothing kept the hourly table current. A stale
hourly table makes `core.returns.entry_price_for` return NaN instead of
raising, which would silently drop two of four entry kinds in Task 5.

This test asserts `nightly()` calls `ingest.run_bars_hourly` with the same
`(tickers, start, end)` window it passes to `ingest.run_bars_daily`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from capitalscan.jobs import cli, compute, db_io, ingest, scheduled_runs
from capitalscan.research import path_backfill as path_backfill_mod
from capitalscan.research.path_backfill import PathBackfillReport


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
    # `complete` closes the slot `record` opened (DESIGN §9.6, added
    # 2026-08-09). It is a second database boundary in the same chain, so it
    # needs the same stub — without it the real function runs against this
    # fixture's placeholder engine string.
    monkeypatch.setattr(scheduled_runs, "complete", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        path_backfill_mod, "run_path_capture", lambda *args, **kwargs: PathBackfillReport()
    )
    # ADR 093's peak refresh runs after path capture (added 2026-08-09) and
    # is a third database boundary in this chain. `nightly` imports it
    # inside the function body, so patching the module attribute is what
    # the call actually resolves against.
    from capitalscan.research import peak_labels as peak_labels_mod

    monkeypatch.setattr(peak_labels_mod, "backfill_peak_labels", lambda *args, **kwargs: 0)

    # `nightly` wraps its path capture in `ingest.run_job` (2026-08-06) so
    # the rows it writes carry a `run_id` — `path.run_id`, ADR 034. The real
    # `run_job` inserts a `runs` row, which is exactly the database access
    # this fixture exists to keep out of a unit test.
    @contextmanager
    def _fake_run_job(engine, job, params):
        yield ingest.IngestReport(job=job, run_id="test-run-id")

    monkeypatch.setattr(ingest, "run_job", _fake_run_job)

    # **The ticker seed refresh joined the chain 2026-09-01, and it reaches
    # the network.** `nightly` wraps it in a `try`, so leaving it unstubbed
    # does not fail these tests -- it just quietly scrapes Wikipedia and the
    # SEC from a unit test, or reads their cache, which is exactly the IO
    # this fixture exists to keep out. Silent because the failure path is
    # the intended one.
    monkeypatch.setattr(
        ingest,
        "run_tickers_refresh",
        lambda **kwargs: ingest.IngestReport(job="tickers", run_id="test-run-id"),
    )


def _record_call(calls: list, name: str, result=None):
    def _fake(*args, **kwargs):
        calls.append({"name": name, "args": args, "kwargs": kwargs})
        return result

    return _fake


def test_nightly_calls_run_bars_hourly_with_daily_window(monkeypatch):
    calls: list = []

    # Every ingest.run_* and compute.run_* function nightly() invokes,
    # patched on the module object per the caller's note: nightly() imports
    # `compute, db_io, ingest, scheduled_runs` inside the function body, so
    # patching the module attribute (not a name bound at import time) is
    # what actually takes effect.
    for name in [
        "run_bars_daily",
        "run_bars_hourly",
        "run_actions",
        "run_market",
        "run_shares",
        "run_earnings",
    ]:
        monkeypatch.setattr(ingest, name, _record_call(calls, name))
    for name in ["run_indicators", "run_events"]:
        monkeypatch.setattr(compute, name, _record_call(calls, name))

    monkeypatch.setattr(
        cli, "_sweep_provisional_poll_rows", lambda *a, **k: 0
    )  # ADR 150; sentinel engine has no .begin()

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

    for name in [
        "run_bars_daily",
        "run_bars_hourly",
        "run_actions",
        "run_market",
        "run_shares",
        "run_earnings",
    ]:
        monkeypatch.setattr(ingest, name, _record_call(calls, name))
    for name in ["run_indicators", "run_events"]:
        monkeypatch.setattr(compute, name, _record_call(calls, name))
    monkeypatch.setattr(
        path_backfill_mod,
        "run_path_capture",
        _record_call(calls, "run_path_capture", result=PathBackfillReport()),
    )
    # ADR 150: the nightly sweeps unreconciled poller rows after
    # `run_events`. Stubbed here like every other real call in the chain --
    # these tests hand `nightly()` a sentinel engine, so a genuine
    # `engine.begin()` is an AttributeError rather than a query.
    monkeypatch.setattr(
        cli, "_sweep_provisional_poll_rows", _record_call(calls, "sweep_poll", result=0)
    )

    cli.nightly()

    names = [c["name"] for c in calls]
    assert "run_path_capture" in names, "nightly() never called run_path_capture"
    assert names.index("run_path_capture") > names.index("run_events")
    # ADR 150's ordering is the decision, not an implementation detail: the
    # sweep deletes rows the authoritative pass declined to reproduce, so it
    # is only correct *after* that pass has run.
    assert "sweep_poll" in names, "nightly() never swept provisional poller rows"
    assert names.index("sweep_poll") > names.index("run_events")


class TestTickerRefreshIsInTheChain:
    """Added 2026-09-01. `run_tickers_refresh` was reachable only through
    `cscan tickers --refresh`, so a new S&P 500 constituent entered the
    system when someone remembered a command -- `runs` showed it last
    executing 2026-08-25, and before that 2026-08-01.

    It compounded with a cache defect: all three seed fetchers keyed on a
    constant string, so even the hand-run command replayed a 2026-07-31
    snapshot. Both halves are fixed; this pins the scheduling half.
    """

    def test_nightly_refreshes_the_ticker_seed(self) -> None:
        import inspect

        from capitalscan.jobs import cli

        assert "run_tickers_refresh" in inspect.getsource(cli.nightly)

    def test_it_runs_before_the_ticker_list_is_resolved(self) -> None:
        """A ticker added after `_resolve_tickers` waits a full day for its
        first bar, which defeats the point of scheduling this at all."""
        import inspect

        from capitalscan.jobs import cli

        src = inspect.getsource(cli.nightly)
        assert src.index("run_tickers_refresh") < src.index("tickers = _resolve_tickers(None)")

    def test_a_failure_does_not_fail_the_chain(self) -> None:
        """Wikipedia is a scrape and nothing downstream depends on it. A
        night that cannot reach it should ingest the universe it already
        knows -- which is what every night before 2026-09-01 did."""
        import inspect

        from capitalscan.jobs import cli

        src = inspect.getsource(cli.nightly)
        head = src[: src.index("tickers = _resolve_tickers(None)")]
        block = head[head.index("run_tickers_refresh") :]
        assert "except Exception" in head, "the refresh must be wrapped"
        assert "raise" not in block.split("except Exception")[1].split("tickers =")[0]

    def test_it_does_not_call_run_universe(self) -> None:
        """The safety property against ADR 060. `tickers` is a reference
        table; membership is decided by `run_universe`. Refreshing the seed
        makes a name *eligible* to be evaluated at the next universe pass
        rather than entering the traded set overnight.
        """
        import inspect

        from capitalscan.jobs import cli

        # Comment lines stripped first: the word appears in the explanation
        # of why the call is absent, and matching the bare word failed
        # against correct code -- the same trap that caught
        # `test_nightly_is_not_short_circuited` on the word "return".
        code = [
            line
            for line in inspect.getsource(cli.nightly).splitlines()
            if not line.lstrip().startswith("#")
        ]
        assert "run_universe(" not in chr(10).join(code)
