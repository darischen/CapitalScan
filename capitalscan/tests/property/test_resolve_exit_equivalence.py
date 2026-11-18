"""`resolve_exit` must return exactly what it returned before 2026-11-18.

Written before the optimisation it protects (BACKLOG, "`core.exits.
resolve_exit` builds three pandas Series per forward bar"). The live
function stopped building a pandas Series per bar and reads its forward
window once into plain lists; the DESIGN §5.5 order, the early `break` and
every comparison are unchanged. This module holds a **frozen copy** of the
implementation as it stood at `65f3b8d` and asserts the two agree on every
returned field, exactly: same values, same Python types, NaN only where the
other has NaN, and the same exception type wherever the old one raised.

**Do not edit `reference_resolve_exit` to make a test pass.** It is the
specification of "no change in output". A future change to exit logic that
is meant to change output retires this module in the same commit, with its
reason, rather than quietly updating the copy.

The generators aim at the places a rewrite of the row access would break:

- prices placed **exactly on** the stop and the target, and 0.00004 either
  side, where `_breach`'s 4-decimal rounding decides the tie;
- opens gapped past the stop or target, on both sides;
- NaN and `None` in indicator cells, missing indicator columns, an absent
  `ind_at_entry`, and `ind_at_entry` as a plain dict;
- frames shaped like `research.backtest`'s (a `ts` column kept beside the
  index, a `ticker` string), which turns every row into an object Series;
- windows longer and shorter than `max_hold_days` (TIMEOUT and UNFINISHED);
- an indicator frame shorter than the bar frame, which raised `IndexError`
  only once the loop reached the missing row.

Bars never carry NaN OHLC: invariant 4 drops such rows to `bar_rejects`
before anything reaches `core/`. NaN in bars is still exercised by one
explicit test, so a difference there is visible rather than assumed away.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from capitalscan.core.config import ExitParams
from capitalscan.core.exits import resolve_exit
from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bound, ExitReason, ExitResult, Side

SETTINGS = settings(suppress_health_check=[HealthCheck.too_slow], deadline=None)

# ---------------------------------------------------------------------------
# Frozen reference: core/exits.py at 65f3b8d, plus `mfe_mae` and the
# excursion formula from core/returns.py, which this change also touched.
# Same statements, with `_ref` prefixes, comments and docstrings dropped, and
# the formatter's line wrapping. `_breach` and `_isnan` are imported, not
# copied: they are outside the change and pinned by their own tests.
# ---------------------------------------------------------------------------


def _ref_mfe_mae(entry_price: float, side: Side, fwd_bars: pd.DataFrame):
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


def _ref_stop_level(entry_price: float, side: Side, atr_at_entry: float, ep: ExitParams) -> float:
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


def _ref_target_level(entry_price: float, side: Side, ep: ExitParams) -> float:
    move = ep.target_pct * float(entry_price)
    return entry_price + move if side is Side.LONG else entry_price - move


def _ref_hit(price: float, level: float, side: Side, adverse: bool) -> bool:
    against = (side is Side.LONG) == adverse
    return _breach(price, level, Bound.LOWER if against else Bound.UPPER)


def _ref_favorable_exit(bar, level, side, reason):
    if _isnan(level):
        return None
    open_ = float(bar["open"])
    if _ref_hit(open_, level, side, adverse=False):
        return open_, reason
    extreme = float(bar["high"]) if side is Side.LONG else float(bar["low"])
    if _ref_hit(extreme, level, side, adverse=False):
        return float(level), reason
    return None


def _ref_level(row, field: str) -> float:
    if row is None:
        return float("nan")
    try:
        value = row[field]
    except KeyError:
        return float("nan")
    return float("nan") if value is None else float(value)


def _ref_exit_on_bar(bar, prior_ind, own_ind, side, stop, target, ep):
    open_ = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])

    if not _isnan(stop) and _ref_hit(open_, stop, side, adverse=True):
        return open_, ExitReason.STOP, False
    if _ref_hit(open_, target, side, adverse=False):
        return open_, ExitReason.TARGET, False

    adverse_extreme = low if side is Side.LONG else high
    if not _isnan(stop) and _ref_hit(adverse_extreme, stop, side, adverse=True):
        favorable_extreme = high if side is Side.LONG else low
        ambiguous = _ref_hit(favorable_extreme, target, side, adverse=False)
        return float(stop), ExitReason.STOP, ambiguous

    hit = _ref_favorable_exit(bar, target, side, ExitReason.TARGET)
    if hit is not None:
        return hit[0], hit[1], False

    if prior_ind is not None:
        if ep.exit_on_mid_band:
            hit = _ref_favorable_exit(
                bar, _ref_level(prior_ind, "bb_mid"), side, ExitReason.MID_BAND
            )
            if hit is not None:
                return hit[0], hit[1], False
        if ep.exit_on_upper_band:
            field = "bb_upper" if side is Side.LONG else "bb_lower"
            hit = _ref_favorable_exit(
                bar, _ref_level(prior_ind, field), side, ExitReason.UPPER_BAND
            )
            if hit is not None:
                return hit[0], hit[1], False

    if ep.exit_on_stoch_80:
        k = _ref_level(own_ind, ep.exit_stoch_source)
        if not _isnan(k):
            if side is Side.LONG:
                extreme = k >= ep.exit_stoch_threshold
            else:
                extreme = k <= ep.exit_stoch_threshold_short
            if extreme:
                return float(bar["close"]), ExitReason.STOCH_80, False

    return None


def reference_resolve_exit(
    entry_price: float,
    entry_idx: int,
    side: Side,
    fwd_bars: pd.DataFrame,
    fwd_ind: pd.DataFrame,
    atr_at_entry: float,
    ep: ExitParams,
    ind_at_entry=None,
) -> ExitResult:
    """`core.exits.resolve_exit` as of 65f3b8d. Frozen; see module docstring."""
    if len(fwd_bars) == 0:
        raise ValueError("resolve_exit requires at least one forward bar")

    window = fwd_bars.iloc[: ep.max_hold_days]
    ind_window = fwd_ind.iloc[: ep.max_hold_days]

    stop = _ref_stop_level(entry_price, side, atr_at_entry, ep)
    target = _ref_target_level(entry_price, side, ep)

    exit_idx = len(window) - 1
    exit_price = float(window.iloc[-1]["close"])
    reason = ExitReason.TIMEOUT if len(window) >= ep.max_hold_days else ExitReason.UNFINISHED
    ambiguous = False

    for i in range(len(window)):
        bar = window.iloc[i]
        prior_ind = ind_window.iloc[i - 1] if i > 0 else ind_at_entry
        outcome = _ref_exit_on_bar(bar, prior_ind, ind_window.iloc[i], side, stop, target, ep)
        if outcome is not None:
            exit_price, reason, ambiguous = outcome
            exit_idx = i
            break

    mfe, mae, time_to_mfe = _ref_mfe_mae(entry_price, side, window.iloc[: exit_idx + 1])

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


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_FIELDS = (
    "exit_idx",
    "exit_date",
    "exit_price",
    "reason",
    "holding_days",
    "mfe",
    "mae",
    "time_to_mfe",
    "ambiguous",
)


def _same(a: object, b: object) -> bool:
    """Exact: same type, same value, NaN matching NaN only."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        # `==` alone treats 0.0 and -0.0 as equal; the bits do not.
        return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)
    return a == b


