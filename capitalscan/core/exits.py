"""Exit resolution. Pure functions, no IO (DESIGN §3.1).

The most error-prone module in the system, which is why it is separated from
`returns.py` and carries its own test suite plus the property tests.

Evaluation order is pinned in DESIGN §5.5 and implemented once, in
`_exit_on_bar`, exactly in that order. Four details decide correctness:

1. **Gap checks run first.** An open past the level means the fill happened
   at the open, not at the level. Booking a 3% loss on a stop that gapped 8%
   through invents money, and mega-cap earnings gaps of that size are routine.
2. **Band levels come from bar i-1.** Using bar i's band means using bar i's
   close to decide an intraday exit on bar i — the same look-ahead trap as
   the entry side.
3. **`ambiguous` is set only on a stop-and-target collision.** Stop wins by
   rule (ADR 010); the flag makes the frequency measurable, and above 10%
   triggers hourly escalation.
4. **The stochastic exit is close-based and evaluated last,** because a
   close-triggered exit cannot preempt an intraday fill that already happened.

Every price comparison routes through `core.signals._breach` — no second
band comparison exists in the repo (CLAUDE.md invariant 2). A stop, a target,
and a band are all the same operation: a level plus a direction.

Both sides are resolved (ADR 058). The short is the sign flip: the target
sits below entry, the stop above, and the far band a long exits into
(`bb_upper`) becomes the near band a short exits into (`bb_lower`). The
reason code stays `UPPER_BAND` on both sides — it names the far-band exit,
not a direction.
"""

from __future__ import annotations

import pandas as pd

from capitalscan.core.config import ExitParams
from capitalscan.core.returns import mfe_mae
from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bound, ExitReason, ExitResult, Side


def stop_level(entry_price: float, side: Side, atr_at_entry: float, ep: ExitParams) -> float:
    """Stop price, or NaN when no stop applies.

    `stop_mode="none"` is a control arm (ADR 008), and a null ATR means the
    ATR stop cannot be placed — NaN either way, never a substituted level.
    """
    if ep.stop_mode == "none":
        return float("nan")
    if ep.stop_mode == "atr":
        if _isnan(atr_at_entry):
            return float("nan")
        distance = ep.stop_atr_k * float(atr_at_entry)
    elif ep.stop_mode == "fixed":
        distance = ep.stop_fixed_pct * float(entry_price)
    else:
        raise ValueError(f"unknown stop_mode: {ep.stop_mode!r}")
    return entry_price - distance if side is Side.LONG else entry_price + distance


def target_level(entry_price: float, side: Side, ep: ExitParams) -> float:
    """Profit target price."""
    move = ep.target_pct * float(entry_price)
    return entry_price + move if side is Side.LONG else entry_price - move


def min_stop_distance(entry_price: float, atr_at_entry: float, ep: ExitParams) -> float:
    """Stop distance as a fraction of entry, 0.0 when no stop applies.

    Used by the property tests to assert that a STOP exit landed at or beyond
    the stop level.
    """
    stop = stop_level(entry_price, Side.LONG, atr_at_entry, ep)
    if _isnan(stop):
        return 0.0
    return (float(entry_price) - stop) / float(entry_price)


def _hit(price: float, level: float, side: Side, adverse: bool) -> bool:
    """Whether `price` reached `level` in the direction that matters.

    `adverse=True` means the level sits against the position (a stop for a
    long, below entry); `adverse=False` means it sits in favor (a target or
    the far band). Both reduce to `_breach` with a bound chosen by side.
    """
    against = (side is Side.LONG) == adverse
    return _breach(price, level, Bound.LOWER if against else Bound.UPPER)


def _favorable_exit(
    bar: pd.Series, level: float, side: Side, reason: ExitReason
) -> tuple[float, ExitReason] | None:
    """A target or band exit on this bar, gap-adjusted, or None.

    The gap case fills at the open: a bar opening past the level never traded
    at the level, so filling there would invent a better price than existed.
    """
    if _isnan(level):
        return None
    open_ = float(bar["open"])
    if _hit(open_, level, side, adverse=False):
        return open_, reason
    extreme = float(bar["high"]) if side is Side.LONG else float(bar["low"])
    if _hit(extreme, level, side, adverse=False):
        return float(level), reason
    return None


