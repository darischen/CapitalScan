"""Session 11.2 acceptance: per-ticker-year baselines (DESIGN §6.2, ADR 062).

Every expected value here is computed a second way — by explicit arithmetic
in the test, or from a published/derived closed form — never by calling the
function under test with different arguments. A baseline test that reuses
the implementation to build its own expectation proves only that the code is
consistent with itself, and the failure this session guards against
(lookahead in the trailing window) is exactly the kind that stays consistent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from capitalscan.core.baselines import (
    baseline_gap,
    disagreement_flag,
    empirical_baseline,
    event_weighted_baseline,
    horizon_drift_vol,
    parametric_baseline,
    trailing_drift_vol,
)
from capitalscan.core.config import DEFAULT_BASELINE, BaselineParams
from capitalscan.research.baselines import (
    attach_event_baselines,
    cell_baselines,
    ticker_year_baselines,
)

BP = DEFAULT_BASELINE
TRADING_DAYS = BP.trading_days_per_year


def _panel(ticker: str, closes: np.ndarray, start: str = "2010-01-04") -> pd.DataFrame:
    """A `bars`-shaped daily panel for one ticker."""
    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": pd.bdate_range(start, periods=len(closes), freq="C"),
            "adj_close": closes,
        }
    )


class TestParametricWorkedExample:
    """DESIGN §6.2's worked example, digit for digit."""

    def test_horizon_scaling(self):
        # mu_5d = mu_ann / 50.4, where 50.4 = 252 / 5.
        mu_5d, sigma_5d = horizon_drift_vol(0.30, 0.40, BP)
        assert abs(mu_5d - 0.30 / 50.4) < 1e-12
        assert abs(mu_5d - 0.0060) < 5e-5  # "mu_5d near 0.60%"
        assert abs(sigma_5d - 0.40 * np.sqrt(5 / 252)) < 1e-12
        assert abs(sigma_5d - 0.056) < 5e-4  # "sigma_5d near 5.6%"

    def test_reachability_with_and_without_drift(self):
        # P(R_5d >= 2%) at 30% drift / 40% vol is ~40.1%, versus ~36.1% at
        # zero drift: four points of baseline before any indicator fires.
        with_drift = parametric_baseline(0.02, 0.30, 0.40, BP)
        without_drift = parametric_baseline(0.02, 0.00, 0.40, BP)
        assert abs(with_drift - 0.401) < 0.001
        assert abs(without_drift - 0.361) < 0.001
        assert with_drift - without_drift > 0.03

    def test_degenerate_volatility(self):
        # Zero variance is a determinate outcome, not an error.
        assert parametric_baseline(0.02, 0.00, 0.0, BP) == 0.0
        assert parametric_baseline(0.02, 5.00, 0.0, BP) == 1.0

    def test_rejects_negative_sigma(self):
        with pytest.raises(ValueError):
            parametric_baseline(0.02, 0.0, -0.1, BP)


class TestTrailingWindowIsStrictlyPrior:
    """The lookahead guard. This is the failure that looks like an edge."""

    def test_observation_day_excluded_from_its_own_window(self):
        # 300 calm days, then one 20% jump. The jump day's own window must
        # not see the jump.
        rng = np.random.default_rng(11_02)
        daily = pd.Series(rng.normal(0.0, 0.005, 320))
        jump_at = 300
        daily.iloc[jump_at] = 0.20

        out = trailing_drift_vol(daily, BP)

        # Hand-built expectation: the 252 returns ending the day BEFORE.
        prior = daily.iloc[jump_at - 252 : jump_at]
        assert abs(out["mu_annual"].iloc[jump_at] - prior.mean() * TRADING_DAYS) < 1e-12
        assert (
            abs(out["sigma_annual"].iloc[jump_at] - prior.std(ddof=1) * np.sqrt(TRADING_DAYS))
            < 1e-12
        )

        # And the inclusive window is a visibly different number, so the
        # assertion above is not passing by coincidence.
        inclusive = daily.iloc[jump_at - 251 : jump_at + 1]
        assert inclusive.std(ddof=1) > 2 * prior.std(ddof=1)

    def test_short_history_is_null_not_shortened(self):
        daily = pd.Series(np.full(260, 0.001))
        out = trailing_drift_vol(daily, BP)
        # Index 252 is the first day with 252 strictly-prior returns
        # (indices 0..251). Everything before it is null, not a 40-day
        # estimate wearing a 252-day label.
        assert out["mu_annual"].iloc[:252].isna().all()
        assert not np.isnan(out["mu_annual"].iloc[252])

    def test_rejects_degenerate_window(self):
        with pytest.raises(ValueError):
            trailing_drift_vol(pd.Series([0.1, 0.2]), BaselineParams(trailing_window_days=1))


