"""Synthetic price paths for the statistical self-validation tests
(DESIGN §6.13, ADR 087).

The point of a null test is to run the real measurement code over data
that provably contains nothing, and confirm it reports nothing. That only
works if the generator is beyond suspicion, so everything here is pure,
seeded, and independently checkable:

- Log-returns are drawn iid from `N(mu_daily, sigma_daily^2)`. Driftless
  means `mu = 0` **in log space**, so the log-price is a driftless random
  walk and the reflection principle applies exactly in the continuous
  limit. This is the sense in which the null has "no signal": every
  conditioning variable a signal could key on is independent of every
  future return, by construction.
- `numpy.random.default_rng(seed)` per ticker, seeded from a base seed and
  the ticker index, so a single ticker's path is reproducible without
  regenerating the whole panel and a change in ticker count does not
  reshuffle existing tickers' paths.

No IO, no clock. This module lives in `research/` rather than `core/`
because it is a test/validation tool, not part of the signal path — but it
obeys invariant 1 anyway, so nothing stops a `core/` test from using it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import erfc

TRADING_DAYS_PER_YEAR = 252


def synthetic_ticker_names(n_tickers: int) -> list[str]:
    """`SYN000`, `SYN001`, ... — fixed width so lexical order is numeric
    order, and a prefix no real ticker uses, so a synthetic frame that
    leaks into a real query is obvious rather than silently plausible."""
    return [f"SYN{i:03d}" for i in range(n_tickers)]


def gbm_bars(
    n_tickers: int = 50,
    n_days: int = 2500,
    sigma_annual: float = 0.30,
    mu_annual: float = 0.0,
    start_price: float = 100.0,
    intrabar_range: float = 0.0,
    seed: int = 20260806,
) -> pd.DataFrame:
    """A panel of geometric random walks, one row per (ticker, day).

    Returns columns `ticker`, `ts`, `open`, `high`, `low`, `close` — the
    same names `bars` uses, so the frame drops into anything that reads
    split-adjusted OHLC with no translation layer.

    `mu_annual = 0.0` is the null: driftless in log space. Pass a non-zero
    value for the recovery test, where a *known* drift goes in and the
    measurement code has to recover it.

    `intrabar_range` is the half-width of the intraday excursion, as a
    fraction of the day's close, applied symmetrically:
    `high = close * (1 + r)`, `low = close * (1 - r)`. Zero (the default)
    makes `high = low = close`, which is the cleanest null — every
    reachability measurement then reads the same series the drift argument
    is stated about, with no extra intraday process to reason about. A
    non-zero value is deliberately symmetric so it cannot itself create a
    directional edge.

    Determinism (ADR 060): identical arguments produce an identical frame,
    including dtypes and row order. Ticker `i` depends only on `seed + i`,
    never on `n_tickers`.
    """
    if n_tickers <= 0 or n_days <= 0:
        raise ValueError(f"n_tickers and n_days must be positive; got {n_tickers}, {n_days}")
    if sigma_annual <= 0:
        raise ValueError(f"sigma_annual must be positive; got {sigma_annual}")
    if intrabar_range < 0:
        raise ValueError(f"intrabar_range must be non-negative; got {intrabar_range}")

    sigma_daily = sigma_annual / np.sqrt(TRADING_DAYS_PER_YEAR)
    mu_daily = mu_annual / TRADING_DAYS_PER_YEAR

    # A plain business-day index. The null test measures return geometry,
    # not calendar effects, so a real NYSE calendar would add holiday
    # handling without changing a single assertion.
    ts = pd.bdate_range("2010-01-04", periods=n_days, freq="C")

    frames = []
    for i, ticker in enumerate(synthetic_ticker_names(n_tickers)):
        rng = np.random.default_rng(seed + i)
        log_returns = rng.normal(loc=mu_daily, scale=sigma_daily, size=n_days)
        # Day 0 opens at `start_price`; the first drawn return moves it.
        close = start_price * np.exp(np.cumsum(log_returns))
        frames.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "ts": ts,
                    "open": close,
                    "high": close * (1.0 + intrabar_range),
                    "low": close * (1.0 - intrabar_range),
                    "close": close,
                }
            )
        )

    return pd.concat(frames, ignore_index=True)


def single_factor_bars(
    betas: tuple[float, ...],
    n_days: int = 1500,
    sigma_market_annual: float = 0.16,
    sigma_resid_annual: float = 0.20,
    resid_rho: float = 0.0,
    mu_annual: float = 0.0,
    start_price: float = 100.0,
    seed: int = 20260810,
) -> tuple[pd.DataFrame, pd.Series]:
    """A panel generated from a **known** single-factor model, plus its
    market series. Session 11.3's check on ADR 098's factor diagnostic.

    ```
    r_i = beta_i * r_m + eps_i
    eps_i = sigma_e * ( sqrt(resid_rho) * z_common + sqrt(1 - resid_rho) * z_i )
    ```

    Everything the factor-implied estimator claims to recover is an input
    here, so the analytical answer is available in closed form:

    ```
    sigma_i^2 = beta_i^2 sigma_m^2 + sigma_e^2
    rho_ij    = ( beta_i beta_j sigma_m^2 + resid_rho sigma_e^2 )
                / ( sigma_i sigma_j )
    ```

    `resid_rho = 0` is the case where the single-factor formula is exactly
    right, and the diagnostic must reproduce the analytical value.
    `resid_rho > 0` is the sector co-movement ADR 098 says the factor
    version omits: the empirical estimate must then come out **above** the
    factor-implied one and `rho_gap` must be positive. That direction is the
    whole basis for treating the factor value as a diagnostic rather than as
    the correction, so it is generated, not assumed.

    Returns `(bars, market_close)`. `bars` matches `gbm_bars`' columns; the
    market series is a close series indexed by the same dates, which is the
    shape `market_days.spx_close` arrives in.

    Determinism: one generator seeded from `seed` drives the market and the
    common residual factor; ticker `i`'s idiosyncratic draw comes from
    `seed + 1 + i`, so adding a ticker leaves the existing ones' paths
    unchanged, matching `gbm_bars`' guarantee.
    """
    if not betas:
        raise ValueError("betas must not be empty")
    if n_days <= 0:
        raise ValueError(f"n_days must be positive; got {n_days}")
    if sigma_market_annual <= 0 or sigma_resid_annual < 0:
        raise ValueError(
            f"sigma_market_annual must be positive and sigma_resid_annual non-negative; "
            f"got {sigma_market_annual}, {sigma_resid_annual}"
        )
    if not (0.0 <= resid_rho <= 1.0):
        raise ValueError(f"resid_rho must be in [0, 1]; got {resid_rho}")

    sigma_m = sigma_market_annual / np.sqrt(TRADING_DAYS_PER_YEAR)
    sigma_e = sigma_resid_annual / np.sqrt(TRADING_DAYS_PER_YEAR)
    mu_daily = mu_annual / TRADING_DAYS_PER_YEAR

    shared = np.random.default_rng(seed)
    market_returns = shared.normal(loc=0.0, scale=sigma_m, size=n_days)
    common_resid = shared.normal(loc=0.0, scale=1.0, size=n_days)

    ts = pd.bdate_range("2010-01-04", periods=n_days, freq="C")
    frames = []
    for i, (ticker, beta) in enumerate(zip(synthetic_ticker_names(len(betas)), betas)):
        rng = np.random.default_rng(seed + 1 + i)
        idiosyncratic = rng.normal(loc=0.0, scale=1.0, size=n_days)
        eps = sigma_e * (
            np.sqrt(resid_rho) * common_resid + np.sqrt(1.0 - resid_rho) * idiosyncratic
        )
        log_returns = mu_daily + beta * market_returns + eps
        close = start_price * np.exp(np.cumsum(log_returns))
        frames.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "ts": ts,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "adj_close": close,
                }
            )
        )

    market_close = pd.Series(start_price * np.exp(np.cumsum(market_returns)), index=ts)
    return pd.concat(frames, ignore_index=True), market_close


def analytical_pair_correlation(
    beta_i: float,
    beta_j: float,
    sigma_market_annual: float,
    sigma_resid_annual: float,
    resid_rho: float = 0.0,
) -> float:
    """The correlation `single_factor_bars` was generated to have.

    The closed form in that function's docstring, in one place so a test
    cannot quietly rewrite it while asserting against it.
    """
    var_m = sigma_market_annual**2
    var_e = sigma_resid_annual**2
    sigma_i = np.sqrt(beta_i**2 * var_m + var_e)
    sigma_j = np.sqrt(beta_j**2 * var_m + var_e)
    return float((beta_i * beta_j * var_m + resid_rho * var_e) / (sigma_i * sigma_j))


def analytical_touch_probability(target: float, sigma_annual: float, horizon_days: int) -> float:
    """P(a driftless log random walk touches `+target` within
    `horizon_days`), by the reflection principle.

    For driftless Brownian motion `W` with per-day variance `sigma_d^2`,
    the running maximum satisfies

        P( max_{t <= T} W_t >= a ) = 2 * P( W_T >= a )

    so with `a = log(1 + target)` and `s = sigma_d * sqrt(T)`:

        p = 2 * (1 - Phi(a / s)) = erfc( a / (s * sqrt(2)) )

    This is the **continuous-monitoring** value. Daily bars monitor the
    path at 252 points a year, not continuously, so a discretely-sampled
    walk touches strictly less often than this — the barrier can be crossed
    and recrossed between two closes. Treat this as an upper bound, not an
    equality target; the tests here assert `measured <= analytical` and
    check the two-sided symmetry separately, which is the part discretization
    cannot bias.
    """
    sigma_daily = sigma_annual / np.sqrt(TRADING_DAYS_PER_YEAR)
    a = np.log1p(target)
    s = sigma_daily * np.sqrt(horizon_days)
    return float(erfc(a / (s * np.sqrt(2.0))))
