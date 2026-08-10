import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from capitalscan.core.stats import benjamini_hochberg, standard_error_n_eff, wilson_ci


class TestWilsonCI:
    """Wilson confidence interval tests.

    Reference values from published tables and hand-computed examples.
    """

    def test_wilson_reference_cases(self):
        """Match published reference values across parameter space."""
        # Reference case 1: p=0.5, n=100 (from Wilson 1927 table)
        lower, upper = wilson_ci(successes=50, trials=100, alpha=0.05)
        assert 0.40 <= lower <= 0.42
        assert 0.58 <= upper <= 0.60

        # Reference case 2: p near 0 (small success rate)
        # k=1, n=100: boundary case
        lower, upper = wilson_ci(successes=1, trials=100, alpha=0.05)
        assert 0.0 <= lower <= 0.01
        assert 0.04 <= upper <= 0.06

        # Reference case 3: p near 1 (high success rate)
        # k=99, n=100: boundary case
        lower, upper = wilson_ci(successes=99, trials=100, alpha=0.05)
        assert 0.94 <= lower <= 0.96
        assert 0.99 <= upper <= 1.0

        # Reference case 4: small n, edge case
        # k=0, n=10
        lower, upper = wilson_ci(successes=0, trials=10, alpha=0.05)
        assert lower == 0.0
        assert 0.0 <= upper <= 0.30

        # Reference case 5: small n, all successes
        # k=10, n=10
        lower, upper = wilson_ci(successes=10, trials=10, alpha=0.05)
        assert 0.70 <= lower <= 1.0
        assert upper == 1.0

        # Reference case 6: moderate n, p=0.03 (the normal approximation failure case)
        # k=1, n=35
        lower, upper = wilson_ci(successes=1, trials=35, alpha=0.05)
        assert lower >= 0.0  # Normal would go negative
        assert upper <= 1.0

    @given(
        successes=st.integers(min_value=0, max_value=1000),
        trials=st.integers(min_value=1, max_value=1000),
    )
    def test_wilson_bounds_in_valid_range(self, successes, trials):
        """Property test: bounds always lie in [0, 1]."""
        if successes <= trials:
            lower, upper = wilson_ci(successes, trials, alpha=0.05)
            assert 0.0 <= lower <= 1.0
            assert 0.0 <= upper <= 1.0
            assert lower <= upper

    def test_wilson_zero_and_one(self):
        """Edge cases: k=0 and k=n."""
        # All failures
        lower, upper = wilson_ci(successes=0, trials=50, alpha=0.05)
        assert lower == 0.0
        assert 0.0 < upper < 0.1

        # All successes
        lower, upper = wilson_ci(successes=50, trials=50, alpha=0.05)
        assert 0.9 < lower < 1.0
        assert upper == 1.0

    def test_wilson_error_handling(self):
        """Reject invalid inputs."""
        with pytest.raises(ValueError):
            wilson_ci(successes=-1, trials=100)
        with pytest.raises(ValueError):
            wilson_ci(successes=101, trials=100)
        with pytest.raises(ValueError):
            wilson_ci(successes=50, trials=0)


class TestStandardErrorNEff:
    """Standard error on effective sample size."""

    def test_se_formula(self):
        """Compute against known values."""
        # p=0.5, n_eff=100 -> SE = sqrt(0.25 / 100) = 0.05
        se = standard_error_n_eff(p=0.5, n_eff=100)
        assert abs(se - 0.05) < 1e-10

        # p=0.3, n_eff=50 -> SE = sqrt(0.21 / 50) ≈ 0.0648
        se = standard_error_n_eff(p=0.3, n_eff=50)
        assert abs(se - np.sqrt(0.21 / 50)) < 1e-10

    def test_se_boundaries(self):
        """SE is zero at p=0 and p=1."""
        assert standard_error_n_eff(p=0.0, n_eff=100) == 0.0
        assert standard_error_n_eff(p=1.0, n_eff=100) == 0.0

    def test_se_n_eff_not_n(self):
        """Parameter is named n_eff, not n (structural test)."""
        import inspect

        sig = inspect.signature(standard_error_n_eff)
        assert "n_eff" in sig.parameters
        assert "n" not in [p for p in sig.parameters if p != "n_eff"]

    def test_se_error_handling(self):
        """Reject invalid inputs."""
        with pytest.raises(ValueError):
            standard_error_n_eff(p=-0.1, n_eff=100)
        with pytest.raises(ValueError):
            standard_error_n_eff(p=1.1, n_eff=100)
        with pytest.raises(ValueError):
            standard_error_n_eff(p=0.5, n_eff=-1)


