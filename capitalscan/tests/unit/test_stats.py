import pytest
from hypothesis import given
from hypothesis import strategies as st

from capitalscan.core.stats import wilson_ci


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
