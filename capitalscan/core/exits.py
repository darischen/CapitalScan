"""Exit resolution. Pure functions, no IO (DESIGN §3.1).

The most error-prone module in the system, which is why it is separated from
`returns.py` and carries its own test suite plus the property tests.

Evaluation order is pinned in DESIGN §5.5 and implemented once, in
`_exit_on_values`, exactly in that order. Four details decide correctness:

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

**`resolve_exit` reads its window as columns, once** (2026-11-18). It built
three pandas Series per forward bar until then, plus three frame slices per
call, and that row construction was most of its cost. The bar-by-bar loop,
the DESIGN §5.5 order and the first-exit `break` are unchanged, and
`tests/property/test_resolve_exit_equivalence.py` holds the row-based
version frozen and asserts identical output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from capitalscan.core.config import ExitParams
from capitalscan.core.returns import mfe_mae, mfe_mae_arrays
from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bound, ExitReason, ExitResult, Side

#: A row read by column name: a pandas Series or a plain mapping.
_Row = pd.Series | Mapping[str, Any]


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
    open_: float, high: float, low: float, level: float, side: Side, reason: ExitReason
) -> tuple[float, ExitReason] | None:
    """A target or band exit on this bar, gap-adjusted, or None.

    The gap case fills at the open: a bar opening past the level never traded
    at the level, so filling there would invent a better price than existed.
    """
    if _isnan(level):
        return None
    if _hit(open_, level, side, adverse=False):
        return open_, reason
    extreme = high if side is Side.LONG else low
    if _hit(extreme, level, side, adverse=False):
        return float(level), reason
    return None


def _exit_on_values(
    open_: float,
    high: float,
    low: float,
    close: Any,
    mid: Any,
    far: Any,
    k: Any,
    side: Side,
    stop: float,
    target: float,
    ep: ExitParams,
) -> tuple[float, ExitReason, bool] | None:
    """Resolve one forward bar, in the pinned DESIGN §5.5 order.

    Returns `(exit_price, reason, ambiguous)` or None if nothing fired.
    `mid` and `far` are bar i-1's band cells (`bb_mid`, and `bb_upper` for
    a long or `bb_lower` for a short); `k` is bar i's own %K cell from
    `ep.exit_stoch_source`, which is close-based. Those three and `close`
    arrive as raw cells and are converted only on the branch that reads
    them, as the row-based version did. A cell that is `None`, or absent
    from its frame, is NaN, and a NaN level never fires.
    """
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

    hit = _favorable_exit(open_, high, low, target, side, ExitReason.TARGET)
    if hit is not None:
        return hit[0], hit[1], False

    if ep.exit_on_mid_band:
        hit = _favorable_exit(open_, high, low, _as_level(mid), side, ExitReason.MID_BAND)
        if hit is not None:
            return hit[0], hit[1], False
    if ep.exit_on_upper_band:
        # The far band: bb_upper for a long, bb_lower for a short.
        hit = _favorable_exit(open_, high, low, _as_level(far), side, ExitReason.UPPER_BAND)
        if hit is not None:
            return hit[0], hit[1], False

    # 3. CLOSE-BASED — last, so it cannot preempt an intraday fill.
    if ep.exit_on_stoch_80:
        # `ep.exit_stoch_source`, not a hardcoded "k_full": the column is
        # exit policy the same way the two thresholds below are. It stayed
        # invisible while `SignalParams.stoch_source` was also `k_full`, and
        # became a silent entry/exit disagreement the moment the entry
        # flipped to `k_fast`. Defaults to `k_full`, so naming it changed no
        # measured exit.
        k_level = _as_level(k)
        if not _isnan(k_level):
            # ADR 092: both levels are exit policy, read from ExitParams,
            # never literals and never derived from each other. The short is
            # not a mirror of the long (ADR 016), so the two sweep
            # independently.
            if side is Side.LONG:
                extreme = k_level >= ep.exit_stoch_threshold
            else:
                extreme = k_level <= ep.exit_stoch_threshold_short
            if extreme:
                return float(close), ExitReason.STOCH_80, False

    return None


def _far_band(side: Side) -> str:
    """The band a position exits into: bb_upper for a long, bb_lower for a short."""
    return "bb_upper" if side is Side.LONG else "bb_lower"


def _exit_on_bar(
    bar: _Row,
    prior_ind: _Row | None,
    own_ind: _Row,
    side: Side,
    stop: float,
    target: float,
    ep: ExitParams,
) -> tuple[float, ExitReason, bool] | None:
    """`_exit_on_values` for one bar given as rows (a Series or a dict).

    `resolve_exit` reads its window as columns and calls `_exit_on_values`
    directly. This row-shaped entry point stays for the tests that drive a
    single bar. `prior_ind` is bar i-1's indicator row and supplies the band
    levels; `own_ind` is bar i's and supplies %K.
    """
    return _exit_on_values(
        float(bar["open"]),
        float(bar["high"]),
        float(bar["low"]),
        bar["close"],
        _cell(prior_ind, "bb_mid"),
        _cell(prior_ind, _far_band(side)),
        _cell(own_ind, ep.exit_stoch_source),
        side,
        stop,
        target,
        ep,
    )


def _cell(row: _Row | None, field: str) -> Any:
    """`row[field]` unconverted, or None when there is no row or no field."""
    if row is None:
        return None
    try:
        return row[field]
    except KeyError:
        return None


def _as_level(value: Any) -> float:
    return float("nan") if value is None else float(value)


def _level(row: _Row | None, field: str) -> float:
    return _as_level(_cell(row, field))


def _column(frame: pd.DataFrame, field: str, n: int) -> list[Any]:
    """The first `n` cells of `frame[field]` as Python objects.

    A missing column reads as `None` in every cell, which `_as_level` turns
    into NaN: the same answer a row lookup gave through its `KeyError`.
    """
    if field not in frame.columns:
        return [None] * n
    cells: list[Any] = frame[field].to_numpy()[:n].tolist()
    return cells


def resolve_exit(
    entry_price: float,
    entry_idx: int,
    side: Side,
    fwd_bars: pd.DataFrame,
    fwd_ind: pd.DataFrame,
    atr_at_entry: float,
    ep: ExitParams,
    ind_at_entry: _Row | None = None,
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

    # Each column is read once. A list slice `[:horizon]` takes exactly the
    # rows `.iloc[:horizon]` took, for any int, so `n` and `n_ind` are the
    # lengths the old `window` and `ind_window` frames had.
    horizon = ep.max_hold_days
    n = len(range(len(fwd_bars))[:horizon])
    n_ind = len(range(len(fwd_ind))[:horizon])
    high_col = fwd_bars["high"].to_numpy()[:horizon]
    low_col = fwd_bars["low"].to_numpy()[:horizon]
    highs: list[Any] = high_col.tolist()
    lows: list[Any] = low_col.tolist()
    opens: list[Any] = fwd_bars["open"].to_numpy()[:horizon].tolist()
    closes: list[Any] = fwd_bars["close"].to_numpy()[:horizon].tolist()
    # Only the indicator columns `ep` turns on. Cell i-1 of `mids` and
    # `fars` is bar i's band level; cell i of `ks` is bar i's own %K.
    unused: list[Any] = [None] * n_ind
    far_field = _far_band(side)
    mids = _column(fwd_ind, "bb_mid", n_ind) if ep.exit_on_mid_band else unused
    fars = _column(fwd_ind, far_field, n_ind) if ep.exit_on_upper_band else unused
    ks = _column(fwd_ind, ep.exit_stoch_source, n_ind) if ep.exit_on_stoch_80 else unused

    stop = stop_level(entry_price, side, atr_at_entry, ep)
    target = target_level(entry_price, side, ep)

    exit_idx = n - 1
    exit_price = float(closes[-1])
    # A window shorter than the horizon ran out of data, not time. Filled
    # the same way; labelled apart so a count of timeouts means five bars.
    reason = ExitReason.TIMEOUT if n >= horizon else ExitReason.UNFINISHED
    ambiguous = False

    for i in range(n):
        if i >= n_ind:
            # The row version raised here, from `ind_window.iloc[i]`, and only
            # once the loop reached a bar with no indicator row.
            raise IndexError("single positional indexer is out-of-bounds")
        if i > 0:
            mid, far = mids[i - 1], fars[i - 1]
        else:
            mid = _cell(ind_at_entry, "bb_mid")
            far = _cell(ind_at_entry, far_field)
        outcome = _exit_on_values(
            float(opens[i]),
            float(highs[i]),
            float(lows[i]),
            closes[i],
            mid,
            far,
            ks[i],
            side,
            stop,
            target,
            ep,
        )
        if outcome is not None:
            exit_price, reason, ambiguous = outcome
            exit_idx = i
            break

    # 4. TERMINAL — the loop falling through leaves the final bar's close,
    # as TIMEOUT at the horizon or UNFINISHED before it.

    # Path metrics run over [t+1, exit_idx] (DESIGN §5.6). Reachability spans
    # the full window regardless of exit timing and belongs to the backtest
    # engine, not here. The array path is exact only for float64 without NaN
    # (`mfe_mae_arrays`); anything else takes the frame path it always took.
    held_high = high_col[: exit_idx + 1]
    held_low = low_col[: exit_idx + 1]
    if (
        held_high.dtype == np.float64
        and held_low.dtype == np.float64
        and not np.isnan(held_high).any()
        and not np.isnan(held_low).any()
    ):
        mfe, mae, time_to_mfe = mfe_mae_arrays(entry_price, side, held_high, held_low)
    else:
        mfe, mae, time_to_mfe = mfe_mae(entry_price, side, fwd_bars.iloc[: exit_idx + 1])

    return ExitResult(
        exit_idx=exit_idx,
        exit_date=fwd_bars.index[exit_idx].date(),
        exit_price=exit_price,
        reason=reason,
        holding_days=exit_idx + 1,
        mfe=mfe,
        mae=mae,
        time_to_mfe=time_to_mfe,
        ambiguous=ambiguous,
    )
