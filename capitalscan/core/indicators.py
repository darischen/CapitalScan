"""Indicator registry and computation. Pure functions, no IO (DESIGN §3.1).

Every indicator registers through `@register`, declaring its upstream
dependencies, warmup length, and storage dtype (ADR 051). `compute_all`
iterates the registry so the `indicators` job's read-window expansion
(`max_warmup() * 1.6` calendar days) adapts automatically when a new
indicator is added — no separate constant to keep in sync.

Formulas are pinned in DESIGN §3.5 / ADR 004 and are not re-derived here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from capitalscan.core.config import IndicatorParams

IndicatorFn = Callable[[pd.DataFrame, IndicatorParams], pd.DataFrame]


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    deps: tuple[str, ...]
    warmup: int
    dtype: str
    fn: IndicatorFn


_REGISTRY: dict[str, IndicatorSpec] = {}


def register(
    name: str, deps: list[str], warmup: int, dtype: str
) -> Callable[[IndicatorFn], IndicatorFn]:
    """Decorator registering an indicator function under `name`.

    `fn` takes one ticker's bars plus `IndicatorParams` and returns a
    DataFrame of one or more output columns, indexed identically to `bars`.
    Grouping several related output columns (e.g. bb_mid/bb_upper/bb_lower)
    under one registration is expected where they share a computation.
    """

    def decorator(fn: IndicatorFn) -> IndicatorFn:
        if name in _REGISTRY:
            raise ValueError(f"indicator '{name}' already registered")
        _REGISTRY[name] = IndicatorSpec(
            name=name, deps=tuple(deps), warmup=warmup, dtype=dtype, fn=fn
        )
        return fn

    return decorator


def registry() -> dict[str, IndicatorSpec]:
    return dict(_REGISTRY)


def max_warmup() -> int:
    """Longest warmup across all registered indicators, in trading bars."""
    if not _REGISTRY:
        return 0
    return max(spec.warmup for spec in _REGISTRY.values())


def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Fraction of the trailing window at or below the current value, in [0, 1].

    Strict trailing: the window for row t is rows [t-window+1, t], i.e. it
    includes the current value. `min_periods=window` so a short history
    yields NaN rather than a percentile computed against a partial window.
    """

    def _frac_at_or_below(w: np.ndarray) -> float:
        current = w[-1]
        return float(np.mean(w <= current))

    return series.rolling(window=window, min_periods=window).apply(_frac_at_or_below, raw=True)


