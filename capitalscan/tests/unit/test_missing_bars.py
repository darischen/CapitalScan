"""Unit tests for `jobs.ingest.find_missing_bars` (DESIGN §2.3's missing-bar
rule, DESIGN §4.3's silent-truncation warning). Pure function, no DB or
network — same pattern as `test_validate_bars.py`.
"""

from __future__ import annotations

import pandas as pd

from capitalscan.jobs.ingest import find_missing_bars


def _trading_days(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"d": pd.to_datetime(dates)})


def _bars(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ticker": t, "d": pd.Timestamp(d)} for t, d in rows]
    )


class TestNoGaps:
    def test_full_coverage_has_no_missing_bars(self):
        trading_days = _trading_days(
            ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        )
        bars = _bars(
            [
                ("TSM", "2020-01-02"),
                ("TSM", "2020-01-03"),
                ("TSM", "2020-01-06"),
                ("TSM", "2020-01-07"),
            ]
        )
        result = find_missing_bars(bars, trading_days)
        assert result.empty

    def test_empty_bars_frame_returns_empty(self):
        trading_days = _trading_days(["2020-01-02", "2020-01-03"])
        bars = pd.DataFrame(columns=["ticker", "d"])
        result = find_missing_bars(bars, trading_days)
        assert result.empty


class TestGenuineGap:
    def test_trading_day_inside_span_with_no_bar_is_flagged(self):
        # 01-03 is a trading day inside TSM's [01-02, 01-07] span but has no bar.
        trading_days = _trading_days(
            ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        )
        bars = _bars([("TSM", "2020-01-02"), ("TSM", "2020-01-06"), ("TSM", "2020-01-07")])
        result = find_missing_bars(bars, trading_days)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "TSM"
        assert result.iloc[0]["missing_date"] == pd.Timestamp("2020-01-03").date()


class TestExpectedAbsence:
    def test_trading_days_before_first_bar_are_not_flagged(self):
        # Ticker listed 2020-01-06; earlier trading days are pre-listing, not a gap.
        trading_days = _trading_days(
            ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        )
        bars = _bars([("NEWCO", "2020-01-06"), ("NEWCO", "2020-01-07")])
        result = find_missing_bars(bars, trading_days)
        assert result.empty

    def test_trading_days_after_last_bar_are_not_flagged(self):
        # Ticker delisted after 2020-01-03; later trading days are post-delisting.
        trading_days = _trading_days(
            ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        )
        bars = _bars([("DEADCO", "2020-01-02"), ("DEADCO", "2020-01-03")])
        result = find_missing_bars(bars, trading_days)
        assert result.empty


class TestMultiTicker:
    def test_tickers_are_checked_independently(self):
        trading_days = _trading_days(
            ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        )
        bars = _bars(
            [
                ("TSM", "2020-01-02"),
                ("TSM", "2020-01-03"),
                ("TSM", "2020-01-06"),
                ("TSM", "2020-01-07"),
                ("NVDA", "2020-01-02"),
                ("NVDA", "2020-01-07"),  # missing 01-03 and 01-06
            ]
        )
        result = find_missing_bars(bars, trading_days)
        assert set(result["ticker"]) == {"NVDA"}
        assert len(result) == 2
