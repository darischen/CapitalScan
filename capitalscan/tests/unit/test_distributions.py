"""`core/distributions.py`: grids, CDF inversion, exceedance, CRPS.

**What these pin is the arithmetic, not a model.** Every number a
distributional predictor reports passes through these four functions, so a
sign or an off-by-one here is invisible in the model and fatal in the
result. Two of the tests below exist because exactly that happened in
Session 24.
"""

from __future__ import annotations

import numpy as np
import pytest

from capitalscan.core import distributions as dist


def _uniform_pmf(n_rows: int, n_bins: int) -> np.ndarray:
    return np.full((n_rows, n_bins), 1.0 / n_bins)


class TestCrpsGrid:
    def test_bins_are_equal_width(self) -> None:
        """The measured reason this is not a quantile grid.

        Equal-mass edges on concentrated returns put 71.5% of the total dz
        in the two outer bins, which turns CRPS into tail-fitting and stops
        the model learning after one pass.
        """
        grid = dist.crps_grid(np.random.default_rng(0).normal(0, 0.03, 20_000), 32)
        widths = np.diff(grid)
        assert len(grid) == 33
        assert np.ptp(widths) == pytest.approx(0.0, abs=1e-12)

    def test_the_span_is_truncated_not_the_full_range(self) -> None:
        """Extending to the extremes hands the integral back to the tails."""
        labels = np.concatenate([np.random.default_rng(1).normal(0, 0.02, 10_000), [-5.0, 5.0]])
        grid = dist.crps_grid(labels, 16)
        assert grid[0] > -1.0 and grid[-1] < 1.0

    def test_nan_labels_do_not_reach_the_quantile(self) -> None:
        clean = dist.crps_grid(np.array([0.0, 1.0, 2.0, 3.0]), 4)
        dirty = dist.crps_grid(np.array([0.0, 1.0, np.nan, 2.0, 3.0]), 4)
        assert clean == pytest.approx(dirty)

    @pytest.mark.parametrize("bad", [0, 1, -3])
    def test_it_refuses_a_degenerate_bin_count(self, bad: int) -> None:
        with pytest.raises(ValueError):
            dist.crps_grid(np.array([0.0, 1.0]), bad)

    def test_it_refuses_constant_labels(self) -> None:
        """A zero-width grid divides by zero inside the integral."""
        with pytest.raises(ValueError, match="degenerate span"):
            dist.crps_grid(np.full(100, 0.5), 8)

    def test_it_refuses_an_empty_label_set(self) -> None:
        with pytest.raises(ValueError):
            dist.crps_grid(np.array([np.nan, np.nan]), 8)


class TestQuantilesFromPmf:
    def test_a_uniform_pmf_recovers_the_grid_linearly(self) -> None:
        grid = np.linspace(0.0, 1.0, 11)
        out = dist.quantiles_from_pmf(_uniform_pmf(1, 10), grid, (0.05, 0.5, 0.95))
        assert out[0.05][0] == pytest.approx(0.05)
        assert out[0.5][0] == pytest.approx(0.50)
        assert out[0.95][0] == pytest.approx(0.95)

    def test_a_point_mass_sits_in_its_own_bin(self) -> None:
        grid = np.linspace(0.0, 10.0, 11)
        pmf = np.zeros((1, 10))
        pmf[0, 4] = 1.0
        out = dist.quantiles_from_pmf(pmf, grid, (0.5,))
        assert 4.0 <= out[0.5][0] <= 5.0

    def test_the_fan_is_monotone_without_sorting(self) -> None:
        """The structural advantage over independently fitted heads.

        DESIGN §7.4 sorts because the twenty heads can cross. A fan read
        off one CDF cannot, so `sort_quantiles` has nothing to do here --
        and applying it anyway would hide a real bug rather than repair one.
        """
        rng = np.random.default_rng(3)
        raw = rng.random((200, 16))
        pmf = raw / raw.sum(axis=1, keepdims=True)
        grid = np.linspace(-0.2, 0.2, 17)
        taus = (0.05, 0.25, 0.5, 0.75, 0.95)
        out = dist.quantiles_from_pmf(pmf, grid, taus)
        stacked = np.vstack([out[t] for t in taus])
        assert (np.diff(stacked, axis=0) >= -1e-12).all()

    def test_it_refuses_a_grid_that_does_not_match_the_pmf(self) -> None:
        with pytest.raises(ValueError, match="bins"):
            dist.quantiles_from_pmf(_uniform_pmf(2, 10), np.linspace(0, 1, 8), (0.5,))

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.4])
    def test_it_refuses_tau_outside_the_open_interval(self, bad: float) -> None:
        with pytest.raises(ValueError, match="tau"):
            dist.quantiles_from_pmf(_uniform_pmf(1, 4), np.linspace(0, 1, 5), (bad,))


class TestExceedance:
    def test_it_inverts_quantiles_from_pmf(self) -> None:
        """`P(Y > q_tau)` must be `1 - tau`, or the two readings of the same
        distribution disagree and `Prediction` carries both."""
        rng = np.random.default_rng(5)
        raw = rng.random((50, 24))
        pmf = raw / raw.sum(axis=1, keepdims=True)
        grid = np.linspace(-0.3, 0.3, 25)
        q = dist.quantiles_from_pmf(pmf, grid, (0.75,))[0.75]
        for i in range(len(pmf)):
            got = dist.exceedance(pmf[i : i + 1], grid, float(q[i]))[0]
            assert got == pytest.approx(0.25, abs=1e-9)

    def test_below_the_grid_everything_exceeds(self) -> None:
        grid = np.linspace(0.0, 1.0, 11)
        assert dist.exceedance(_uniform_pmf(1, 10), grid, -5.0)[0] == pytest.approx(1.0)

    def test_above_the_grid_nothing_exceeds(self) -> None:
        grid = np.linspace(0.0, 1.0, 11)
        assert dist.exceedance(_uniform_pmf(1, 10), grid, 5.0)[0] == pytest.approx(0.0)


