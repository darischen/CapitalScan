"""As-of membership resolved once per ticker, not once per bar.

`in_trade` and `in_watch` each masked the whole `universe` frame and sorted
it, and `run_events` called both for every bar. Measured 2026-09-22 on
`capitalscan_hist`: 106,275 universe rows scanned twice per bar, which is
why `cscan events` ran at one core for hours with Postgres idle (BACKLOG,
the `cscan events` item).

`membership_for` resolves one ticker's timeline once; `in_trade` and
`in_watch` now delegate to it, so there is still ONE implementation of
"which population was this ticker in on this date" and the fail-closed
contract (ADR 129) is unchanged: absent evidence is not membership.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core import universe as cu

Q = [
    ("AAPL", date(2026, 3, 31), True, False),
    ("AAPL", date(2026, 6, 30), False, True),
    ("MSFT", date(2026, 3, 31), False, False),
]


def _flags(rows=Q) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "as_of", "in_trade", "in_watch"])


class TestItMatchesTheSingleDateFunctions:
    @pytest.mark.parametrize(
        ("ticker", "when"),
        [
            ("AAPL", date(2026, 3, 30)),  # before any evaluation
            ("AAPL", date(2026, 3, 31)),  # exactly on one
            ("AAPL", date(2026, 5, 1)),  # between two
            ("AAPL", date(2026, 6, 30)),  # on the later one
            ("AAPL", date(2026, 9, 1)),  # after the last
            ("MSFT", date(2026, 5, 1)),  # evaluated, not a member
            ("NVDA", date(2026, 5, 1)),  # never evaluated
        ],
    )
    def test_same_answer_as_in_trade_and_in_watch(self, ticker: str, when: date) -> None:
        flags = _flags()
        m = cu.membership_for(flags, ticker)
        assert m.in_trade_at(when) == cu.in_trade(flags, ticker, when)
        assert m.in_watch_at(when) == cu.in_watch(flags, ticker, when)


class TestTheFailClosedContract:
    def test_a_ticker_never_evaluated_is_not_a_member(self) -> None:
        """ADR 129: defaulting to True admitted 18,805 training events on
        566 tickers that were never evaluated."""
        m = cu.membership_for(_flags(), "NVDA")
        assert m.in_trade_at(date(2026, 5, 1)) is False
        assert m.in_watch_at(date(2026, 5, 1)) is False

    def test_a_date_before_the_first_evaluation_is_not_a_member(self) -> None:
        m = cu.membership_for(_flags(), "AAPL")
        assert m.in_trade_at(date(2020, 1, 1)) is False

    def test_a_null_watch_flag_reads_as_not_watched(self) -> None:
        """NULL on rows written before ADR 149's migration."""
        flags = _flags([("AAPL", date(2026, 3, 31), True, None)])
        assert cu.membership_for(flags, "AAPL").in_watch_at(date(2026, 4, 1)) is False


class TestItTakesTheLatestEvaluationOnOrBeforeTheDate:
    def test_the_later_quarter_wins(self) -> None:
        m = cu.membership_for(_flags(), "AAPL")
        assert m.in_trade_at(date(2026, 5, 1)) is True  # only Q1 is in force
        assert m.in_trade_at(date(2026, 7, 1)) is False  # Q2 has superseded it
        assert m.in_watch_at(date(2026, 7, 1)) is True

    def test_duplicate_as_of_rows_resolve_like_the_single_date_function(self) -> None:
        """Two rows for one quarter: both paths must pick the same one."""
        flags = _flags(
            [
                ("AAPL", date(2026, 3, 31), False, False),
                ("AAPL", date(2026, 3, 31), True, False),
            ]
        )
        when = date(2026, 4, 1)
        m = cu.membership_for(flags, "AAPL")
        assert m.in_trade_at(when) == cu.in_trade(flags, "AAPL", when)

    def test_unsorted_input_is_handled(self) -> None:
        flags = _flags([Q[1], Q[0]])
        m = cu.membership_for(flags, "AAPL")
        assert m.in_trade_at(date(2026, 5, 1)) is True
        assert m.in_trade_at(date(2026, 7, 1)) is False


class TestItIsResolvedOnce:
    def test_the_frame_is_scanned_once_not_once_per_query(self) -> None:
        """The point of the change: one pass over the frame per ticker, then
        cheap lookups. A regression to per-call masking would scan again."""
        flags = _flags()
        scans = {"n": 0}
        real_eq = pd.Series.__eq__

        def counting_eq(self, other):  # noqa: ANN001, ANN202
            scans["n"] += 1
            return real_eq(self, other)

        pd.Series.__eq__ = counting_eq  # type: ignore[method-assign]
        try:
            m = cu.membership_for(flags, "AAPL")
            after_build = scans["n"]
            for day in pd.date_range("2026-04-01", periods=50):
                m.in_trade_at(day.date())
                m.in_watch_at(day.date())
        finally:
            pd.Series.__eq__ = real_eq  # type: ignore[method-assign]
        assert scans["n"] == after_build, "lookups must not touch the frame again"
