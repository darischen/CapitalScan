"""Return measurement and entry pricing. Pure functions, no IO (DESIGN §3.1).

Price series matters here. `forward_returns` measures return, so its caller
passes **total-return adjusted close** (`adj_close`). `entry_price_for` and
`mfe_mae` price actual fills, so their callers pass **split-adjusted** OHLC —
the series the market traded at and the bands live in (DESIGN §2.2).

Coverage constraint (DESIGN §3.8): `TOUCH_5M` and `TOUCH_30M` need hourly
bars, which reach back only ~730 days. Before that they return NaN. Never a
fabricated value — the entry-timing sweep splits into two claims, and the
fine-grained one carries its coverage limitation with it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bound, EntryKind, Side

# TOUCH_5M interpolates 5 minutes into the hourly bar that contains the
# touch. DESIGN §5.4 pins the source ("interpolated from the first hourly
# bar after touch") but not the weight; linear in time from the touch level
# to that bar's close is the only reading, so it is written once here.
_MINUTES_PER_HOURLY_BAR = 60.0
_TOUCH_5M_MINUTES = 5.0


def realized_return(entry_price: float, exit_price: float, side: Side) -> float:
    """Return actually realized between the two fills, gross of costs."""
    if _isnan(entry_price) or _isnan(exit_price) or float(entry_price) == 0.0:
        return float("nan")
    raw = (float(exit_price) - float(entry_price)) / float(entry_price)
    return raw if side is Side.LONG else -raw


def forward_returns(close: pd.Series, horizons: list[int]) -> pd.DataFrame:
    """Unconditional forward returns at each horizon, for baseline comparison.

    Pass total-return adjusted close: dividends are real return and belong
    in the payoff (DESIGN §2.2). The tail is NaN where the horizon runs past
    the end of the series — never filled (DESIGN §3.11).
    """
    out = {f"fwd_ret_{h}d": close.shift(-h) / close - 1.0 for h in horizons}
    return pd.DataFrame(out, index=close.index)


def mfe_mae(entry_price: float, side: Side, fwd_bars: pd.DataFrame) -> tuple[float, float, int]:
    """Max favorable excursion, max adverse excursion, and time-to-MFE.

    Measured over the rows supplied, which the exit resolver bounds at
    `[t+1, exit_idx]` (DESIGN §5.6). `time_to_mfe` counts bars from entry,
    1-based, and takes the first bar reaching the peak.

    MFE is **not** clamped at zero. A position that gapped down and never
    recovered has negative MFE, which is why DESIGN §5.6 stores
    `capture_ratio` as null when MFE <= 0.
    """
    if len(fwd_bars) == 0:
        raise ValueError("mfe_mae requires at least one forward bar")
    entry = float(entry_price)
    if side is Side.LONG:
        favorable = (fwd_bars["high"] - entry) / entry
        adverse = (fwd_bars["low"] - entry) / entry
    else:
        favorable = (entry - fwd_bars["low"]) / entry
        adverse = (entry - fwd_bars["high"]) / entry

    mfe = float(favorable.max())
    mae = float(adverse.min())
    time_to_mfe = int(np.argmax(favorable.to_numpy())) + 1
    return mfe, mae, time_to_mfe


def _first_hourly_touch(hourly: pd.DataFrame, touch_level: float, side: Side) -> pd.Series | None:
    """First hourly bar whose range reaches `touch_level`, or None."""
    price_col, bound = ("low", Bound.LOWER) if side is Side.LONG else ("high", Bound.UPPER)
    for _, hbar in hourly.iterrows():
        if _breach(float(hbar[price_col]), touch_level, bound):
            return hbar
    return None


def entry_price_for(
    kind: EntryKind,
    bar: pd.Series,
    next_bar: pd.Series | None,
    touch_level: float,
    side: Side = Side.LONG,
    hourly: pd.DataFrame | None = None,
) -> float:
    """Fill price for one entry timing (DESIGN §5.4). Slippage applies on top.

    `TOUCH` carries the gap rule: a bar that opened past the band never
    traded at the band, so it fills at the open instead. For a long,
    `P = open if open <= L else L`; mirrored for a short.

    `side` decides which direction counts as "gapped through" — the caller
    knows it from the signal, and the band level alone cannot say.
    """
    if kind is EntryKind.TOUCH:
        if _isnan(touch_level):
            return float("nan")
        open_ = float(bar["open"])
        bound = Bound.LOWER if side is Side.LONG else Bound.UPPER
        return open_ if _breach(open_, touch_level, bound) else float(touch_level)

    if kind is EntryKind.NEXT_OPEN:
        # A terminal bar has no next session: null, not the current close.
        return float("nan") if next_bar is None else float(next_bar["open"])

    if kind in (EntryKind.TOUCH_5M, EntryKind.TOUCH_30M):
        if hourly is None or len(hourly) == 0 or _isnan(touch_level):
            return float("nan")
        hbar = _first_hourly_touch(hourly, float(touch_level), side)
        if hbar is None:
            return float("nan")
        close = float(hbar["close"])
        if kind is EntryKind.TOUCH_30M:
            return close
        weight = _TOUCH_5M_MINUTES / _MINUTES_PER_HOURLY_BAR
        return float(touch_level) + (close - float(touch_level)) * weight

    raise ValueError(kind)
