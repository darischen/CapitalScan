"""Signal detection. Pure functions, no IO (DESIGN §3.1).

This is the load-bearing module. `_breach` is the **only** band comparison
in the repo (CLAUDE.md invariant 2) — `core/exits.py` routes its band, stop,
and target comparisons through it too. The backtest calls `detect`; the
poller calls `breach_live`. Both reduce to `_breach` with the same level and
bound, differing only in the price argument, which is correct because a live
quote is a point sample of the same intraday path (DESIGN §3.6, ADR 006).

**Indicators are read at t-1, never t.** `detect` receives the t-1 indicator
row and never derives an indicator from `bar`. %K is a function of the close,
so evaluating it at t would use the close to decide an intraday entry. This
is the highest-risk silent failure in the system and it is guarded again at
the job level (DESIGN §4.7).

Debounce and cluster tagging live in the job layer: they need cross-day
state, which `core/` cannot hold.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from capitalscan.core.config import SignalParams
from capitalscan.core.types import Bands, Bound, Side, SignalHit, SignalType

# Specificity ranking, fixed by ADR 057. Most specific first, mirrored across
# sides. `signal_type` takes the first entry that fired; `signal_types_all`
# preserves this order so the stored array is deterministic.
_SPECIFICITY: dict[Side, tuple[SignalType, ...]] = {
    Side.LONG: (
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    ),
    Side.SHORT: (
        SignalType.CONFLUENCE_HIGH,
        SignalType.BB_UPPER_TOUCH,
        SignalType.STOCH_OVERBOUGHT,
    ),
}


def _breach(price: float, level: float, bound: Bound, tol: float = 0.0) -> bool:
    """The single comparison. Everything routes through here.

    Prices are rounded to 4 decimals before comparing (DESIGN §3.2), so a
    float artifact below a hundredth of a cent never decides an event.
    `tol` widens the trigger as a fraction of the level; the default 0.0
    means "at or beyond", exactly.

    `Bound.MID` raises: mid is a level, not a direction. A long exiting into
    the mid band is an UPPER-direction comparison and callers must say so.
    """
    if _isnan(price) or _isnan(level):
        return False
    p, level_r = round(float(price), 4), round(float(level), 4)
    if bound is Bound.LOWER:
        return p <= level_r * (1 + tol)
    if bound is Bound.UPPER:
        return p >= level_r * (1 - tol)
    raise ValueError(bound)


def _isnan(x: object) -> bool:
    """NaN check that tolerates None and non-float inputs."""
    if x is None:
        return True
    try:
        return bool(np.isnan(float(x)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return True


def _fast_agrees(ind: pd.Series | Bands, sp: SignalParams) -> bool:
    """ADR 044: fast/full Stochastic agreement as a tolerance band.

    Scoped to the confluence definitions, which is where DESIGN §3.6 attaches
    it. A missing or null %K_fast fails the check rather than passing it —
    the flag exists to *restrict* the event set.
    """
    if not sp.require_fast_agreement:
        return True
    k_fast = _get(ind, "k_fast")
    k_full = _get(ind, "k_full")
    if _isnan(k_fast) or _isnan(k_full):
        return False
    return abs(float(k_fast) - float(k_full)) <= sp.fast_agreement_tol


def _get(row: pd.Series | Bands, field: str) -> float:
    """Read one field from either an indicator row or a `Bands` dataclass.

    The two carry identical shape by design (DESIGN §3.4) — this is the
    mechanism behind ADR 006 — so the condition logic below reads from
    either without branching.
    """
    if isinstance(row, Bands):
        return float(getattr(row, field))
    try:
        value = row[field]
    except KeyError:
        return float("nan")
    return float(value) if value is not None else float("nan")


def _types_fired(
    low: float,
    high: float,
    ind: pd.Series | Bands,
    sp: SignalParams,
) -> dict[Side, list[SignalType]]:
    """Evaluate every pinned condition and group the results by side.

    `low` and `high` are the prices tested against the lower and upper band.
    The backtest passes the bar's intraday extremes (ADR 005); the live path
    passes the current quote for both. Stochastic conditions read `ind`,
    which is always the t-1 row.
    """
    bb_lower = _get(ind, "bb_lower")
    bb_upper = _get(ind, "bb_upper")
    k_full = _get(ind, "k_full")

    lower_touch = _breach(low, bb_lower, Bound.LOWER, sp.price_tolerance)
    upper_touch = _breach(high, bb_upper, Bound.UPPER, sp.price_tolerance)
    oversold = not _isnan(k_full) and k_full <= sp.stoch_oversold
    overbought = not _isnan(k_full) and k_full >= sp.stoch_overbought
    agrees = _fast_agrees(ind, sp)

    fired: dict[Side, list[SignalType]] = {Side.LONG: [], Side.SHORT: []}
    if lower_touch and oversold and agrees:
        fired[Side.LONG].append(SignalType.CONFLUENCE_LOW)
    if lower_touch:
        fired[Side.LONG].append(SignalType.BB_LOWER_TOUCH)
    if oversold:
        fired[Side.LONG].append(SignalType.STOCH_OVERSOLD)
    if upper_touch and overbought and agrees:
        fired[Side.SHORT].append(SignalType.CONFLUENCE_HIGH)
    if upper_touch:
        fired[Side.SHORT].append(SignalType.BB_UPPER_TOUCH)
    if overbought:
        fired[Side.SHORT].append(SignalType.STOCH_OVERBOUGHT)
    return fired


def _bar_date(bar: pd.Series) -> date:
    """The bar's trading date, from the index label or a `ts` column."""
    label: object = bar.name
    if label is None and "ts" in bar.index:
        label = bar["ts"]
    if isinstance(label, pd.Timestamp):
        return label.date()
    if isinstance(label, datetime):
        return label.date()
    if isinstance(label, date):
        return label
    return pd.Timestamp(label).date()  # type: ignore[arg-type]


