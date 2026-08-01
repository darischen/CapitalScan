"""Unit tests for the pre-window universe cut (ADR 035, ADR 055).

ADR 035 keeps delisted and removed names in the union on purpose. The
only names that may leave are those whose entire index tenure closed
before `SplitParams.event_start`, since they contribute no events. These
tests pin that boundary from both sides.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.config import SplitParams
from capitalscan.jobs.ingest import drop_pre_window_tickers

EVENT_START = SplitParams().event_start


def _union(*records: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [{"added": None, "removed": None, "needs_review": True, **r} for r in records]
    )


class TestDropPreWindowTickers:
    def test_drops_a_ticker_removed_before_the_window(self):
        df = _union({"ticker": "ABS", "removed": "July 1, 1998"})
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == ["ABS"]
        assert kept.empty

    def test_keeps_a_ticker_removed_inside_the_window(self):
        """The survivorship guard. A 2015 delisting is exactly what ADR 035 keeps."""
        df = _union({"ticker": "AKS", "removed": "March 13, 2020"})
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert list(kept["ticker"]) == ["AKS"]

    def test_keeps_a_current_member_regardless_of_old_dates(self):
        df = _union(
            {"ticker": "MMM", "needs_review": False},
            {"ticker": "MMM", "removed": "January 2, 1990"},
        )
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert set(kept["ticker"]) == {"MMM"}

    def test_keeps_a_ticker_that_left_and_came_back(self):
        """Pre-2010 exit, post-2010 re-entry: its max date reaches the window."""
        df = _union(
            {"ticker": "XYZ", "removed": "June 1, 2004"},
            {"ticker": "XYZ", "added": "August 3, 2018"},
        )
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert len(kept) == 2

    def test_keeps_a_ticker_with_no_observed_removal(self):
        """Wikipedia logs *selected* changes; an unrecorded exit is not proof of one."""
        df = _union({"ticker": "OLD", "added": "May 4, 2005"})
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert list(kept["ticker"]) == ["OLD"]

    def test_keeps_a_ticker_whose_date_will_not_parse(self):
        """ADR 055 expects malformed older rows. Ambiguity means review, not delete."""
        df = _union({"ticker": "BAD", "removed": "sometime in the 90s"})
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert list(kept["ticker"]) == ["BAD"]

    def test_boundary_date_is_inclusive_of_the_window(self):
        """A removal exactly on `event_start` is inside the window."""
        df = _union({"ticker": "EDGE", "removed": EVENT_START})
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert list(kept["ticker"]) == ["EDGE"]

    def test_returns_a_new_frame_and_leaves_the_input_alone(self):
        df = _union(
            {"ticker": "ABS", "removed": "July 1, 1998"},
            {"ticker": "AKS", "removed": "March 13, 2020"},
        )
        before = df.copy()
        kept, _ = drop_pre_window_tickers(df, EVENT_START)

        assert len(kept) == 1
        pd.testing.assert_frame_equal(df, before)


class TestSurvivorshipInvariant:
    def test_no_in_window_removal_is_ever_dropped(self):
        """Sweep a decade of exits; every one must survive the cut."""
        years = range(2010, 2026)
        df = _union(*[{"ticker": f"T{y}", "removed": f"June 15, {y}"} for y in years])
        kept, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == []
        assert len(kept) == len(list(years))

    @pytest.mark.parametrize("year", [1976, 1990, 2001, 2009])
    def test_pre_window_exits_are_all_dropped(self, year: int):
        df = _union({"ticker": "GONE", "removed": f"June 15, {year}"})
        _, dropped = drop_pre_window_tickers(df, EVENT_START)

        assert dropped == ["GONE"]
