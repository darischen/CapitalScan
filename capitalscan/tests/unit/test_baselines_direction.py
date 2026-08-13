"""Direction-aware ticker-year baselines (Session 12.3, ADR 106).

Six of the twelve headline cells are short. A short cell's `p_hit` is
measured on the *favorable* move — `path_labels` builds `touched_*` from
`reach["favorable"]`, so a short event hits when the price falls — while
Session 11's baseline layer measures `P(R_h >= target)`, which is the long
direction and only the long direction.

Subtracting one from the other gives an `edge` that compares the
probability of a short winning against the probability of a long winning.
That number would look entirely reasonable and mean nothing.

The fix is a `direction` on the windowing, not on the formulas:
`core/baselines.py` is untouched, and a short's return series is the
negation of the long's. `direction=1` must reproduce Session 11's numbers
exactly, which is what the parity test at the bottom pins.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import BaselineParams
from capitalscan.research.baselines import ticker_year_baselines


def _panel(returns: list[float], ticker: str = "TEST", start: str = "2015-01-01") -> pd.DataFrame:
    """A daily panel whose successive returns are exactly `returns`."""
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": pd.bdate_range(start, periods=len(closes)),
            "adj_close": closes,
        }
    )


def _drifting_panel(daily: float, n: int = 400) -> pd.DataFrame:
    return _panel([daily] * n)


class TestDirectionFlipsTheEmpiricalBaseline:
    def test_a_steadily_rising_ticker_has_a_long_baseline_of_one(self):
        bp = BaselineParams()
        rows = ticker_year_baselines(_drifting_panel(0.01), (0.02,), bp, direction=1)
        assert rows["baseline_empirical"].dropna().eq(1.0).all()

    def test_the_same_ticker_has_a_short_baseline_of_zero(self):
        """A price that only ever rises never delivers a 2% decline, so the
        short baseline is 0. Under Session 11's direction-blind code this
        returns 1.0, and every short cell's edge is wrong by that much."""
        bp = BaselineParams()
        rows = ticker_year_baselines(_drifting_panel(0.01), (0.02,), bp, direction=-1)
        assert rows["baseline_empirical"].dropna().eq(0.0).all()

    def test_a_falling_ticker_reverses_both(self):
        bp = BaselineParams()
        long_rows = ticker_year_baselines(_drifting_panel(-0.01), (0.02,), bp, direction=1)
        short_rows = ticker_year_baselines(_drifting_panel(-0.01), (0.02,), bp, direction=-1)
        assert long_rows["baseline_empirical"].dropna().eq(0.0).all()
        assert short_rows["baseline_empirical"].dropna().eq(1.0).all()


class TestDirectionFlipsDriftButNotVolatility:
    def test_short_drift_is_the_negation_of_long_drift(self):
        bp = BaselineParams()
        long_rows = ticker_year_baselines(_drifting_panel(0.001), (0.02,), bp, direction=1)
        short_rows = ticker_year_baselines(_drifting_panel(0.001), (0.02,), bp, direction=-1)
        merged = long_rows.merge(short_rows, on=["ticker", "year"], suffixes=("_l", "_s"))
        usable = merged.dropna(subset=["mu_annual_l", "mu_annual_s"])
        assert not usable.empty
        assert np.allclose(usable["mu_annual_l"], -usable["mu_annual_s"])

    def test_volatility_is_unchanged_by_direction(self):
        """Negating a return series leaves its dispersion alone. A
        `direction` that flipped sigma too would be a sign error that only
        shows up as a slightly wrong parametric baseline."""
        bp = BaselineParams()
        rng = np.random.default_rng(11)
        noisy = _panel(list(rng.normal(0.0005, 0.02, 400)))
        long_rows = ticker_year_baselines(noisy, (0.02,), bp, direction=1)
        short_rows = ticker_year_baselines(noisy, (0.02,), bp, direction=-1)
        merged = long_rows.merge(short_rows, on=["ticker", "year"], suffixes=("_l", "_s"))
        usable = merged.dropna(subset=["sigma_annual_l", "sigma_annual_s"])
        assert not usable.empty
        assert np.allclose(usable["sigma_annual_l"], usable["sigma_annual_s"])

    def test_parametric_baselines_straddle_the_zero_drift_case(self):
        """With positive drift, a long is likelier to reach +2% than a
        short is to reach a 2% decline. Both stay in [0, 1]."""
        bp = BaselineParams()
        rng = np.random.default_rng(12)
        noisy = _panel(list(rng.normal(0.001, 0.015, 400)))
        long_par = ticker_year_baselines(noisy, (0.02,), bp, direction=1)["baseline_parametric"]
        short_par = ticker_year_baselines(noisy, (0.02,), bp, direction=-1)["baseline_parametric"]
        assert long_par.dropna().between(0, 1).all()
        assert short_par.dropna().between(0, 1).all()
        assert long_par.dropna().mean() > short_par.dropna().mean()


class TestDefaultIsSession11Behaviour:
    def test_direction_defaults_to_long(self):
        bp = BaselineParams()
        rng = np.random.default_rng(13)
        noisy = _panel(list(rng.normal(0.0004, 0.018, 400)))
        default = ticker_year_baselines(noisy, (0.02, 0.05), bp)
        explicit = ticker_year_baselines(noisy, (0.02, 0.05), bp, direction=1)
        pd.testing.assert_frame_equal(default, explicit)

    def test_nulls_still_propagate_rather_than_filling(self):
        """Invariant 4 does not bend for the short side: a ticker-year
        without a complete trailing window carries a null parametric
        baseline in both directions."""
        bp = BaselineParams()
        short_panel = _panel([0.01] * 5)
        rows = ticker_year_baselines(short_panel, (0.02,), bp, direction=-1)
        assert rows["baseline_parametric"].isna().all()

    @pytest.mark.parametrize("bad", [0, 2, -2, 1.5])
    def test_direction_must_be_plus_or_minus_one(self, bad):
        """A silent `direction=0` would zero every return and report a
        baseline of `P(0 >= target)`, which is 0 for every positive target
        and looks like a real measurement."""
        with pytest.raises(ValueError, match="direction"):
            ticker_year_baselines(_drifting_panel(0.01), (0.02,), BaselineParams(), direction=bad)
