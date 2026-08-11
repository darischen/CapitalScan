"""Baseline probabilities: what a target reaches without any signal.

DESIGN §6.2 and ADR 062. Two baselines per ticker-year, both stored, one
reported. Pure functions, no IO, no config reads (invariant 1) — every
constant arrives as an argument from `core.config.BaselineParams`.

The pair exists because they fail differently. The **empirical** baseline is
the realized fraction of days whose 5-day forward return reached the target:
assumption-free, and noisy on ~250 overlapping observations. The
**parametric** baseline is that same probability under a normal return
distribution fitted to the trailing window: smooth, and wrong exactly when
the distribution has fat tails. Edge is reported against the empirical one
(ADR 062). A large gap between them is the diagnostic saying the ticker-year
is not normal, never a correction applied to anything.

Two rules this module exists to enforce structurally:

- The trailing window ends **strictly before** the day being predicted.
  `trailing_drift_vol` shifts before it rolls, so no caller can pass the
  observation day into its own predictor.
- A cell's baseline is the **event-weighted mean** of its constituent
  events' per-ticker-year baselines, never a pooled rate over all days
  (`event_weighted_baseline`). The two differ whenever events are unevenly
  distributed across ticker-years, which they always are.

Null policy (DESIGN §3.11, invariant 4): insufficient history returns
`None`. Never a shortened window, never a filled value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from capitalscan.core.config import BaselineParams


def horizon_drift_vol(
    mu_annual: float,
    sigma_annual: float,
    bp: BaselineParams,
) -> tuple[float, float]:
    """Annual drift and vol scaled to the reachability horizon.

    ```
    mu_h    = mu_annual * (h / trading_days)      # DESIGN §6.2: mu_ann / 50.4
    sigma_h = sigma_annual * sqrt(h / trading_days)
    ```

    Drift scales linearly in time and volatility with its square root, which
    is why the two divisors differ and why writing `50.4` as a literal
    anywhere would silently survive a horizon change.
    """
    if sigma_annual < 0:
        raise ValueError(f"sigma_annual must be non-negative, got {sigma_annual}")
    fraction = bp.horizon_days / bp.trading_days_per_year
    return float(mu_annual * fraction), float(sigma_annual * np.sqrt(fraction))


def parametric_baseline_series(
    target: float,
    mu_annual: np.ndarray,
    sigma_annual: np.ndarray,
    bp: BaselineParams,
) -> np.ndarray:
    """`P(R_h >= target)` for a whole array of trailing (drift, vol) pairs.

    ```
    P_base = 1 - Phi( (X - mu_h) / sigma_h )
    ```

    The vectorized form is the **only** implementation of the formula;
    `parametric_baseline` below is a scalar wrapper around it. The
    per-ticker-year path evaluates this once per trading day per target, and
    a Python-level loop over `scipy.stats.norm` there cost roughly a minute
    per null-test run for arithmetic numpy does instantly. Writing the
    formula twice to get both shapes is what invariant 2 forbids in the
    signal path and is no better an idea here.

    `sigma_h == 0` is degenerate rather than an error: a series with no
    variance reaches the target with probability 1 or 0 depending on whether
    the drift alone clears it.
    """
    mu_h, sigma_h = horizon_drift_vol_array(mu_annual, sigma_annual, bp)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sigma_h > 0, (target - mu_h) / sigma_h, 0.0)
        return np.asarray(
            np.where(sigma_h > 0, stats.norm.sf(z), np.where(mu_h >= target, 1.0, 0.0)),
            dtype="float64",
        )


def horizon_drift_vol_array(
    mu_annual: np.ndarray,
    sigma_annual: np.ndarray,
    bp: BaselineParams,
) -> tuple[np.ndarray, np.ndarray]:
    """`horizon_drift_vol` over arrays. Same two divisors, same reason."""
    sigma = np.asarray(sigma_annual, dtype="float64")
    if np.any(sigma < 0):
        raise ValueError("sigma_annual must be non-negative")
    fraction = bp.horizon_days / bp.trading_days_per_year
    return np.asarray(mu_annual, dtype="float64") * fraction, sigma * np.sqrt(fraction)


def parametric_baseline(
    target: float,
    mu_annual: float,
    sigma_annual: float,
    bp: BaselineParams,
) -> float:
    """`P(R_h >= target)` under a normal fitted to trailing drift and vol.

    DESIGN §6.2's worked example: at 30% annualized drift and 40% vol,
    `mu_5d = 0.60%`, `sigma_5d = 5.6%`, and `P(R_5d >= 2%) = 40.1%`, against
    36.1% at zero drift. Those four points arrive before any indicator
    fires, which is the whole reason the baseline is per ticker-year rather
    than pooled.
    """
    if sigma_annual < 0:
        raise ValueError(f"sigma_annual must be non-negative, got {sigma_annual}")
    return float(
        parametric_baseline_series(target, np.array([mu_annual]), np.array([sigma_annual]), bp)[0]
    )


def trailing_drift_vol(
    daily_returns: pd.Series,
    bp: BaselineParams,
) -> pd.DataFrame:
    """Per-day annualized drift and vol from the **strictly prior** window.

    `daily_returns` is indexed the same way the price series is, so
    `daily_returns[t]` is the return realized *on* day `t` and therefore
    reads `close[t]`. Including it in the window that predicts day `t`
    is lookahead. The `.shift(1)` below is what makes that impossible:
    the value at `t` is computed from returns `[t - window, t - 1]`.

    `min_periods` equals the full window, so a day without a complete prior
    window yields NaN rather than a shortened estimate (invariant 4).

    Returns a frame with `mu_annual` and `sigma_annual`, indexed like the
    input. Volatility uses the sample standard deviation (`ddof=1`), the
    estimator for an unknown mean, which is the case here — unlike the
    Bollinger band's population `ddof=0` (ADR 004), where the window *is*
    the population by construction.
    """
    if bp.trailing_window_days <= 1:
        raise ValueError(f"trailing_window_days must exceed 1, got {bp.trailing_window_days}")

    prior = daily_returns.shift(1)
    window = prior.rolling(bp.trailing_window_days, min_periods=bp.trailing_window_days)
    return pd.DataFrame(
        {
            "mu_annual": window.mean() * bp.trading_days_per_year,
            "sigma_annual": window.std(ddof=1) * np.sqrt(bp.trading_days_per_year),
        },
        index=daily_returns.index,
    )


def empirical_baseline(forward_returns: pd.Series, target: float) -> tuple[float | None, int]:
    """Realized fraction of days whose forward return reached `target`.

    Returns `(fraction, n_obs)`. `n_obs` counts only days with a complete
    forward window; the tail of the series is NaN there (DESIGN §3.11) and
    is dropped, never filled. A ticker-year with no complete window yields
    `(None, 0)` — the null that must propagate rather than a zero rate,
    which would read as "never reached" and is a different claim.
    """
    observed = forward_returns.dropna()
    n_obs = int(observed.size)
    if n_obs == 0:
        return None, 0
    return float((observed >= target).mean()), n_obs


def baseline_gap(empirical: float | None, parametric: float | None) -> float | None:
    """Signed `empirical - parametric`. Null in, null out."""
    if empirical is None or parametric is None:
        return None
    return float(empirical - parametric)


def disagreement_flag(gap: float | None, bp: BaselineParams) -> bool | None:
    """True when the two baselines disagree by more than `disagreement_pct`.

    Absolute value, both directions. Fat tails push the empirical rate above
    the normal-fitted one at far targets and below it at near ones, so a
    one-sided test would miss half of the cases the flag exists to surface.
    """
    if gap is None:
        return None
    return bool(abs(gap) > bp.disagreement_pct)


def event_weighted_baseline(
    per_event_baselines: pd.Series,
) -> tuple[float | None, int, int]:
    """A cell's baseline: the mean over its **events**, not over its days.

    DESIGN §6.2 and ADR 062 are explicit that a cell's baseline is the
    event-weighted mean of its constituent events' per-ticker-year
    baselines, never a pooled rate over all days. The difference is
    material: a cell where 90 of 100 events land in one high-drift
    ticker-year should carry that ticker-year's baseline almost entirely,
    and a pooled rate over the underlying days weights every ticker-year the
    same regardless of how many events it contributed.

    Pass one row per event, holding that event's ticker-year baseline.
    Returns `(baseline, n_used, n_null)`. Events whose ticker-year baseline
    is null are excluded from the mean and counted, so the caller can report
    how many (11.2 acceptance: "a cell containing such events reports how
    many"). All-null yields `(None, 0, n)`.
    """
    values = pd.to_numeric(per_event_baselines, errors="coerce")
    used = values.dropna()
    n_null = int(values.size - used.size)
    if used.size == 0:
        return None, 0, n_null
    return float(used.mean()), int(used.size), n_null