class TestBenjaminiHochberg:
    """Benjamini-Hochberg multiple testing correction."""

    def test_bh_hand_computed_example(self):
        """Reproduce a hand-computed example with known answer."""
        # Example: 5 tests with p-values [0.001, 0.008, 0.039, 0.041, 0.042]
        # At alpha=0.05:
        # Thresholds: 0.01, 0.02, 0.03, 0.04, 0.05
        # Below: [True, False, False, False, False] -> k=1
        # Reject only the first test
        # Raw q-values: (5/1)*0.001=0.005, (5/2)*0.008=0.020, (5/3)*0.039=0.065,
        #               (5/4)*0.041=0.051, (5/5)*0.042=0.042
        # After running min: 0.005, 0.020, 0.042, 0.042, 0.042
        # (monotone increasing)
        p_vals = np.array([0.001, 0.008, 0.039, 0.041, 0.042])
        p_out, q_vals = benjamini_hochberg(p_vals, alpha=0.05)

        np.testing.assert_array_equal(p_out, p_vals)

        # Check monotonicity: q-values should be monotone non-decreasing
        assert np.all(np.diff(q_vals) >= -1e-10)

        # Check first few q-values
        assert abs(q_vals[0] - 0.005) < 0.001
        assert abs(q_vals[1] - 0.020) < 0.001

        # All should be >= corresponding p-value
        assert np.all(q_vals >= p_vals - 1e-10)

    def test_bh_monotonicity_enforced(self):
        """Q-values are monotone even without naive implementation."""
        # Construct a case where naive (without running min) would fail
        p_vals = np.array([0.001, 0.01, 0.05, 0.002, 0.03])
        p_out, q_vals = benjamini_hochberg(p_vals, alpha=0.05)

        # The sorted p-values are [0.001, 0.002, 0.01, 0.03, 0.05]
        # Naive would give (5/1)*0.001=0.005, (5/2)*0.002=0.005, (5/3)*0.01=0.017,
        #                  (5/4)*0.03=0.0375, (5/5)*0.05=0.05
        # But without running min, after unsort, the original order might not be monotone
        # Our implementation ensures it is by applying running min to sorted

        # Check: sorted q-values should be monotone
        sorted_idx = np.argsort(p_vals)
        sorted_q = q_vals[sorted_idx]
        assert np.all(np.diff(sorted_q) >= -1e-10)

    def test_bh_all_ones_rejects_nothing(self):
        """All p-values = 1.0 means no rejections."""
        p_vals = np.ones(10)
        p_out, q_vals = benjamini_hochberg(p_vals, alpha=0.05)
        assert np.all(q_vals == 1.0)

    def test_bh_all_zeros_rejects_all(self):
        """All p-values ~ 0 means all rejected."""
        p_vals = np.full(10, 1e-10)
        p_out, q_vals = benjamini_hochberg(p_vals, alpha=0.05)
        assert np.all(q_vals <= 0.05 + 1e-10)

    def test_bh_q_ge_p_always(self):
        """Property test: q_value >= p_value for every test."""

        @given(
            p_array_len=st.integers(min_value=1, max_value=100),
        )
        def check_q_ge_p(p_array_len):
            p_vals = np.random.uniform(0, 1, size=p_array_len)
            p_out, q_vals = benjamini_hochberg(p_vals, alpha=0.05)
            assert np.all(q_vals >= p_vals - 1e-10)

        check_q_ge_p()

    def test_bh_error_handling(self):
        """Reject invalid inputs."""
        with pytest.raises(ValueError):
            benjamini_hochberg(np.array([-0.1, 0.5]), alpha=0.05)
        with pytest.raises(ValueError):
            benjamini_hochberg(np.array([0.5, 1.1]), alpha=0.05)
        with pytest.raises(ValueError):
            benjamini_hochberg(np.array([0.5, 0.3]), alpha=-0.05)

    def test_bh_empty_input(self):
        """Handle empty p-value array."""
        p_vals = np.array([])
        p_out, q_vals = benjamini_hochberg(p_vals, alpha=0.05)
        assert len(q_vals) == 0