class TestCrps:
    def test_a_sharp_correct_forecast_beats_a_diffuse_one(self) -> None:
        """The property that makes it a scoring rule at all."""
        grid = np.linspace(-1.0, 1.0, 33)
        y = np.zeros(4)
        sharp = np.zeros((4, 32))
        sharp[:, 16] = 1.0
        assert dist.crps(sharp, y, grid) < dist.crps(_uniform_pmf(4, 32), y, grid)

    def test_a_sharp_wrong_forecast_loses_to_a_diffuse_one(self) -> None:
        """Confidence is only rewarded when it is right, which is the half
        a coverage check cannot see and a sharpness metric cannot see."""
        grid = np.linspace(-1.0, 1.0, 33)
        y = np.full(4, 0.9)
        wrong = np.zeros((4, 32))
        wrong[:, 0] = 1.0
        assert dist.crps(wrong, y, grid) > dist.crps(_uniform_pmf(4, 32), y, grid)

    def test_it_is_ordered_not_categorical(self) -> None:
        """The reason CRPS replaces cross-entropy for these bins.

        Cross-entropy treats the bins as unordered labels, so a neighbouring
        bin costs what the far tail costs. Ordered returns need a loss that
        charges by distance.
        """
        grid = np.linspace(0.0, 32.0, 33)
        y = np.full(1, 16.5)
        near = np.zeros((1, 32))
        near[0, 15] = 1.0
        far = np.zeros((1, 32))
        far[0, 0] = 1.0
        assert dist.crps(near, y, grid) < dist.crps(far, y, grid)

    def test_nan_targets_are_dropped_not_scored_as_zero(self) -> None:
        """Invariant 4. Imputing them makes a model look better the more
        unresolved events it was shown."""
        grid = np.linspace(-1.0, 1.0, 9)
        pmf = _uniform_pmf(3, 8)
        clean = dist.crps(pmf[:2], np.array([0.1, -0.2]), grid)
        dirty = dist.crps(pmf, np.array([0.1, -0.2, np.nan]), grid)
        assert clean == pytest.approx(dirty)

    def test_it_refuses_an_all_nan_comparison(self) -> None:
        grid = np.linspace(-1.0, 1.0, 9)
        with pytest.raises(ValueError, match="perfect forecast"):
            dist.crps(_uniform_pmf(2, 8), np.array([np.nan, np.nan]), grid)

    def test_weights_change_the_answer(self) -> None:
        """DESIGN §7.5's `1/|cluster|` has to reach this score too, or a
        four-event cluster votes four times here as well."""
        grid = np.linspace(-1.0, 1.0, 9)
        pmf = np.zeros((2, 8))
        pmf[0, 0] = 1.0
        pmf[1, 7] = 1.0
        y = np.array([-0.9, -0.9])
        flat = dist.crps(pmf, y, grid)
        tilted = dist.crps(pmf, y, grid, weights=np.array([1.0, 0.0]))
        assert tilted < flat

    def test_it_refuses_zero_total_weight(self) -> None:
        grid = np.linspace(-1.0, 1.0, 9)
        with pytest.raises(ValueError, match="weights sum to zero"):
            dist.crps(_uniform_pmf(2, 8), np.array([0.1, 0.2]), grid, weights=np.zeros(2))

    def test_grid_and_pmf_must_agree(self) -> None:
        """The Session 24 shape error, pinned.

        The CDF, the right edges and the bin widths all have length K.
        Slicing one of them by one misaligns the integral, and the lucky
        version of that mistake raises rather than returning a number.
        """
        with pytest.raises(ValueError, match="bins"):
            dist.crps(_uniform_pmf(2, 32), np.array([0.0, 0.0]), np.linspace(-1, 1, 32))


class TestUnconditionalPmf:
    def test_it_reproduces_the_label_histogram(self) -> None:
        grid = np.linspace(0.0, 4.0, 5)
        pmf = dist.unconditional_pmf(np.array([0.5, 1.5, 1.6, 2.5, 3.5]), grid)
        assert pmf == pytest.approx([0.2, 0.4, 0.2, 0.2])

    def test_it_sums_to_one(self) -> None:
        rng = np.random.default_rng(7)
        labels = rng.normal(0, 0.03, 5_000)
        grid = dist.crps_grid(labels, 20)
        assert dist.unconditional_pmf(labels, grid).sum() == pytest.approx(1.0)

    def test_it_beats_nothing_and_loses_to_a_conditional_forecast(self) -> None:
        """It is a baseline: a real model has to beat it, and a model that
        does not has extracted nothing from its features."""
        rng = np.random.default_rng(11)
        labels = rng.normal(0.0, 1.0, 4_000)
        grid = dist.crps_grid(labels, 32)
        base = np.tile(dist.unconditional_pmf(labels, grid), (200, 1))
        y = rng.normal(0.0, 1.0, 200)
        assert dist.crps(base, y, grid) < dist.crps(_uniform_pmf(200, 32), y, grid)

    def test_it_refuses_labels_that_all_miss_the_grid(self) -> None:
        with pytest.raises(ValueError, match="outside the grid"):
            dist.unconditional_pmf(np.array([50.0, 60.0]), np.linspace(0.0, 1.0, 5))
