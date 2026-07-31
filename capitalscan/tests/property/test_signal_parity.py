"""Signal path parity — ADR 006 enforcement (BUILD.md §3.7, TESTS.md §3.2).

`detect()` on a bar must produce the same signal set as `breach_live()`
walked across a simulated intraday path. This is the test that prevents the
live system from firing on events the backtest never measured, which is the
failure mode you discover months later through unexplained losses.

The simulated path walks open -> low -> high -> close, which is the ordering
assumption the backtest makes.

Comparison is over `signal_types_all` unioned across hits, not over
`signal_type` alone. `detect` collapses concurrent signals to the most
specific one per side (ADR 057) while `breach_live` returns every concurrent
type, because the poller accumulates the day's set in the job layer. Union
is the comparison that means "the same signals fired".
"""

from __future__ import annotations

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from capitalscan.core.config import SignalParams
from capitalscan.core.signals import breach_live, detect
from capitalscan.core.types import Bands, SignalType


def finite(min_value: float, max_value: float) -> st.SearchStrategy[float]:
    """Real prices and fractions only: no NaN, no infinity."""
    return st.floats(
        min_value=min_value, max_value=max_value, allow_nan=False, allow_infinity=False
    )


def simulate_intraday(bar: pd.Series) -> list[float]:
    """The path the backtest assumes: open, then low, then high, then close."""
    return [float(bar["open"]), float(bar["low"]), float(bar["high"]), float(bar["close"])]


def _bands_from(ind: pd.Series) -> Bands:
    return Bands(
        bb_lower=float(ind["bb_lower"]),
        bb_mid=float(ind["bb_mid"]),
        bb_upper=float(ind["bb_upper"]),
        k_full=float(ind["k_full"]),
        d_full=float(ind["d_full"]),
        k_fast=float(ind["k_fast"]),
        atr_14=float(ind["atr_14"]),
    )


@st.composite
def bar_and_bands(draw):
    """One valid bar plus a t-1 indicator row, both in overlapping ranges so
    breaches are common rather than vanishingly rare."""
    low = draw(finite(50.0, 150.0))
    high = low + draw(finite(0.0, 20.0))
    open_ = draw(finite(low, high))
    close = draw(finite(low, high))
    bar = pd.Series(
        {"open": open_, "high": high, "low": low, "close": close},
        name=pd.Timestamp("2026-07-29"),
    )

    mid = draw(finite(50.0, 150.0))
    half_width = draw(finite(0.5, 25.0))
    ind = pd.Series(
        {
            "bb_lower": mid - half_width,
            "bb_mid": mid,
            "bb_upper": mid + half_width,
            "bb_pctb": draw(finite(-0.5, 1.5)),
            "k_full": draw(finite(0.0, 100.0)),
            "d_full": draw(finite(0.0, 100.0)),
            "k_fast": draw(finite(0.0, 100.0)),
            "atr_14": draw(finite(0.1, 10.0)),
        }
    )
    return bar, ind


@st.composite
def signal_params(draw) -> SignalParams:
    return SignalParams(
        stoch_oversold=draw(st.sampled_from([15.0, 20.0, 25.0])),
        stoch_overbought=draw(st.sampled_from([75.0, 80.0, 85.0])),
        require_fast_agreement=draw(st.booleans()),
        fast_agreement_tol=draw(st.sampled_from([2.0, 5.0, 10.0])),
        price_tolerance=draw(st.sampled_from([0.0, 0.001])),
    )


def _backtest_types(bar, ind, sp) -> set[SignalType]:
    types: set[SignalType] = set()
    for hit in detect(bar, ind, sp):
        types.update(hit.signal_types_all)
    return types


def _live_types(bar, ind, sp) -> set[SignalType]:
    bands = _bands_from(ind)
    types: set[SignalType] = set()
    for price in simulate_intraday(bar):
        types.update(breach_live(price, bands, sp))
    return types


@given(bar_and_bands(), signal_params())
@settings(deadline=None)
def test_backtest_and_live_agree(fixture, sp):
    bar, ind = fixture
    assert _backtest_types(bar, ind, sp) == _live_types(bar, ind, sp)


@given(bar_and_bands())
@settings(deadline=None)
def test_parity_holds_under_default_params(fixture):
    bar, ind = fixture
    sp = SignalParams()
    assert _backtest_types(bar, ind, sp) == _live_types(bar, ind, sp)


def test_parity_on_a_hand_built_confluence_bar():
    """A worked case, so a strategy regression cannot hide behind shrinking."""
    bar = pd.Series(
        {"open": 100.0, "high": 101.0, "low": 94.0, "close": 96.0},
        name=pd.Timestamp("2026-07-29"),
    )
    ind = pd.Series(
        {
            "bb_lower": 95.0,
            "bb_mid": 100.0,
            "bb_upper": 105.0,
            "bb_pctb": -0.05,
            "k_full": 12.0,
            "d_full": 15.0,
            "k_fast": 10.0,
            "atr_14": 2.0,
        }
    )
    sp = SignalParams()
    expected = {
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    }
    assert _backtest_types(bar, ind, sp) == expected
    assert _live_types(bar, ind, sp) == expected


def test_live_path_never_fires_on_a_bar_the_backtest_ignores():
    """The asymmetric failure that matters: live firing on unmeasured events."""
    bar = pd.Series(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        name=pd.Timestamp("2026-07-29"),
    )
    ind = pd.Series(
        {
            "bb_lower": 90.0,
            "bb_mid": 100.0,
            "bb_upper": 110.0,
            "bb_pctb": 0.5,
            "k_full": 50.0,
            "d_full": 50.0,
            "k_fast": 50.0,
            "atr_14": 2.0,
        }
    )
    sp = SignalParams()
    assert _backtest_types(bar, ind, sp) == set()
    assert _live_types(bar, ind, sp) == set()