def _run(fn, kwargs):
    try:
        return "ok", fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the result
        return "raised", type(exc)


def assert_equivalent(kwargs: dict) -> ExitResult | None:
    old_kind, old = _run(reference_resolve_exit, kwargs)
    new_kind, new = _run(resolve_exit, kwargs)
    assert old_kind == new_kind, (old, new)
    if old_kind == "raised":
        assert old is new, (old, new)
        return None
    for name in _FIELDS:
        a, b = getattr(old, name), getattr(new, name)
        assert _same(a, b), f"{name}: reference {a!r} vs live {b!r}"
    assert isinstance(new, ExitResult)
    assert isinstance(new.exit_date, date)
    return new


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

ENTRY_PRICES = st.sampled_from([100.0, 37.25, 412.1234, 9.87])
TICK = 0.00004  # below the 4-decimal rounding `_breach` applies


def _finite(lo: float, hi: float) -> st.SearchStrategy[float]:
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


@st.composite
def exit_params(draw) -> ExitParams:
    return ExitParams(
        max_hold_days=draw(st.integers(min_value=1, max_value=6)),
        target_pct=draw(st.sampled_from([0.03, 0.05, 0.07]) | _finite(0.005, 0.12)),
        stop_mode=draw(st.sampled_from(["atr", "fixed", "none"])),
        stop_atr_k=draw(st.sampled_from([1.0, 1.5, 2.0, 2.5])),
        stop_fixed_pct=draw(st.sampled_from([0.02, 0.03]) | _finite(0.005, 0.10)),
        exit_on_upper_band=draw(st.booleans()),
        exit_on_stoch_80=draw(st.booleans()),
        exit_on_mid_band=draw(st.booleans()),
        exit_stoch_threshold=draw(st.sampled_from([70.0, 80.0, 90.0])),
        exit_stoch_threshold_short=draw(st.sampled_from([10.0, 20.0, 30.0])),
        exit_stoch_source=draw(st.sampled_from(["k_full", "k_fast", "k_missing"])),
    )


