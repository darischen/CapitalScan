"""Statistical primitives for Phase 4 event analysis.

Pure functions computing confidence intervals, standard errors, and
multiple-testing corrections. No IO, no database, no config reads
(invariant 1). All functions are deterministic.

References:
- Wilson (1927) confidence intervals: not the normal approximation
- Benjamini & Hochberg (1995) FDR control
"""

from typing import Tuple

import numpy as np
from scipy import stats


def wilson_ci(
    successes: int,
    trials: int,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    """Wilson score interval for a proportion.

    Computes a confidence interval on a binomial success probability
    using the Wilson score method, which handles edge cases (p near 0 or 1,
    small n) correctly, unlike the normal approximation.

    At p=0.03, n=35, the normal interval's lower bound goes negative.
    This method never leaves [0, 1].

    Args:
        successes: Number of successes (k)
        trials: Total number of trials (n)
        alpha: Significance level; default 0.05 for 95% CI

    Returns:
        (lower, upper) bounds, guaranteed to lie in [0, 1]

    Reference: Wilson, E.B. (1927). "Probable Inference, the Law of
    Succession, and Statistical Inference." JASA 22(158): 209-212.
    """
    if trials <= 0:
        raise ValueError(f"trials must be positive, got {trials}")
    if successes < 0 or successes > trials:
        raise ValueError(f"successes must be in [0, trials], got {successes} / {trials}")
    if not (0 < alpha < 1):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    p = successes / trials
    z = stats.norm.ppf(1 - alpha / 2)  # two-tailed critical value

    denom = 1 + z**2 / trials
    center = (p + z**2 / (2 * trials)) / denom
    margin = z * np.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denom

    lower = float(np.round(np.clip(center - margin, 0, 1), 4))
    upper = float(np.round(np.clip(center + margin, 0, 1), 4))

    return (lower, upper)


def standard_error_n_eff(p: float, n_eff: float) -> float:
    """Standard error of a proportion on effective sample size.

    SE = sqrt(p * (1 - p) / n_eff)

    Always uses n_eff, never raw sample count n. This ensures inflated
    sample sizes from correlated events are properly reflected in wider
    confidence intervals (ADR 098).

    Args:
        p: Estimated proportion, in [0, 1]
        n_eff: Effective sample size (not raw count)

    Returns:
        Standard error as a float
    """
    if not (0 <= p <= 1):
        raise ValueError(f"p must be in [0, 1], got {p}")
    if n_eff <= 0:
        raise ValueError(f"n_eff must be positive, got {n_eff}")

    # At the boundaries (p=0 or p=1), SE is zero
    se = np.sqrt(p * (1 - p) / n_eff)
    return float(se)


def benjamini_hochberg(
    p_values: np.ndarray,
    alpha: float = 0.05,
) -> Tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg procedure for FDR control.

    Controls the False Discovery Rate (FDR) at level alpha across a
    family of tests. Returns both p-values (unchanged) and q-values
    (adjusted p-values for FDR control).

    The q-value is computed with running minimum enforcement, so
    q_values are monotone increasing with the original p-value ordering.
    A naive implementation omitting this produces non-monotone q-values.

    Args:
        p_values: Array of p-values from m independent tests, in [0, 1]
        alpha: FDR control level; default 0.05

    Returns:
        (p_values_out, q_values) tuple, both as numpy arrays

    Reference: Benjamini, Y. & Hochberg, Y. (1995). "Controlling the
    False Discovery Rate: A Practical and Powerful Approach to Multiple
    Testing." JRSS-B 57(1): 289-300.
    """
    if not isinstance(p_values, np.ndarray):
        p_values = np.asarray(p_values)

    if p_values.size == 0:
        return p_values.copy(), np.array([], dtype=float)

    if not (0 < alpha <= 1):
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")

    # Check p-values are in [0, 1]
    if np.any((p_values < 0) | (p_values > 1)):
        raise ValueError("all p-values must be in [0, 1]")

    m = len(p_values)

    # Sort p-values and track original indices
    sorted_indices = np.argsort(p_values)
    sorted_p = p_values[sorted_indices]

    # Find threshold: largest k where p_(k) <= (k/m) * alpha
    # k is 1-indexed in the paper; we use 0-indexed, so k_0 <= (k_0+1)/m * alpha
    thresholds = ((np.arange(1, m + 1)) / m) * alpha
    below_threshold = sorted_p <= thresholds

    if np.any(below_threshold):
        _k = np.where(below_threshold)[0][-1] + 1  # Largest index (1-indexed count)
    else:
        _k = 0  # No rejections

    # Compute raw q-values: (m/j) * p_(j) for each j
    raw_q = (m / np.arange(1, m + 1)) * sorted_p

    # Apply running minimum from right to left to ensure monotonicity
    q_sorted = np.zeros_like(raw_q)
    q_sorted[-1] = raw_q[-1]
    for i in range(m - 2, -1, -1):
        q_sorted[i] = min(raw_q[i], q_sorted[i + 1])

    # Clip to [0, 1]
    q_sorted = np.clip(q_sorted, 0, 1)

    # Restore to original order
    q_values = np.empty_like(q_sorted)
    q_values[sorted_indices] = q_sorted

    return p_values.copy(), q_values