class TestEmpiricalBaselineHandVerified:
    """Three ticker-years checked against arithmetic done outside the module."""

    def _hand_fraction(self, closes: np.ndarray, target: float, horizon: int = 5) -> float:
        """The spreadsheet: for each day with a complete forward window,
        `close[t+h] / close[t] - 1`, then the fraction at or above target."""
        hits = 0
        total = 0
        for t in range(len(closes) - horizon):
            total += 1
            if closes[t + horizon] / closes[t] - 1.0 >= target:
                hits += 1
        return hits / total

    def test_three_ticker_years(self):
        rng = np.random.default_rng(20260810)
        cases = {
            "AAA": 100.0 * np.exp(np.cumsum(rng.normal(0.0008, 0.012, 260))),
            "BBB": 50.0 * np.exp(np.cumsum(rng.normal(-0.0005, 0.020, 260))),
            "CCC": 200.0 * np.exp(np.cumsum(rng.normal(0.0000, 0.008, 260))),
        }
        bars = pd.concat([_panel(t, c) for t, c in cases.items()], ignore_index=True)
        # One calendar year only, so the ticker-year is the whole series.
        bars = bars[bars["ts"].dt.year == 2010].reset_index(drop=True)

        out = ticker_year_baselines(bars, (0.03,), BP)
        for ticker, closes in cases.items():
            trimmed = closes[: (bars["ticker"] == ticker).sum()]
            expected = self._hand_fraction(trimmed, 0.03)
            got = out.loc[out["ticker"] == ticker, "baseline_empirical"].iloc[0]
            assert abs(got - expected) < 1e-12, ticker

    def test_split_spanning_year_reads_the_adjusted_series(self):
        # A 2:1 split mid-year. `adj_close` is continuous through it; raw
        # `close` halves. The baseline must be identical to the same series
        # with no split, because it reads the adjusted one.
        rng = np.random.default_rng(7)
        adj = 100.0 * np.exp(np.cumsum(rng.normal(0.0006, 0.014, 250)))
        split_at = 120
        raw = adj.copy()
        raw[:split_at] *= 2.0  # pre-split prints, as the market traded them

        bars = _panel("SPLT", adj)
        out = ticker_year_baselines(bars, (0.03,), BP)
        expected = self._hand_fraction(adj, 0.03)
        assert abs(out["baseline_empirical"].iloc[0] - expected) < 1e-12

        # What the raw series would have done, which is what makes the
        # price-series choice load-bearing rather than cosmetic: every
        # 5-day window straddling the split prints a ~-50% "return" that
        # never happened.
        def five_day(series: np.ndarray) -> np.ndarray:
            return series[5:] / series[:-5] - 1.0

        assert five_day(raw).min() < -0.45
        assert five_day(adj).min() > -0.45

    def test_no_complete_forward_window_is_null(self):
        assert empirical_baseline(pd.Series([np.nan, np.nan]), 0.03) == (None, 0)

    def test_counts_only_complete_windows(self):
        fwd = pd.Series([0.04, 0.01, np.nan, np.nan])
        value, n_obs = empirical_baseline(fwd, 0.03)
        assert (value, n_obs) == (0.5, 2)


