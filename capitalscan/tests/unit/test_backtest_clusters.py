"""Tests for `research/candidates.tag_clusters` — DESIGN §5.3, ADR 056
(Session 9, Task 4).

Controller Ruling C5: this tagger differs from the shipped v1 tagger at
`capitalscan.jobs.compute._tag_clusters` in exactly one respect —
`days_since_head` and the cluster-break gap test are measured in TRADING
BARS here, not calendar days, because `ExitParams.max_hold_days` counts
forward bars. It matches that function in two others: clusters key on
`(ticker, side)`, never ticker alone, and `cluster_id` is a deterministic
hash of `(ticker, side, head_date)`, never a row counter.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.research.candidates import tag_clusters


def _candidate_row(**overrides) -> dict:
    row = {
        "ticker": "TSM",
        "signal_date": date(2026, 7, 30),
        "signal_type": "confluence_low",
        "signal_types_all": ["confluence_low"],
        "signal_strength": 1,
        "side": "long",
        "touch_level": 95.0,
    }
    row.update(overrides)
    return row


class TestTagClusters:
    def test_two_events_within_max_hold_days_share_a_cluster(self):
        """Two long TSM events one trading bar apart, max_hold_days=5:
        second event is well inside the window, so both join one cluster,
        seq 1 and 2, and only seq 1 is a head."""
        candidates = pd.DataFrame(
            [
                _candidate_row(signal_date=date(2026, 7, 27)),
                _candidate_row(signal_date=date(2026, 7, 28)),
            ]
        )
        trading_dates = {
            "TSM": [date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29)],
        }

        out = tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

        out = out.sort_values("signal_date").reset_index(drop=True)
        assert out.loc[0, "cluster_id"] == out.loc[1, "cluster_id"]
        assert out.loc[0, "seq_in_cluster"] == 1
        assert out.loc[1, "seq_in_cluster"] == 2
        assert bool(out.loc[0, "is_cluster_head"]) is True
        assert bool(out.loc[1, "is_cluster_head"]) is False

    def test_events_further_apart_than_max_hold_days_get_distinct_clusters(self):
        """Two long TSM events fourteen trading bars apart (July 1 to July
        15, one trading date per calendar day in the fixture below, so the
        bar count matches the calendar-day count here), max_hold_days=5:
        both are heads of their own cluster."""
        trading_dates_list = [date(2026, 7, d) for d in range(1, 32)]
        candidates = pd.DataFrame(
            [
                _candidate_row(signal_date=date(2026, 7, 1)),
                _candidate_row(signal_date=date(2026, 7, 15)),
            ]
        )
        trading_dates = {"TSM": trading_dates_list}

        out = tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

        out = out.sort_values("signal_date").reset_index(drop=True)
        assert out.loc[0, "cluster_id"] != out.loc[1, "cluster_id"]
        assert out.loc[0, "seq_in_cluster"] == 1
        assert out.loc[1, "seq_in_cluster"] == 1
        assert bool(out.loc[0, "is_cluster_head"]) is True
        assert bool(out.loc[1, "is_cluster_head"]) is True

    def test_two_tickers_never_share_a_cluster_even_on_identical_dates(self):
        """Same signal_date, same side, different ticker: must not merge
        into one cluster even though `(ticker, side)` keying would allow
        it if the ticker were ignored."""
        candidates = pd.DataFrame(
            [
                _candidate_row(ticker="TSM", signal_date=date(2026, 7, 27)),
                _candidate_row(ticker="AAPL", signal_date=date(2026, 7, 27)),
            ]
        )
        trading_dates = {
            "TSM": [date(2026, 7, 27)],
            "AAPL": [date(2026, 7, 27)],
        }

        out = tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

        assert out["cluster_id"].nunique() == 2
        assert (out["seq_in_cluster"] == 1).all()
        assert out["is_cluster_head"].all()

    def test_long_and_short_on_one_ticker_never_share_a_cluster(self):
        """`(ticker, side)` keying, matching compute.py:552-556 — a long
        cluster and a short cluster on one ticker are different positions."""
        candidates = pd.DataFrame(
            [
                _candidate_row(side="long", signal_date=date(2026, 7, 27)),
                _candidate_row(side="short", signal_date=date(2026, 7, 27)),
            ]
        )
        trading_dates = {"TSM": [date(2026, 7, 27)]}

        out = tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

        assert out["cluster_id"].nunique() == 2
        assert out["is_cluster_head"].all()

    def test_days_since_head_counts_trading_bars_not_calendar_days(self):
        """Friday 2026-07-24 to Monday 2026-07-27 spans a weekend: 3
        calendar days but 1 trading bar (Sat/Sun are not trading dates for
        this ticker). With max_hold_days=1, a calendar-day gap test would
        split these into two clusters (3 > 1); the trading-bar gap test
        must keep them in one cluster (1 is not > 1), and `days_since_head`
        on the second event must read 1, never 3.
        """
        candidates = pd.DataFrame(
            [
                _candidate_row(signal_date=date(2026, 7, 24)),  # Friday
                _candidate_row(signal_date=date(2026, 7, 27)),  # Monday
            ]
        )
        # No Saturday/Sunday entries: this ticker traded on Fri and Mon only
        # in this window.
        trading_dates = {"TSM": [date(2026, 7, 24), date(2026, 7, 27)]}

        out = tag_clusters(candidates, max_hold_days=1, trading_dates=trading_dates)

        out = out.sort_values("signal_date").reset_index(drop=True)
        # Same cluster: the trading-bar gap (1) does not exceed max_hold_days.
        assert out.loc[0, "cluster_id"] == out.loc[1, "cluster_id"]
        assert out.loc[0, "seq_in_cluster"] == 1
        assert out.loc[1, "seq_in_cluster"] == 2
        assert out.loc[0, "days_since_head"] == 0
        assert out.loc[1, "days_since_head"] == 1  # not 3 (calendar days)

    def test_cluster_id_is_deterministic_across_reruns(self):
        """ADR 060: the same input must produce the same cluster_id every
        run — no row counter, no uuid."""
        candidates = pd.DataFrame(
            [
                _candidate_row(signal_date=date(2026, 7, 27)),
                _candidate_row(signal_date=date(2026, 7, 28)),
            ]
        )
        trading_dates = {
            "TSM": [date(2026, 7, 27), date(2026, 7, 28)],
        }

        out1 = tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)
        out2 = tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

        pd.testing.assert_series_equal(
            out1["cluster_id"].reset_index(drop=True),
            out2["cluster_id"].reset_index(drop=True),
        )

    def test_empty_candidates_returns_empty_frame_with_new_columns(self):
        candidates = pd.DataFrame(columns=[
            "ticker", "signal_date", "signal_type", "signal_types_all",
            "signal_strength", "side", "touch_level",
        ])

        out = tag_clusters(candidates, max_hold_days=5, trading_dates={})

        assert out.empty
        assert "cluster_id" in out.columns
        assert "seq_in_cluster" in out.columns
        assert "is_cluster_head" in out.columns
        assert "days_since_head" in out.columns

    def test_does_not_mutate_the_input_frame(self):
        candidates = pd.DataFrame(
            [_candidate_row(signal_date=date(2026, 7, 27))]
        )
        trading_dates = {"TSM": [date(2026, 7, 27)]}
        original_columns = list(candidates.columns)

        tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

        assert list(candidates.columns) == original_columns


class TestTagClustersRaisesOnInputMismatch:
    """Code review finding: `sorted_dates.get(ticker, [])` used to default
    silently to an empty list for a ticker missing from `trading_dates`.
    With an empty date list, `_trading_bars_between` always returns 0, so
    the gap test never fires and every candidate for that ticker collapses
    into one endless cluster with `days_since_head == 0` throughout — a
    plausible-looking wrong answer, not an error. Both checks below must
    raise instead of silently degrading."""

    def test_a_ticker_missing_from_trading_dates_raises(self):
        candidates = pd.DataFrame(
            [
                _candidate_row(ticker="TSM", signal_date=date(2026, 7, 1)),
                _candidate_row(ticker="TSM", signal_date=date(2026, 7, 20)),
            ]
        )
        # trading_dates has no "TSM" entry at all.
        trading_dates: dict = {}

        with pytest.raises(ValueError, match="TSM"):
            tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

    def test_a_ticker_with_an_empty_trading_dates_list_raises(self):
        candidates = pd.DataFrame(
            [_candidate_row(ticker="TSM", signal_date=date(2026, 7, 1))]
        )
        trading_dates = {"TSM": []}

        with pytest.raises(ValueError, match="TSM"):
            tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)

    def test_a_signal_date_absent_from_the_tickers_trading_dates_raises(self):
        """The ticker has an entry, but it does not cover the candidate's
        own signal_date — a partial mismatch, same failure class as a
        wholly missing ticker."""
        candidates = pd.DataFrame(
            [_candidate_row(ticker="TSM", signal_date=date(2026, 7, 4))]
        )
        # TSM traded on the 1st and the 8th in this fixture, never the 4th.
        trading_dates = {"TSM": [date(2026, 7, 1), date(2026, 7, 8)]}

        with pytest.raises(ValueError, match="2026-07-04"):
            tag_clusters(candidates, max_hold_days=5, trading_dates=trading_dates)
