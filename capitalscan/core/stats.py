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
    successes: float,
    n_eff: float,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    """Wilson score interval for a proportion, on **effective** sample size.

    Computes a confidence interval on a binomial success probability
    using the Wilson score method, which handles edge cases (p near 0 or 1,
    small n) correctly, unlike the normal approximation.

    At p=0.03, n=35, the normal interval's lower bound goes negative.
    This method never leaves [0, 1].

    **The sample-size parameter is `n_eff`, never a raw event count**, for
    the same reason `standard_error_n_eff` names its own (invariant 8, ADR
    098): every probability Phase 4 publishes carries an interval, and an
    interval sized on `n` rather than `n_eff` is too narrow by
    `sqrt(1 + (k_bar - 1) * rho_bar)` — the exact bug the Session 11.4 null
    test catches, and the reason the name is enforced by a signature test
    rather than by a comment.

    `n_eff` is a float and is used as one. Rounding it to an integer first
    (the shape this function originally forced on its callers) discards the
    correction's precision for no gain: the Wilson formula is continuous in
    the sample size and never required an integer.

    Args:
        successes: Effective successes, `p_hit * n_eff`. Need not be integral
        n_eff: Effective sample size, after the ADR 098 clustering correction
        alpha: Significance level; default 0.05 for 95% CI

    Returns:
        (lower, upper) bounds, guaranteed to lie in [0, 1]

    Reference: Wilson, E.B. (1927). "Probable Inference, the Law of
    Succession, and Statistical Inference." JASA 22(158): 209-212.
    """
    if n_eff <= 0:
        raise ValueError(f"n_eff must be positive, got {n_eff}")
    if successes < 0 or successes > n_eff:
        raise ValueError(f"successes must be in [0, n_eff], got {successes} / {n_eff}")
    if not (0 < alpha < 1):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    p = successes / n_eff
    z = stats.norm.ppf(1 - alpha / 2)  # two-tailed critical value

    denom = 1 + z**2 / n_eff
    center = (p + z**2 / (2 * n_eff)) / denom
    margin = z * np.sqrt(p * (1 - p) / n_eff + z**2 / (4 * n_eff**2)) / denom

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


def effective_sample_size(n: int, k_bar: float, rho_bar: float) -> float:
    """DESIGN §6.3 / ADR 098's clustering correction.

    ```
    n_eff = n / (1 + (k_bar - 1) * rho_bar)
    ```

    A signal firing on twelve names the same day does not deliver twelve
    independent observations. If the market fell that day it delivers
    something closer to one, and this is the discount that says so.

    `k_bar` is the mean `cofire_count` across the cell's events, already
    stored on `events`. `rho_bar` is the mean pairwise correlation of 5-day
    returns among co-firing tickers, measured per era and read from
    `rho_era` — never a literal, and never `StatsParams`, because it is a
    measurement rather than a chosen parameter (ADR 098 part 3).

    Two identities hold exactly and are pinned by property test:
    `n_eff <= n` always, with equality precisely when `k_bar == 1` (no
    co-firing) or `rho_bar == 0` (co-firing carries no shared information).

    Args:
        n: Raw event count
        k_bar: Mean co-fire count, at least 1 (an event always co-fires
            with itself)
        rho_bar: Mean pairwise correlation, in [0, 1]

    Returns:
        Effective sample size as a float, never exceeding `n`
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if k_bar < 1:
        raise ValueError(f"k_bar must be at least 1, got {k_bar}")
    if not (0 <= rho_bar <= 1):
        raise ValueError(f"rho_bar must be in [0, 1], got {rho_bar}")

    # The denominator is >= 1 for every admissible input, so no clamp is
    # needed to keep n_eff <= n. A negative rho_bar would break that, which
    # is why it is rejected above rather than clipped: a negative mean
    # pairwise correlation among co-firing names would mean the clustering
    # *adds* information, and that claim deserves an error, not a silent 0.
    return float(n / (1.0 + (k_bar - 1.0) * rho_bar))


def rho_bar_for_correction(rho_measured: float) -> float:
    """The measured `rho_bar`, made admissible for `effective_sample_size`.

    A measured mean pairwise correlation can land slightly below zero on a
    population with no real clustering — that is estimation noise around
    zero, not evidence that co-firing adds information. Passing it through
    unchanged would make the denominator smaller than 1 and hand back an
    `n_eff` **larger** than `n`, which is the unsafe direction and the one
    ADR 098 spends its whole overlapping-window argument avoiding.

    Clamping at zero means "apply no correction," which is the conservative
    reading of a null measurement. The stored `rho_era.rho_empirical` keeps
    the raw value: this function is applied at the point of use, so the
    measurement stays visible and only the correction is bounded.

    Values above 1 are impossible for a correlation and are rejected rather
    than clamped, since they indicate a computation error rather than noise.
    """
    if rho_measured > 1.0:
        raise ValueError(f"rho_measured cannot exceed 1, got {rho_measured}")
    return float(max(rho_measured, 0.0))


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

    # The rejection set is not returned, and deliberately so. The paper's
    # step is "find the largest k with p_(k) <= (k/m) * alpha, reject 1
    # through k", and `q_(i) <= alpha` holds for exactly those same i — the
    # running minimum below is what makes the two equivalent. Every caller
    # thresholds on `q < alpha`, and DESIGN §6.8 stores `p_value` and
    # `q_value`, never a rejection flag, so computing k here as well would
    # be a second encoding of one decision with no consumer.

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
