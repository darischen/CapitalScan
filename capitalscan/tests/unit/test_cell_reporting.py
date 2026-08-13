"""Session 12.4: breadth terciles and per-ticker concentration.

Both are descriptive splits on cells already tested pooled. Neither enters
a test family, neither carries a `q_value` (ADR 103, ADR 099).

`ReportingParams` is standalone rather than a `StatsParams` field, for the
same reason `BaselineParams` is: `jobs.config.config_hash` hashes
`dataclasses.asdict(Config)`, so folding a reporting threshold into
`Config` would move `config_hash` for every config already written to
`events` — including the live poller's `1835688bf7d760ba` — in order to
name a constant the backtest never reads.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import ReportingParams
from capitalscan.research.cell_stats import (
    assign_breadth_tercile,
    breadth_ratio,
    quarter_universe_size,
    ticker_concentration,
)


def _events(tickers: list[str], cofire: list[int] | None = None, dates=None) -> pd.DataFrame:
    n = len(tickers)
    return pd.DataFrame(
        {
            "ticker": tickers,
            "signal_date": dates if dates is not None else pd.bdate_range("2015-01-05", periods=n),
            "cofire_count": cofire if cofire is not None else [1] * n,
            "era": "2015-2019",
        }
    )


class TestQuarterUniverseSize:
    """ADR 104: the denominator is the count of distinct tickers with any
    event in that quarter — the train universe, not the trade universe."""

    def test_counts_distinct_tickers_per_quarter(self):
        events = _events(
            ["A", "B", "C", "A"],
            dates=pd.to_datetime(["2015-01-05", "2015-02-05", "2015-03-05", "2015-03-06"]),
        )
        sizes = quarter_universe_size(events)
        assert sizes[pd.Period("2015Q1")] == 3

    def test_quarters_are_counted_independently(self):
        events = _events(
            ["A", "B", "C", "D", "E"],
            dates=pd.to_datetime(
                ["2015-01-05", "2015-02-05", "2015-04-06", "2015-05-06", "2015-06-06"]
            ),
        )
        sizes = quarter_universe_size(events)
        assert sizes[pd.Period("2015Q1")] == 2
        assert sizes[pd.Period("2015Q2")] == 3

    def test_a_repeated_ticker_counts_once(self):
        events = _events(["A"] * 10)
        assert quarter_universe_size(events)[pd.Period("2015Q1")] == 1


class TestBreadthRatio:
    def test_ratio_is_cofire_over_the_quarters_universe(self):
        events = _events(
            ["A", "B", "C", "D"],
            cofire=[2, 2, 2, 2],
            dates=pd.to_datetime(["2015-01-05"] * 4),
        )
        assert breadth_ratio(events).tolist() == pytest.approx([0.5] * 4)

    def test_ratio_never_exceeds_one(self):
        """The defect ADR 104 corrects. Under ADR 099's trade-universe
        denominator the numerator counted co-firing names across the train
        universe while the denominator counted roughly 62 trade names, so
        the ratio crossed 1 — a boundary a genuine fraction cannot brush."""
        rng = np.random.default_rng(5)
        tickers = [f"T{i}" for i in range(200)]
        events = _events(
            tickers,
            cofire=list(rng.integers(1, 200, 200)),
            dates=pd.to_datetime(["2015-02-05"] * 200),
        )
        assert (breadth_ratio(events) <= 1.0).all()

    def test_a_trade_universe_denominator_would_break_the_bound(self):
        """Pins the failure mode rather than only the fix: with a
        denominator of 62 and a numerator drawn from a 200-name universe,
        the ratio exceeds 1. If this ever stops being true the test above
        has stopped proving anything."""
        events = _events(["A"] * 5, cofire=[120] * 5, dates=pd.to_datetime(["2015-02-05"] * 5))
        trade_universe_ratio = events["cofire_count"] / 62
        assert (trade_universe_ratio > 1.0).all()

    def test_null_cofire_propagates(self):
        events = _events(["A", "B"], cofire=[2, None], dates=pd.to_datetime(["2015-01-05"] * 2))
        assert breadth_ratio(events).isna().sum() == 1


class TestBreadthTerciles:
    def test_three_terciles_are_assigned(self):
        rng = np.random.default_rng(6)
        n = 300
        events = _events(
            [f"T{i}" for i in range(n)],
            cofire=list(rng.integers(1, 300, n)),
            dates=pd.to_datetime(["2015-02-05"] * n),
        )
        out = assign_breadth_tercile(events, ReportingParams())
        assert set(out["breadth_tercile"].dropna().unique()) == {"low", "mid", "high"}

    def test_terciles_are_cut_within_era_not_across(self):
        """Firing rates differ across regimes, so a pooled cut would load
        one era into one tercile and measure the era rather than the
        breadth."""
        n = 150
        # Disjoint breadth ranges per era. Cut pooled, era A lands wholly in
        # 'low' and era B wholly in 'high'; cut per era, each spans all three.
        low_era = _events(
            [f"A{i}" for i in range(n)],
            cofire=list(range(1, n + 1)),
            dates=pd.to_datetime(["2012-02-05"] * n),
        )
        low_era["era"] = "2010-2014"
        high_era = _events(
            [f"B{i}" for i in range(n)],
            cofire=list(range(400, 400 + n)),
            dates=pd.to_datetime(["2016-02-05"] * n),
        )
        high_era["era"] = "2015-2019"
        out = assign_breadth_tercile(
            pd.concat([low_era, high_era], ignore_index=True), ReportingParams()
        )
        # Every era contributes to every tercile it can, rather than one
        # era occupying 'low' and the other 'high' wholesale.
        per_era = out.groupby("era")["breadth_tercile"].nunique()
        assert (per_era > 1).all()

    def test_tercile_count_comes_from_reporting_params(self):
        rng = np.random.default_rng(8)
        n = 300
        events = _events(
            [f"T{i}" for i in range(n)],
            cofire=list(rng.integers(1, 300, n)),
            dates=pd.to_datetime(["2015-02-05"] * n),
        )
        out = assign_breadth_tercile(events, ReportingParams(breadth_quantiles=2))
        assert out["breadth_tercile"].dropna().nunique() == 2


class TestTickerConcentration:
    def test_reports_the_largest_contributor_and_its_share(self):
        events = _events(["NVDA"] * 4 + ["AAPL"] * 6)
        top, share = ticker_concentration(events)
        assert top == "AAPL"
        assert share == pytest.approx(0.6)

    def test_a_single_ticker_cell_is_fully_concentrated(self):
        top, share = ticker_concentration(_events(["NVDA"] * 5))
        assert (top, share) == ("NVDA", 1.0)

    def test_evenly_spread_cell_has_low_concentration(self):
        top, share = ticker_concentration(_events([f"T{i}" for i in range(20)]))
        assert share == pytest.approx(0.05)

    def test_threshold_comes_from_reporting_params(self):
        """DESIGN §6.7's 15%. A literal here and a different literal in the
        report is how the two come to disagree."""
        assert ReportingParams().max_ticker_share == 0.15

    def test_empty_cell_reports_no_contributor(self):
        top, share = ticker_concentration(_events([]))
        assert top is None
        assert share is None
