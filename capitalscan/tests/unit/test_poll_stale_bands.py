"""The poller skips a ticker whose newest indicator row is not t-1 (ADR 202).

`_load_indicator_rows` returns the newest row per ticker. That row is t-1
only when last night's settled bar arrived. When it did not, the newest row
is t-2 and the poller compared live price against a band one session old.

The case this pins: ADI on 2026-09-28 read its 2026-09-24 row, reported a
confirmed bear reversal at 392.07 against a 389.24 band, and the correct
2026-09-25 band was 392.83. Price was inside the band.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime

import pandas as pd
import pytest

from capitalscan.jobs import poll as poll_job


def _row(ticker: str, ts: datetime) -> pd.Series:
    return pd.Series({"ticker": ticker, "ts": pd.Timestamp(ts, tz="UTC"), "bb_upper": 1.0})


class TestDropStaleIndicatorRows:
    def test_the_adi_case_is_skipped(self) -> None:
        rows = {"ADI": _row("ADI", datetime(2026, 9, 24))}
        kept, stale = poll_job.drop_stale_indicator_rows(rows, date(2026, 9, 25))
        assert kept == {}
        assert stale == ["ADI"]

    def test_a_t_minus_1_row_is_kept(self) -> None:
        rows = {"AAPL": _row("AAPL", datetime(2026, 9, 25))}
        kept, stale = poll_job.drop_stale_indicator_rows(rows, date(2026, 9, 25))
        assert list(kept) == ["AAPL"]
        assert stale == []

    def test_a_row_newer_than_expected_is_kept(self) -> None:
        """Ahead of the calendar is `assert_target_is_current`'s question."""
        rows = {"AAPL": _row("AAPL", datetime(2026, 9, 26))}
        kept, stale = poll_job.drop_stale_indicator_rows(rows, date(2026, 9, 25))
        assert list(kept) == ["AAPL"]
        assert stale == []

    def test_mixed_population_splits_and_sorts(self) -> None:
        rows = {
            "TPR": _row("TPR", datetime(2026, 9, 24)),
            "AAPL": _row("AAPL", datetime(2026, 9, 25)),
            "ADI": _row("ADI", datetime(2026, 9, 24)),
        }
        kept, stale = poll_job.drop_stale_indicator_rows(rows, date(2026, 9, 25))
        assert list(kept) == ["AAPL"]
        assert stale == ["ADI", "TPR"]

    def test_naive_timestamps_compare_by_date(self) -> None:
        rows = {"ADI": pd.Series({"ts": pd.Timestamp("2026-09-24")})}
        _, stale = poll_job.drop_stale_indicator_rows(rows, date(2026, 9, 25))
        assert stale == ["ADI"]


class TestRunPollAppliesTheGuard:
    def test_bands_are_built_only_from_fresh_rows(self) -> None:
        """The guard must sit between the load and the band build.

        A guard computed and then ignored is the failure mode, so the order
        is asserted on the source rather than trusted to review.
        """
        src = inspect.getsource(poll_job.run_poll)
        assert src.index("drop_stale_indicator_rows(") < src.index("_bands_from(row)")
        assert "_previous_trading_day(engine, session_date)" in src


class TestPreviousTradingDayFailsClosed:
    def test_an_empty_calendar_raises(self) -> None:
        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class _R:
                    def scalar_one_or_none(self):
                        return None

                return _R()

        class _Engine:
            def connect(self):
                return _Conn()

        with pytest.raises(RuntimeError, match="freshness cannot be checked"):
            poll_job._previous_trading_day(_Engine(), date(2026, 9, 28))  # type: ignore[arg-type]