def _exit_on_bar(
    bar: pd.Series,
    prior_ind: pd.Series | None,
    own_ind: pd.Series,
    side: Side,
    stop: float,
    target: float,
    ep: ExitParams,
) -> tuple[float, ExitReason, bool] | None:
    """Resolve one forward bar, in the pinned DESIGN §5.5 order.

    Returns `(exit_price, reason, ambiguous)` or None if nothing fired.
    `prior_ind` is bar i-1's indicator row and supplies the band levels;
    `own_ind` is bar i's and supplies %K, which is close-based.
    """
    open_ = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])

    # 1. GAP CHECK — stop first, then target.
    if not _isnan(stop) and _hit(open_, stop, side, adverse=True):
        return open_, ExitReason.STOP, False
    if _hit(open_, target, side, adverse=False):
        return open_, ExitReason.TARGET, False

    # 2. INTRADAY, priority order: stop, target, mid band, far band.
    adverse_extreme = low if side is Side.LONG else high
    if not _isnan(stop) and _hit(adverse_extreme, stop, side, adverse=True):
        # Stop wins by rule (ADR 010); the flag records that the daily bar
        # carried no ordering information to decide it properly.
        favorable_extreme = high if side is Side.LONG else low
        ambiguous = _hit(favorable_extreme, target, side, adverse=False)
        return float(stop), ExitReason.STOP, ambiguous

    hit = _favorable_exit(bar, target, side, ExitReason.TARGET)
    if hit is not None:
        return hit[0], hit[1], False

    if prior_ind is not None:
        if ep.exit_on_mid_band:
            hit = _favorable_exit(bar, _level(prior_ind, "bb_mid"), side, ExitReason.MID_BAND)
            if hit is not None:
                return hit[0], hit[1], False
        if ep.exit_on_upper_band:
            # The far band: bb_upper for a long, bb_lower for a short.
            field = "bb_upper" if side is Side.LONG else "bb_lower"
            hit = _favorable_exit(bar, _level(prior_ind, field), side, ExitReason.UPPER_BAND)
            if hit is not None:
                return hit[0], hit[1], False

    # 3. CLOSE-BASED — last, so it cannot preempt an intraday fill.
    if ep.exit_on_stoch_80:
        k_full = _level(own_ind, "k_full")
        if not _isnan(k_full):
            extreme = k_full >= 80.0 if side is Side.LONG else k_full <= 20.0
            if extreme:
                return float(bar["close"]), ExitReason.STOCH_80, False

    return None


def _level(row: pd.Series | None, field: str) -> float:
    if row is None:
        return float("nan")
    try:
        value = row[field]
    except KeyError:
        return float("nan")
    return float("nan") if value is None else float(value)


def resolve_exit(
    entry_price: float,
    entry_idx: int,
    side: Side,
    fwd_bars: pd.DataFrame,
    fwd_ind: pd.DataFrame,
    atr_at_entry: float,
    ep: ExitParams,
    ind_at_entry: pd.Series | None = None,
) -> ExitResult:
    """Resolve a position's exit over its forward window.

    `fwd_bars` holds rows `entry_idx+1 .. entry_idx+max_hold`, and `fwd_ind`
    shares its index. Band levels for bar i come from bar i-1, which this
    function derives by shifting `fwd_ind` — so the **first** forward bar has
    no i-1 row inside the frame. `ind_at_entry` supplies it: the entry bar's
    indicator row. Omitting it skips band exits on that first bar rather than
    falling back to bar i's own level, which would re-introduce the look-ahead
    the i-1 rule exists to prevent.

    `exit_idx` is positional into `fwd_bars`, 0-based. `holding_days` counts
    bars held, 1-based, so it equals `exit_idx + 1`.
    """
    if len(fwd_bars) == 0:
        raise ValueError("resolve_exit requires at least one forward bar")

    window = fwd_bars.iloc[: ep.max_hold_days]
    ind_window = fwd_ind.iloc[: ep.max_hold_days]

    stop = stop_level(entry_price, side, atr_at_entry, ep)
    target = target_level(entry_price, side, ep)

    exit_idx = len(window) - 1
    exit_price = float(window.iloc[-1]["close"])
    reason = ExitReason.TIMEOUT
    ambiguous = False

    for i in range(len(window)):
        bar = window.iloc[i]
        prior_ind = ind_window.iloc[i - 1] if i > 0 else ind_at_entry
        outcome = _exit_on_bar(bar, prior_ind, ind_window.iloc[i], side, stop, target, ep)
        if outcome is not None:
            exit_price, reason, ambiguous = outcome
            exit_idx = i
            break

    # 4. TERMINAL — the loop falling through leaves the final bar's close.

    # Path metrics run over [t+1, exit_idx] (DESIGN §5.6). Reachability spans
    # the full window regardless of exit timing and belongs to the backtest
    # engine, not here.
    mfe, mae, time_to_mfe = mfe_mae(entry_price, side, window.iloc[: exit_idx + 1])

    return ExitResult(
        exit_idx=exit_idx,
        exit_date=window.index[exit_idx].date(),
        exit_price=exit_price,
        reason=reason,
        holding_days=exit_idx + 1,
        mfe=mfe,
        mae=mae,
        time_to_mfe=time_to_mfe,
        ambiguous=ambiguous,
    )
