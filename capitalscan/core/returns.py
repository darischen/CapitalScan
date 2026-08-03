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


def _favorable_adverse_series(
    entry_price: float, side: Side, bars: pd.DataFrame
) -> tuple[pd.Series, pd.Series]:
    """Vectorized, direction-neutral per-bar excursion from `entry_price`.

    The one implementation of the excursion formula (invariant 2). `mfe_mae`
    reduces this to a max/min over the window it's given; `path_for_event`
    (Session 10) uses it directly, one value per forward day — neither
    writes a second `(high - entry) / entry`-shaped formula of its own.
    """
    entry = float(entry_price)
    if side is Side.LONG:
        favorable = (bars["high"] - entry) / entry
        adverse = (bars["low"] - entry) / entry
    else:
        favorable = (entry - bars["low"]) / entry
        adverse = (entry - bars["high"]) / entry
    return favorable, adverse


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
    favorable, adverse = _favorable_adverse_series(entry_price, side, fwd_bars)
    mfe = float(favorable.max())
    mae = float(adverse.min())
    time_to_mfe = int(np.argmax(favorable.to_numpy())) + 1
    return mfe, mae, time_to_mfe


def entry_offset_for(entry_kind: EntryKind) -> int:
    """Trading-day offset of an entry fill from its signal date.

    `TOUCH`, `TOUCH_5M`, and `TOUCH_30M` all fill on the signal bar itself
    (offset 0). `NEXT_OPEN` fills exactly one trading day later, always —
    never more, because `entry_price_for`'s own `NEXT_OPEN` branch either
    returns the very next bar's open or, if there is no next bar, `NaN`
    (a position that never filled, which never reaches the path table at
    all). This makes the offset a pure function of `entry_kind`, needing
    no price lookup — the path table's `day_offset` column (DESIGN §5.7b)
    counts from the signal date, not the entry date, and this is the
    translation between the two anchors every path-derived, entry-anchored
    quantity (MFE, MAE, reachability, `fwd_ret_*d`) needs.
    """
    return 1 if entry_kind is EntryKind.NEXT_OPEN else 0


def path_for_event(entry_price: float, side: Side, fwd_bars: pd.DataFrame) -> pd.DataFrame:
    """Per-day forward path (DESIGN §5.7b): one row per trading day after
    whatever date `fwd_bars` starts at (the caller decides — Task 10.2's
    caller passes bars starting the day after `signal_date`), with
    direction-neutral `favorable`/`adverse` extremes and a `terminal`
    mark, all anchored to `entry_price` and expressed as a return
    fraction.

    Vectorized over the whole `fwd_bars` frame at once (perf follow-up:
    the original per-bar Python loop cost ~3.5ms/event, which at the real
    event count — ~2M filled entries — priced out to ~1.9 hours of pure
    compute before a single row was written). Reuses
    `_favorable_adverse_series` for `favorable`/`adverse` (invariant 2, the
    same formula `mfe_mae` reduces to a max/min) and mirrors
    `realized_return`'s exact one-line formula (`raw = (exit - entry) /
    entry`, sign-flipped for a short) for `terminal`, applied to the whole
    `close` column at once rather than called once per bar — the formula
    is unchanged, only the call shape is.

    Split-adjusted OHLC only (`high`, `low`, `close`) — never `adj_close`.
    This intentionally does not match `forward_returns`' total-return
    convention; see the plan header ("Price series discipline") for why.

    `day_offset` is 1-based and contiguous, matching the row's position in
    `fwd_bars` — never padded past however many rows `fwd_bars` actually
    has (invariant 4: a short window near the end of price history yields
    fewer rows, never a filled/interpolated one).
    """
    columns = ["day_offset", "favorable", "adverse", "terminal"]
    if len(fwd_bars) == 0:
        return pd.DataFrame(columns=columns)

    favorable, adverse = _favorable_adverse_series(entry_price, side, fwd_bars)
    entry = float(entry_price)
    raw_terminal = (fwd_bars["close"] - entry) / entry
    terminal = raw_terminal if side is Side.LONG else -raw_terminal

    return pd.DataFrame(
        {
            "day_offset": range(1, len(fwd_bars) + 1),
            "favorable": favorable.to_numpy(),
            "adverse": adverse.to_numpy(),
            "terminal": terminal.to_numpy(),
        },
        columns=columns,
    )


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

        # Same gap principle as TOUCH (DESIGN §5.4), applied at hourly
        # granularity. `_first_hourly_touch` picks the first hourly bar whose
        # extreme reaches `touch_level` — on a gap day that condition is
        # trivially true on the session's first bar even though the level
        # sits outside the whole day's range, because the bar already opened
        # past it. Interpolating from `touch_level` in that case anchors on a
        # price nobody traded. If the bar's own open already breached the
        # level, the level was never available inside this bar either; the
        # only real anchor is the open. Otherwise the bar genuinely straddles
        # the level and `touch_level` remains the honest anchor, unchanged
        # from before.
        open_ = float(hbar["open"])
        bound = Bound.LOWER if side is Side.LONG else Bound.UPPER
        anchor = open_ if _breach(open_, float(touch_level), bound) else float(touch_level)
        weight = _TOUCH_5M_MINUTES / _MINUTES_PER_HOURLY_BAR
        return anchor + (close - anchor) * weight

    raise ValueError(kind)
