"""Nightly runs a reduced pass on a non-trading day (2026-09-01).

Raised by the user 2026-08-30 after seeing a nightly terminal open at 13:15
on a Sunday.

**The poller's guard does not transfer.** `wait_and_poll.sh` exits entirely
on a closed market because polling one is a *correctness* failure: signals
computed off the previous session's stale quotes, written to the store the
site serves, plus a `poller_sessions` row that pollutes ADR 084's
`coverage_pct`. Nightly has no equivalent -- every step is idempotent.

So the cost is waste, not wrongness, and the guard must not buy it by
breaking the catch-up path: nightly's 7-day lookback is what repairs a
Friday failure on Saturday.
"""

import inspect

from capitalscan.jobs import cli


class TestTradingDayLookup:
    def test_it_reads_the_calendar_not_the_weekday(self) -> None:
        """A holiday is a non-trading day that falls on a weekday."""
        src = inspect.getsource(cli._is_trading_day)
        assert "trading_days" in src
        for weekday_test in ("weekday()", "isoweekday()", "== 5", "== 6"):
            assert weekday_test not in src

    def test_it_fails_open_on_an_unreachable_calendar(self) -> None:
        """The guard avoids waste, so its own failure must cost waste.

        A nightly that silently stops running is the failure this whole
        area is about.
        """

        class _Boom:
            def connect(self):
                raise RuntimeError("no database")

        assert cli._is_trading_day(_Boom(), None) is True

    def test_it_fails_open_on_an_empty_calendar(self) -> None:
        """An empty `trading_days` is not evidence of a closed market."""

        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

            def scalar_one(self):
                return self._value

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, stmt, params=None):
                # first call: the date lookup misses; second: the count is 0
                return _Result(None) if params else _Result(0)

        class _Engine:
            def connect(self):
                return _Conn()

        assert cli._is_trading_day(_Engine(), None) is True


class TestReducedPassNotBlanketSkip:
    def test_only_the_price_fetchers_are_gated(self) -> None:
        """Only these three provably have nothing to fetch on a closed day."""
        src = inspect.getsource(cli.nightly)
        gated = src[src.index("if trading_day:") : src.index("ingest.run_actions(")]
        assert "run_bars_daily" in gated
        assert "run_bars_hourly" in gated
        assert "run_market" in gated

    def test_the_catch_up_path_still_runs(self) -> None:
        """The repair path must not be behind the guard.

        `actions` and `earnings` because corporate actions and calendar
        revisions land on non-trading days; the recompute and the sync
        because they are what fixes a failed Friday.
        """
        src = inspect.getsource(cli.nightly)
        gated = src[src.index("if trading_day:") : src.index("ingest.run_actions(")]
        for always in ("run_actions", "run_earnings", "run_events", "run_sync", "run_indicators"):
            assert always not in gated, f"{always} must run on a non-trading day too"

    def test_nightly_is_not_short_circuited(self) -> None:
        """No early exit: a blanket skip is the thing this rejects.

        Matched on statements rather than substrings -- the first version
        of this test searched for the word "return" and caught the phrase
        "returns NaN" in a comment.
        """
        src = inspect.getsource(cli.nightly)
        head = src[: src.index("ingest.run_actions(")]
        statements = [line.strip() for line in head.splitlines()]
        assert not [s for s in statements if s.startswith(("return", "raise typer.Exit"))]
