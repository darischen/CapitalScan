"""Nightly refetches session bars Yahoo had not published at 13:15 (ADR 202).

On 2026-09-28 the first `bars_daily` pass left 63 of 1,454 tickers without
the day's bar. Nothing refetched them until the next nightly, after the next
session's poller had read t-2 bands as t-1.
"""

import inspect
from pathlib import Path

from capitalscan.jobs import cli, ingest


def _nightly() -> str:
    return inspect.getsource(cli.nightly)


class TestCatchupPlacement:
    def test_it_runs_after_the_first_bars_pass(self) -> None:
        src = _nightly()
        first = src.index("ingest.run_bars_daily(tickers")
        assert first < src.index("tickers_missing_session(")

    def test_it_runs_before_indicators(self) -> None:
        """An indicator row is only written for a bar that exists."""
        src = _nightly()
        assert src.index("tickers_missing_session(") < src.index("compute.run_indicators(")

    def test_it_refetches_only_the_missing(self) -> None:
        assert "ingest.run_bars_daily(missing" in _nightly()

    def test_it_only_runs_on_a_trading_day(self) -> None:
        """A weekend has no session bar to be missing."""
        src = _nightly()
        lines = src.splitlines()
        call = next(i for i, ln in enumerate(lines) if "tickers_missing_session(" in ln)
        assert lines[call - 1].strip() == "if trading_day:"


class TestMissingSessionQuery:
    def test_empty_input_touches_no_database(self) -> None:
        class _Boom:
            def connect(self):
                raise AssertionError("queried with no tickers")

        assert ingest.tickers_missing_session(_Boom(), [], None) == []  # type: ignore[arg-type]

    def test_the_denominator_is_yesterdays_bar(self) -> None:
        """A dead symbol has no bar on either day and is not 'missing'."""
        src = inspect.getsource(ingest.tickers_missing_session)
        assert "max(d) FROM trading_days WHERE d < :s" in src
        assert "NOT EXISTS" in src


class TestRunJobLogIsAppended:
    def test_the_wrapper_never_truncates_the_days_log(self) -> None:
        """The 19:00 skip emptied the 13:15 run's log every night."""
        sh = Path(__file__).resolve().parents[3] / "scripts" / "run_job.sh"
        code = [ln for ln in sh.read_text().splitlines() if not ln.lstrip().startswith("#")]
        assert not any(ln.strip().startswith(': > "$LOG"') for ln in code)