def _price(draw, anchors: list[float], lo: float, hi: float) -> float:
    """A price that is often exactly on, or a sub-tick off, a decision level."""
    finite_anchors = [a for a in anchors if not math.isnan(a) and a > 0]
    if finite_anchors and draw(st.booleans()):
        base: float = draw(st.sampled_from(finite_anchors))
        nudge: float = draw(st.sampled_from([0.0, 0.0, TICK, -TICK, 0.0001, -0.0001]))
        return base + nudge
    price: float = draw(_finite(lo, hi))
    return price


@st.composite
def scenarios(draw):
    ep = draw(exit_params())
    side = draw(st.sampled_from([Side.LONG, Side.SHORT]))
    entry = draw(ENTRY_PRICES)
    atr = draw(_finite(0.05, 0.06 * entry) | st.just(float("nan")))

    stop = _ref_stop_level(entry, side, atr, ep)
    target = _ref_target_level(entry, side, ep)
    lo_px, hi_px = entry * 0.85, entry * 1.15
    anchors = [stop, target, entry]

    n = draw(st.integers(min_value=1, max_value=ep.max_hold_days + 2))
    rows = []
    for _ in range(n):
        a = _price(draw, anchors, lo_px, hi_px)
        b = _price(draw, anchors, lo_px, hi_px)
        low, high = min(a, b), max(a, b)
        gap = draw(st.sampled_from(["inside", "inside", "above", "below"]))
        if gap == "above":
            open_ = high  # gapped up to the top of the bar
        elif gap == "below":
            open_ = low
        else:
            open_ = draw(_finite(low, high)) if high > low else low
        close = draw(_finite(low, high)) if high > low else high
        rows.append((open_, high, low, close))

    ts = pd.date_range("2024-03-01", periods=n, freq="B")
    bars = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=ts)
    production_shape = draw(st.booleans())
    if production_shape:
        bars.insert(0, "ts", ts)
        bars.insert(1, "ticker", "TEST")

    ind_rows = draw(st.integers(min_value=max(0, n - 2), max_value=n + 1))
    if draw(st.integers(min_value=0, max_value=9)) > 0:
        ind_rows = n  # aligned, as `resolve_exit_for_entry` always slices it

    def _cell(lo: float, hi: float):
        return draw(
            st.one_of(
                _finite(lo, hi),
                st.sampled_from(anchors).map(lambda v: v if not math.isnan(v) else entry),
                st.just(float("nan")),
            )
        )

    ind_cols: dict[str, list] = {
        "bb_mid": [_cell(lo_px, hi_px) for _ in range(ind_rows)],
        "bb_upper": [_cell(entry, hi_px * 1.05) for _ in range(ind_rows)],
        "bb_lower": [_cell(lo_px * 0.95, entry) for _ in range(ind_rows)],
        "k_full": [draw(_finite(0.0, 100.0) | st.just(float("nan"))) for _ in range(ind_rows)],
        "k_fast": [draw(_finite(0.0, 100.0) | st.just(float("nan"))) for _ in range(ind_rows)],
    }
    drop = draw(st.sets(st.sampled_from(sorted(ind_cols)), max_size=2))
    for col in drop:
        del ind_cols[col]
    ind_index = pd.date_range("2024-03-01", periods=ind_rows, freq="B")
    ind = pd.DataFrame(ind_cols, index=ind_index)
    if production_shape:
        ind.insert(0, "ticker", "TEST")
        ind.insert(1, "config_hash", "f183b0f5209a4677")
        # Object column with genuine `None`, the way a nullable DB column
        # arrives when the frame is not all-float.
        if "bb_mid" in ind.columns and draw(st.booleans()):
            ind["bb_mid"] = [None if draw(st.booleans()) else v for v in ind["bb_mid"]]
            ind["bb_mid"] = ind["bb_mid"].astype(object)

    entry_kind = draw(st.sampled_from(["series", "dict", "none"]))
    entry_row = {
        "bb_mid": _cell(lo_px, hi_px),
        "bb_upper": _cell(entry, hi_px * 1.05),
        "bb_lower": _cell(lo_px * 0.95, entry),
        "k_full": 50.0,
    }
    if draw(st.booleans()):
        entry_row["bb_upper"] = None
    ind_at_entry: object
    if entry_kind == "series":
        ind_at_entry = pd.Series(entry_row, dtype=object)
    elif entry_kind == "dict":
        ind_at_entry = entry_row
    else:
        ind_at_entry = None

    return dict(
        entry_price=entry,
        entry_idx=draw(st.integers(min_value=0, max_value=5000)),
        side=side,
        fwd_bars=bars,
        fwd_ind=ind,
        atr_at_entry=atr,
        ep=ep,
        ind_at_entry=ind_at_entry,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@given(scenarios())
@SETTINGS
def test_live_matches_the_frozen_reference(kwargs):
    assert_equivalent(kwargs)


def _fixed(opens, highs, lows, closes, *, ep, side=Side.LONG, ind=None, at_entry=None):
    n = len(opens)
    idx = pd.date_range("2025-01-06", periods=n, freq="B")
    bars = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)
    if ind is None:
        ind = pd.DataFrame(
            {"bb_mid": [999.0] * n, "bb_upper": [999.0] * n, "bb_lower": [1.0] * n},
            index=idx,
        )
        ind["k_full"] = 50.0
    return dict(
        entry_price=100.0,
        entry_idx=0,
        side=side,
        fwd_bars=bars,
        fwd_ind=ind,
        atr_at_entry=2.0,
        ep=ep,
        ind_at_entry=at_entry,
    )