class TestDisagreementFlag:
    """Fires on fat tails, quiet on Gaussian (11.2 acceptance)."""

    def _series(self, daily: np.ndarray) -> pd.DataFrame:
        return _panel("SYN", 100.0 * np.exp(np.cumsum(daily)))

    def test_gaussian_stays_quiet(self):
        rng = np.random.default_rng(101)
        # Two years: the second has a full trailing window from the first.
        daily = rng.normal(0.0, 0.012, 620)
        out = ticker_year_baselines(self._series(daily), (0.03,), BP)
        scored = out[out["baseline_parametric"].notna()]
        assert len(scored) >= 1
        assert not scored["disagreement"].any()
        assert scored["baseline_gap"].abs().max() < BP.disagreement_pct

    def test_fat_tails_fire(self):
        rng = np.random.default_rng(102)
        # Rare large moves, near-flat otherwise: a +/-10% jump every 25th
        # day, alternating sign so the drift stays near zero, on a floor of
        # 0.1% daily noise. The jumps carry essentially all the variance, so
        # the normal fit reports sigma_ann near 32% and prices a 3% move at
        # ~26%, while only the ~10% of windows containing an up-jump
        # actually reach it. A ~16-point gap on a distribution that is not
        # remotely normal, which is precisely what the flag is for.
        n = 880
        daily = rng.normal(0.0, 0.001, n)
        signs = np.array([1.0 if (i // 25) % 2 == 0 else -1.0 for i in range(0, n, 25)])
        daily[::25] = 0.10 * signs

        out = ticker_year_baselines(self._series(daily), (0.03,), BP)
        scored = out[out["baseline_parametric"].notna()]
        assert len(scored) >= 1
        assert scored["disagreement"].all()
        assert scored["baseline_gap"].abs().min() > 0.10

    def test_flag_is_two_sided(self):
        assert disagreement_flag(baseline_gap(0.50, 0.30), BP) is True
        assert disagreement_flag(baseline_gap(0.30, 0.50), BP) is True
        assert disagreement_flag(baseline_gap(0.40, 0.38), BP) is False

    def test_null_propagates_through_gap_and_flag(self):
        assert baseline_gap(None, 0.3) is None
        assert baseline_gap(0.3, None) is None
        assert disagreement_flag(None, BP) is None


class TestNullPropagation:
    """A ticker-year without 252 prior days is null, never shortened."""

    def test_first_year_has_no_parametric_baseline(self):
        rng = np.random.default_rng(5)
        daily = rng.normal(0.0003, 0.011, 700)
        bars = _panel("NUL", 100.0 * np.exp(np.cumsum(daily)))
        out = ticker_year_baselines(bars, (0.03,), BP).sort_values("year")

        first = out.iloc[0]
        assert pd.isna(first["baseline_parametric"])
        assert first["disagreement"] is None
        assert pd.isna(first["mu_annual"])
        # The empirical baseline needs no prior history and is still there.
        assert not pd.isna(first["baseline_empirical"])
        assert first["n_obs"] > 0

        # By the third year the trailing window is complete.
        assert not pd.isna(out.iloc[2]["baseline_parametric"])

    def test_cell_counts_its_nulls(self):
        events = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA", "BBB", "BBB"],
                "signal_date": pd.to_datetime(
                    ["2011-03-01", "2011-06-01", "2011-04-01", "2011-05-01"]
                ),
                "cell": ["c1"] * 4,
            }
        )
        baselines = pd.DataFrame(
            {
                "ticker": ["AAA", "BBB"],
                "year": [2011, 2011],
                "target_pct": [0.03, 0.03],
                "baseline_empirical": [0.40, None],
                "baseline_parametric": [0.38, None],
            }
        )
        joined = attach_event_baselines(events, baselines, 0.03)
        cells = cell_baselines(joined, ["cell"])
        assert cells["n_events"].iloc[0] == 4
        assert cells["n_baseline_used"].iloc[0] == 2
        assert cells["n_null_baseline"].iloc[0] == 2
        assert cells["baseline"].iloc[0] == pytest.approx(0.40)

    def test_all_null_cell_reports_null(self):
        value, n_used, n_null = event_weighted_baseline(pd.Series([None, None], dtype="object"))
        assert (value, n_used, n_null) == (None, 0, 2)


class TestEventWeighting:
    """Event-weighted, never pooled. Hand-computed, visibly different."""

    def test_event_weighted_differs_from_pooled(self):
        # Two ticker-years. AAA's baseline is 0.20 over 250 days; BBB's is
        # 0.60 over 250 days. The cell holds 90 AAA events and 10 BBB.
        #
        # Event-weighted: (90*0.20 + 10*0.60) / 100 = 0.24
        # Pooled over days: (250*0.20 + 250*0.60) / 500 = 0.40
        #
        # Sixteen points apart on the same data. Reporting edge against the
        # wrong one moves every number in the cell.
        per_event = pd.Series([0.20] * 90 + [0.60] * 10)
        value, n_used, n_null = event_weighted_baseline(per_event)
        assert value == pytest.approx(0.24)
        assert (n_used, n_null) == (100, 0)

        pooled = (250 * 0.20 + 250 * 0.60) / 500
        assert pooled == pytest.approx(0.40)
        assert abs(value - pooled) > 0.15

    def test_join_uses_the_events_own_ticker_year(self):
        events = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "signal_date": pd.to_datetime(["2011-12-30", "2012-01-03"]),
            }
        )
        baselines = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "year": [2011, 2012],
                "target_pct": [0.03, 0.03],
                "baseline_empirical": [0.11, 0.99],
                "baseline_parametric": [0.10, 0.98],
            }
        )
        joined = attach_event_baselines(events, baselines, 0.03)
        assert list(joined["baseline_empirical"]) == [0.11, 0.99]

    def test_target_selection_is_exact(self):
        baselines = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "year": [2011, 2011],
                "target_pct": [0.03, 0.05],
                "baseline_empirical": [0.40, 0.20],
                "baseline_parametric": [0.38, 0.19],
            }
        )
        events = pd.DataFrame({"ticker": ["AAA"], "signal_date": pd.to_datetime(["2011-05-05"])})
        assert attach_event_baselines(events, baselines, 0.05)["baseline_empirical"].iloc[0] == 0.20