def detect(bar: pd.Series, ind: pd.Series, sp: SignalParams) -> list[SignalHit]:
    """Backtest path. One bar plus its t-1 indicator row.

    Band conditions read `bar["low"]` and `bar["high"]`, because a daily
    bar's extremes are the intraday touch (ADR 005). Stochastic conditions
    read `ind`, which the caller must supply from bar t-1.

    **The t-1 guarantee is structural, and this signature is what carries
    it** (TESTS.md §3.1b). `ind` is one row, not a frame, so bar t's
    indicators are not in scope and no code path can reach them. Only four
    fields are ever read from `bar`: `low`, `high`, `ts`, and `ticker` —
    none of them an indicator. Widening either of those is how look-ahead
    would get reintroduced, so both are asserted by test rather than left
    to review.

    Concurrent signals on one side collapse to a single hit carrying the
    most specific `signal_type`, every concurrent type in `signal_types_all`,
    and their count in `signal_strength` (ADR 057). Both sides can fire on
    one bar — a wide bar touching both bands — which yields two hits, long
    first. Crossover flags are never consulted (ADR 045).
    """
    if isinstance(ind, pd.DataFrame):
        # A frame here would put every bar's indicators in scope, which is
        # exactly the look-ahead this signature exists to prevent. Passing
        # one "for convenience" must fail loudly, not silently widen access.
        raise TypeError("detect() takes a single indicator row (pd.Series), not a DataFrame")

    raw_ticker = bar["ticker"] if "ticker" in bar.index else ""
    ticker = str(raw_ticker) if raw_ticker is not None else ""

    fired = _types_fired(_get(bar, "low"), _get(bar, "high"), ind, sp)
    if not any(fired.values()):
        return []

    ts = _bar_date(bar)
    pctb = _get(ind, "bb_pctb")
    k_full = _get(ind, "k_full")

    hits: list[SignalHit] = []
    for side in (Side.LONG, Side.SHORT):
        types = [t for t in _SPECIFICITY[side] if t in fired[side]]
        if not types:
            continue
        band_types = (SignalType.BB_LOWER_TOUCH, SignalType.BB_UPPER_TOUCH)
        touched_band = any(t in band_types for t in types)
        level_field = "bb_lower" if side is Side.LONG else "bb_upper"
        hits.append(
            SignalHit(
                ticker=ticker,
                ts=ts,
                signal_type=types[0],
                signal_types_all=tuple(types),
                signal_strength=len(types),
                side=side,
                # A stochastic-only hit touched no band, so it has no touch
                # level. Null, never a substituted price (DESIGN §3.11).
                touch_level=_get(ind, level_field) if touched_band else float("nan"),
                pctb=pctb,
                k_full=k_full,
            )
        )
    return hits


def breach_live(price: float, bands: Bands, sp: SignalParams) -> list[SignalType]:
    """Live path. Current quote plus bands fixed at the prior close.

    Returns **every** concurrent type rather than only the most specific one,
    because the poller walks the session tick by tick and the job layer
    accumulates the day's set before ranking it. Ranking here would discard
    information the parity test needs (TESTS.md §3.2).

    The quote is tested against both bands: a single price can only breach
    one of them, and `_breach` decides which.
    """
    fired = _types_fired(price, price, bands, sp)
    out: list[SignalType] = []
    for side in (Side.LONG, Side.SHORT):
        out.extend(t for t in _SPECIFICITY[side] if t in fired[side])
    return out