EP = ExitParams(target_pct=0.05, stop_mode="atr", stop_atr_k=2.0, max_hold_days=5)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        # long stop 96.0, target 105.0
        (_fixed([95.0], [95.5], [94.0], [95.0], ep=EP), ExitReason.STOP),  # gap stop
        (_fixed([106.0], [106.5], [105.5], [106.0], ep=EP), ExitReason.TARGET),  # gap target
        (_fixed([100.0], [100.5], [96.0], [99.0], ep=EP), ExitReason.STOP),  # stop tie
        (_fixed([100.0], [105.0], [99.5], [104.0], ep=EP), ExitReason.TARGET),  # target tie
        (_fixed([100.0], [105.5], [95.5], [99.0], ep=EP), ExitReason.STOP),  # ambiguous
        (_fixed([100.0], [105.0 - TICK], [99.5], [104.0], ep=EP), ExitReason.TARGET),
        (_fixed([100.0], [104.9998], [99.5], [104.0], ep=EP), ExitReason.UNFINISHED),
        (_fixed([100.0] * 5, [100.5] * 5, [99.5] * 5, [100.0] * 5, ep=EP), ExitReason.TIMEOUT),
        (
            _fixed(
                [100.0] * 2,
                [101.5] * 2,
                [99.5] * 2,
                [101.0] * 2,
                ep=EP,
                at_entry={"bb_upper": 101.0},
            ),
            ExitReason.UPPER_BAND,
        ),
        (
            _fixed(
                [100.0] * 2,
                [101.5] * 2,
                [99.5] * 2,
                [101.0] * 2,
                ep=ExitParams(exit_on_mid_band=True),
                at_entry=pd.Series({"bb_mid": 101.0, "bb_upper": 999.0}),
            ),
            ExitReason.MID_BAND,
        ),
        (
            _fixed(
                [100.0] * 2,
                [100.5] * 2,
                [99.5] * 2,
                [100.2] * 2,
                ep=EP,
                ind=pd.DataFrame(
                    {"bb_upper": [999.0, 999.0], "k_full": [85.0, 50.0]},
                    index=pd.date_range("2025-01-06", periods=2, freq="B"),
                ),
            ),
            ExitReason.STOCH_80,
        ),
        # short: stop 104.0, target 95.0
        (_fixed([105.0], [105.5], [104.5], [105.0], ep=EP, side=Side.SHORT), ExitReason.STOP),
        (_fixed([94.0], [94.5], [93.5], [94.0], ep=EP, side=Side.SHORT), ExitReason.TARGET),
        (_fixed([100.0], [104.0], [95.0], [99.0], ep=EP, side=Side.SHORT), ExitReason.STOP),
        (
            _fixed(
                [100.0] * 2,
                [100.5] * 2,
                [98.5] * 2,
                [99.0] * 2,
                ep=EP,
                side=Side.SHORT,
                at_entry={"bb_lower": 99.0},
            ),
            ExitReason.UPPER_BAND,
        ),
    ],
)
def test_every_reason_and_tie_matches(case, expected):
    """Pins that each DESIGN §5.5 branch is reached, then that both agree on it."""
    result = assert_equivalent(case)
    assert result is not None
    assert result.reason is expected