class TestFrameContract:
    def test_rejects_missing_columns(self):
        with pytest.raises(ValueError):
            ticker_year_baselines(pd.DataFrame({"ticker": [], "ts": []}), (0.03,), BP)

    def test_rejects_empty_targets(self):
        with pytest.raises(ValueError):
            ticker_year_baselines(_panel("AAA", np.full(10, 100.0)), (), BP)

    def test_empty_frame_returns_empty_shape(self):
        empty = pd.DataFrame({"ticker": [], "ts": [], "adj_close": []})
        out = ticker_year_baselines(empty, (0.03,), BP)
        assert len(out) == 0
        assert "baseline_empirical" in out.columns

    def test_empty_cell_table_keeps_shape(self):
        empty = pd.DataFrame({"cell": [], "baseline_empirical": []})
        out = cell_baselines(empty, ["cell"])
        assert list(out.columns) == [
            "cell",
            "n_events",
            "baseline",
            "n_baseline_used",
            "n_null_baseline",
        ]

    def test_deterministic(self):
        rng = np.random.default_rng(3)
        bars = _panel("DET", 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))))
        first = ticker_year_baselines(bars, (0.03, 0.05), BP)
        second = ticker_year_baselines(bars, (0.03, 0.05), BP)
        pd.testing.assert_frame_equal(first, second)


class TestBaselineProperties:
    @given(
        target=st.floats(min_value=0.001, max_value=0.30),
        mu=st.floats(min_value=-1.0, max_value=1.0),
        sigma=st.floats(min_value=0.01, max_value=2.0),
    )
    def test_parametric_is_a_probability(self, target, mu, sigma):
        value = parametric_baseline(target, mu, sigma, BP)
        assert 0.0 <= value <= 1.0

    @given(
        target=st.floats(min_value=0.001, max_value=0.30),
        mu_low=st.floats(min_value=-1.0, max_value=0.0),
        bump=st.floats(min_value=0.01, max_value=1.0),
        sigma=st.floats(min_value=0.05, max_value=1.0),
    )
    def test_more_drift_never_lowers_the_baseline(self, target, mu_low, bump, sigma):
        # Free baseline points from selecting on growth (DESIGN §6.2).
        low = parametric_baseline(target, mu_low, sigma, BP)
        high = parametric_baseline(target, mu_low + bump, sigma, BP)
        assert high >= low - 1e-12

    @given(
        values=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=50),
    )
    def test_event_weighted_mean_stays_in_range(self, values):
        value, n_used, n_null = event_weighted_baseline(pd.Series(values))
        assert value is not None
        assert min(values) - 1e-12 <= value <= max(values) + 1e-12
        assert (n_used, n_null) == (len(values), 0)
