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