def test_ambiguous_flag_matches_on_a_stop_and_target_collision():
    result = assert_equivalent(_fixed([100.0], [105.5], [95.5], [99.0], ep=EP))
    assert result is not None
    assert result.ambiguous is True


def test_short_indicator_frame_raises_the_same_way():
    """Old code raised `IndexError` only when the loop reached the missing row."""
    case = _fixed([100.0] * 3, [100.5] * 3, [99.5] * 3, [100.0] * 3, ep=EP)
    case["fwd_ind"] = case["fwd_ind"].iloc[:1]
    assert assert_equivalent(case) is None
    # ...and resolved normally when the exit fired before that row.
    case = _fixed([95.0, 100.0, 100.0], [95.5] * 3, [94.0] * 3, [95.0] * 3, ep=EP)
    case["fwd_ind"] = case["fwd_ind"].iloc[:1]
    assert assert_equivalent(case) is not None


def test_empty_window_raises_the_same_way():
    case = _fixed([100.0], [100.5], [99.5], [100.0], ep=EP)
    case["fwd_bars"] = case["fwd_bars"].iloc[:0]
    assert assert_equivalent(case) is None


NAN = float("nan")


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
@pytest.mark.parametrize(
    "ohlc",
    [
        # NaN in high only, in low only, in both, and on every bar: `Series.max`
        # skips NaN and `ndarray.max` does not, so each must take the frame path.
        ([100.0] * 3, [100.5, NAN, 100.5], [99.5] * 3, [100.0] * 3),
        ([100.0] * 3, [100.5] * 3, [99.5, 99.0, NAN], [100.0] * 3),
        ([100.0, NAN, 100.0], [100.5, NAN, 100.5], [99.5, 99.0, NAN], [100.0, 100.0, NAN]),
        ([NAN] * 2, [NAN] * 2, [NAN] * 2, [100.0] * 2),
    ],
)
def test_nan_ohlc_matches_even_though_invariant_4_keeps_it_out(ohlc, side):
    assert assert_equivalent(_fixed(*ohlc, ep=EP, side=side)) is not None


def test_integer_and_mixed_dtype_columns_match():
    case = _fixed([100, 101], [102, 106], [99, 100], [101, 105], ep=EP)
    case["fwd_bars"] = case["fwd_bars"].astype({"open": np.int64, "close": object})
    assert assert_equivalent(case) is not None
