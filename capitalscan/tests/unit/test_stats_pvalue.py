"""`one_sided_p_value` (Session 12.3, DESIGN §6.8).

The parametric p-value a headline cell publishes: how surprising its hit
rate is against its own baseline, on the **effective** sample size. The
randomization p-value is a different column and arrives with Session 13's
200 replications.
"""

from __future__ import annotations

import pytest

from capitalscan.core.stats import one_sided_p_value


class TestDirectionAndScale:
    def test_hit_rate_equal_to_baseline_is_a_coin_flip(self):
        assert one_sided_p_value(0.40, 0.40, n_eff=200) == pytest.approx(0.5)

    def test_edge_above_baseline_gives_a_small_p_value(self):
        assert one_sided_p_value(0.55, 0.40, n_eff=200) < 0.001

    def test_edge_below_baseline_gives_a_large_p_value(self):
        """One-sided, in the direction the system claims. A signal that
        underperforms its baseline is not evidence for the signal, and a
        two-sided test would report it as significant."""
        assert one_sided_p_value(0.25, 0.40, n_eff=200) > 0.999

    def test_the_same_edge_is_less_surprising_on_a_smaller_sample(self):
        big = one_sided_p_value(0.50, 0.40, n_eff=400)
        small = one_sided_p_value(0.50, 0.40, n_eff=40)
        assert small > big

    def test_n_eff_is_what_moves_it_not_n(self):
        """The whole point of ADR 098. A cell of 4,116 events with n_eff
        717 must be judged on 717, and passing the raw count here is the
        bug that makes every q-value too small to believe."""
        honest = one_sided_p_value(0.45, 0.40, n_eff=717)
        inflated = one_sided_p_value(0.45, 0.40, n_eff=4116)
        assert honest > inflated


class TestBoundariesAndNulls:
    def test_result_is_always_a_probability(self):
        for p_hit in (0.0, 0.01, 0.5, 0.99, 1.0):
            value = one_sided_p_value(p_hit, 0.40, n_eff=100)
            assert 0.0 <= value <= 1.0

    def test_degenerate_baseline_of_zero_has_no_variance_to_test_against(self):
        """SE is zero at a baseline of 0 or 1, so the z-score is undefined
        rather than infinite. Null propagates (invariant 4)."""
        assert one_sided_p_value(0.5, 0.0, n_eff=100) is None
        assert one_sided_p_value(0.5, 1.0, n_eff=100) is None

    def test_null_baseline_propagates(self):
        assert one_sided_p_value(0.5, None, n_eff=100) is None

    def test_null_hit_rate_propagates(self):
        assert one_sided_p_value(None, 0.4, n_eff=100) is None

    def test_non_positive_n_eff_is_an_error_not_a_null(self):
        with pytest.raises(ValueError, match="n_eff"):
            one_sided_p_value(0.5, 0.4, n_eff=0)