@register("bollinger", deps=["close"], warmup=272, dtype="numeric(12,6)")
def bollinger(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Bollinger Bands on split-adjusted close (ADR 004).

    bb_width_pct uses the same 252-day percentile window as rv_pct_252d
    (DESIGN doesn't pin a separate window for it), which is why this
    registration's warmup matches realized_vol's.
    """
    close = bars["close"]
    mid = close.rolling(window=p.bb_window, min_periods=p.bb_window).mean()
    std = close.rolling(window=p.bb_window, min_periods=p.bb_window).std(ddof=p.bb_ddof)
    upper = mid + p.bb_std * std
    lower = mid - p.bb_std * std
    pctb = (close - lower) / (upper - lower)
    width = (upper - lower) / mid
    width_pct = rolling_percentile(width, p.rv_pct_window)

    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": upper,
            "bb_lower": lower,
            "bb_pctb": pctb,
            "bb_width": width,
            "bb_width_pct": width_pct,
        },
        index=bars.index,
    )


@register("stochastic", deps=["high", "low", "close"], warmup=20, dtype="numeric(12,6)")
def stochastic(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Fast and Full Stochastic on split-adjusted OHLC (ADR 004).

    When the trailing high equals the trailing low (halted or fully flat
    session), %K is NaN rather than 0 or 50 — a filled value would
    fabricate an extreme reading (DESIGN §3.5).
    """
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]

    hh = high.rolling(window=p.stoch_window, min_periods=p.stoch_window).max()
    ll = low.rolling(window=p.stoch_window, min_periods=p.stoch_window).min()
    span = hh - ll

    k_fast = pd.Series(np.where(span == 0, np.nan, 100 * (close - ll) / span), index=bars.index)
    d_fast = k_fast.rolling(window=p.stoch_smooth_k, min_periods=p.stoch_smooth_k).mean()
    k_full = k_fast.rolling(window=p.stoch_smooth_k, min_periods=p.stoch_smooth_k).mean()
    d_full = k_full.rolling(window=p.stoch_smooth_d, min_periods=p.stoch_smooth_d).mean()

    # Crossover is a feature only, never a gate (ADR 045).
    k_cross_up = (k_full.shift(1) <= d_full.shift(1)) & (k_full > d_full)
    k_cross_down = (k_full.shift(1) >= d_full.shift(1)) & (k_full < d_full)
    k_cross_up = k_cross_up.where(k_full.notna() & d_full.notna())
    k_cross_down = k_cross_down.where(k_full.notna() & d_full.notna())

    return pd.DataFrame(
        {
            "k_fast": k_fast,
            "d_fast": d_fast,
            "k_full": k_full,
            "d_full": d_full,
            "k_cross_up": k_cross_up,
            "k_cross_down": k_cross_down,
        },
        index=bars.index,
    )


@register("atr", deps=["high", "low", "close"], warmup=15, dtype="numeric(12,6)")
def atr(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Average True Range on split-adjusted OHLC, simple moving average of
    true range (not Wilder's exponential smoothing — DESIGN pins no
    smoothing method beyond "ATR_14", so SMA keeps it consistent with
    every other pinned formula, which are all SMA-based)."""
    high = bars["high"]
    low = bars["low"]
    prev_close = bars["close"].shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_14 = tr.rolling(window=p.atr_window, min_periods=p.atr_window).mean()
    return pd.DataFrame({"atr_14": atr_14}, index=bars.index)


@register("realized_vol", deps=["adj_close"], warmup=272, dtype="numeric(12,6)")
def realized_vol(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Realized volatility on TOTAL-RETURN adjusted close.

    Exception to the split-adjusted-close rule (§2.2): this measures
    return dispersion rather than price level, so dividend adjustment is
    correct here and nowhere else in this module.
    """
    adj_close = bars["adj_close"]
    # np.log on a Series is typed as returning ndarray by numpy's stubs even
    # though it returns a Series at runtime (pandas overrides __array_ufunc__);
    # wrap explicitly so downstream .rolling() type-checks.
    log_ret = pd.Series(np.log(adj_close / adj_close.shift(1)), index=bars.index)
    rv_20d = log_ret.rolling(window=p.rv_window, min_periods=p.rv_window).std(ddof=0) * np.sqrt(252)
    rv_pct_252d = rolling_percentile(rv_20d, p.rv_pct_window)

    return pd.DataFrame({"rv_20d": rv_20d, "rv_pct_252d": rv_pct_252d}, index=bars.index)


@register("drawdown_from_high", deps=["close"], warmup=252, dtype="numeric(12,6)")
def drawdown_from_high(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Drawdown from the trailing 52-week (dd_window) high, split-adjusted
    close. Reported as a positive fraction, matching StatsParams.dd_buckets
    (0.10, 0.20, 0.35 — all positive)."""
    close = bars["close"]
    rolling_high = close.rolling(window=p.dd_window, min_periods=p.dd_window).max()
    dd_52w = (rolling_high - close) / rolling_high
    return pd.DataFrame({"dd_52w": dd_52w}, index=bars.index)


@register("bull_close_below_lower", deps=["open", "close"], warmup=272, dtype="boolean")
def bull_close_below_lower(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """An up bar closing at or below **that same day's** lower band (ADR 144).

    ```
    close > open  AND  close <= bb_lower[t]
    ```

    The long-side mirror of `bear_close_above_upper`, and deliberately a
    mirror in every respect that matters -- the same band lag, the same
    same-day band, the same `_breach` call, the same null-through-warmup
    treatment. Where the two differ it is only by `Bound` and by the
    direction of the open/close comparison, because any *other* difference
    between them would be a claim that the long and short sides behave
    asymmetrically, which ADR 016 says must be measured rather than assumed.

    **Why here and not in `core/signals.py`**: identical to the bear case.
    `detect` may read only `low`, `high`, `ts` and `ticker` from the bar --
    the signature probe pins it and CLAUDE.md calls that probe the real
    guarantee. This needs `open` and `close`, so it goes on the indicator
    row that `detect` already receives.

    **The band is bar t's own**, per ADR 109's correction to ADR 108. At the
    closing bell today's band is fully computable from information that
    already exists, so reading it is not look-ahead; and the circularity runs
    conservative in this direction too -- a low close *lowers* the band and
    makes it harder to close beneath.

    `bear_close_band_lag` governs both flags. One field rather than two,
    because the lag is a statement about how a close-confirmed band is read,
    not about which side is being read -- and two fields would let the sides
    silently disagree, which is invariant 9's failure mode.

    **Structural consequence, mirroring the bear case.** `bars_check1`
    enforces `close >= low`, so `close <= bb_lower[t]` implies
    `low <= bb_lower[t]`: every flagged bar necessarily also touched its own
    lower band. The flag refines an existing population rather than creating
    a new one.
    """
    from capitalscan.core.signals import _breach
    from capitalscan.core.types import Bound

    lower = bollinger(bars, p)["bb_lower"]
    if p.bear_close_band_lag:
        lower = lower.shift(p.bear_close_band_lag)
    close = bars["close"]
    at_or_below = pd.Series(
        [
            _breach(float(c), float(lo), Bound.LOWER)
            for c, lo in zip(close.to_numpy(), lower.to_numpy())
        ],
        index=bars.index,
    )
    flag = (close > bars["open"]) & at_or_below
    return pd.DataFrame(
        {"bull_close_below_lower": flag.where(lower.notna())},
        index=bars.index,
    )


@register("bear_close_above_upper", deps=["open", "close"], warmup=272, dtype="boolean")
def bear_close_above_upper(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """A down bar closing at or above **that same day's** upper band (ADR 109).

    ```
    open > close  AND  close >= bb_upper[t]
    ```

    **Why this lives in the indicator registry rather than in
    `core/signals.py`.** `detect` may read only `low`, `high`, `ts`, and
    `ticker` from the bar — the signature probe in
    `tests/unit/test_signature_guarantee.py` asserts it, and CLAUDE.md calls
    that probe "the real guarantee." This condition needs `open` and
    `close`, so evaluating it inside `detect` would mean widening the one
    thing the project pins hardest. Computing it here instead puts the
    answer on the indicator row, which `detect` already receives.

    **The band is bar t's own, and that is a correction** (ADR 109, amending
    ADR 108, 2026-08-14). The first implementation shifted to `bb_upper[t-1]`
    on the reasoning that today's band embeds today's close and testing one
    against the other is circular. That reasoning over-applied invariant 3,
    which exists to stop the close deciding an **intraday** event. Here the
    event *is* the close: at the closing bell today's band is fully
    computable from information that already exists, so reading it is not
    look-ahead. The circularity is also conservative rather than permissive —
    a high close raises the band and makes it *harder* to clear.

    The shift was measurably wrong, not merely unconventional. Across
    2010-2026 the shifted form fires 44,114 times and this form 20,146, and
    **24,104 of the shifted fires (55%) never cleared the band a chart would
    draw**. Bands rise in an uptrend, so `bb_upper[t-1]` sits below
    `bb_upper[t]` and presents a lower bar. STT on 2026-08-13 is the worked
    example: close 189.85 cleared the prior band at 189.758 by nine cents and
    missed the same-day band at 190.451 by sixty. Every charting platform
    computes it this way, so the shifted version disagreed with the chart the
    signal exists to describe.

    Warmup is `bollinger`'s 272, no longer 273 — the extra bar existed only
    to feed the shift.

    **The comparison is "at or above," matching `core.signals._breach`'s
    `price_tolerance = 0.0` convention** ("at or beyond, exact"). It routes
    through `_breach` for exactly the reason invariant 2 exists: a second
    inline band comparison in this repo is how the two would drift apart.

    Null through warmup, never False (invariant 4). "No band yet" is not
    "did not fire," and a False there would read as a measured negative.

    **Structural consequence, and it is load-bearing.** `bars_check1`
    enforces `close <= high`, so `close >= bb_upper[t]` implies
    `high >= bb_upper[t]` — every flagged bar necessarily also touched its
    own upper band. The flag therefore refines an existing population rather
    than creating a new one. Note this is now a statement about the *same
    day's* band on both sides, which is the stronger and more obvious form of
    the guarantee.
    """
    from capitalscan.core.signals import _breach
    from capitalscan.core.types import Bound

    # `bear_close_band_lag` is 0 (bar t's own band, ADR 109) and exists so the
    # choice lives in `config_hash`; 1 restores ADR 108's shifted rule.
    upper = bollinger(bars, p)["bb_upper"]
    if p.bear_close_band_lag:
        upper = upper.shift(p.bear_close_band_lag)
    close = bars["close"]
    at_or_above = pd.Series(
        [
            _breach(float(c), float(u), Bound.UPPER)
            for c, u in zip(close.to_numpy(), upper.to_numpy())
        ],
        index=bars.index,
    )
    flag = (bars["open"] > close) & at_or_above
    return pd.DataFrame(
        {"bear_close_above_upper": flag.where(upper.notna())},
        index=bars.index,
    )


@register("sma_slope", deps=["close"], warmup=260, dtype="numeric(12,6)")
def sma_slope(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """200-day SMA and its 60-day trailing slope, split-adjusted close."""
    close = bars["close"]
    sma_200 = close.rolling(window=p.sma_long, min_periods=p.sma_long).mean()
    sma_prior = sma_200.shift(p.sma_slope_window)
    slope = (sma_200 - sma_prior) / sma_prior
    return pd.DataFrame({"sma_200": sma_200, "sma200_slope_60": slope}, index=bars.index)


@register("volume_zscore", deps=["volume"], warmup=20, dtype="numeric(12,6)")
def volume_zscore(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Z-score of volume against its trailing 20-day mean and population std."""
    volume = bars["volume"]
    mean = volume.rolling(window=p.vol_z_window, min_periods=p.vol_z_window).mean()
    std = volume.rolling(window=p.vol_z_window, min_periods=p.vol_z_window).std(ddof=0)
    vol_z_20d = pd.Series(np.where(std == 0, np.nan, (volume - mean) / std), index=bars.index)
    return pd.DataFrame({"vol_z_20d": vol_z_20d}, index=bars.index)


def compute_all(bars: pd.DataFrame, p: IndicatorParams) -> pd.DataFrame:
    """Run every registered indicator over one ticker's bars.

    One ticker in, one row per bar out, columns matching the `indicators`
    table exactly — no translation layer (DESIGN §3.2).
    """
    frames = [spec.fn(bars, p) for spec in _REGISTRY.values()]
    return pd.concat(frames, axis=1)
