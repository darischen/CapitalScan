"""ADR 108's live half: `poll.is_bear_reversal`.

The intraday analogue of the stored `bear_close_above_upper` flag. The two
must agree at the close and are allowed to differ before it, which is
exactly the distinction these tests pin.
"""

from __future__ import annotations

from capitalscan.jobs import poll

# --------------------------------------------------------------------------
# ADR 108's live half: is_bear_reversal
# --------------------------------------------------------------------------


def _rev_bands(bb_upper=105.0):
    from capitalscan.core.types import Bands

    return Bands(
        bb_lower=95.0,
        bb_mid=100.0,
        bb_upper=bb_upper,
        k_full=50.0,
        d_full=50.0,
        k_fast=50.0,
        atr_14=2.0,
    )


def test_price_above_the_band_and_below_the_open_is_a_reversal():
    assert poll.is_bear_reversal(price=106.0, day_open=110.0, bands=_rev_bands())


def test_price_above_the_band_but_also_above_the_open_is_not():
    """The bar is up on the day. That is a band breach, not a rejection."""
    assert not poll.is_bear_reversal(price=106.0, day_open=104.0, bands=_rev_bands())


def test_price_below_the_band_is_not_a_reversal_however_far_it_fell():
    """Falling all day is not this pattern. The whole point is that price
    is *still holding* above the band despite giving ground."""
    assert not poll.is_bear_reversal(price=100.0, day_open=120.0, bands=_rev_bands())


def test_price_exactly_at_the_band_counts():
    """`_breach` is "at or beyond, exact" with `price_tolerance = 0.0`, and
    the stored flag uses `close >= bb_upper`. The live half must not use a
    strict inequality where the stored half uses a loose one."""
    assert poll.is_bear_reversal(price=105.0, day_open=110.0, bands=_rev_bands())


def test_price_exactly_at_the_open_is_not_a_reversal():
    """A doji gave nothing back. `price < day_open`, strictly."""
    assert not poll.is_bear_reversal(price=110.0, day_open=110.0, bands=_rev_bands(100.0))


def test_a_missing_open_is_not_a_reversal():
    """A quote without `regularMarketOpen` cannot be evaluated. "Cannot
    evaluate" is not "fired" — and NaN comparisons are False in Python, so
    without the explicit guard this would silently return False anyway for
    the wrong reason."""
    assert not poll.is_bear_reversal(price=106.0, day_open=float("nan"), bands=_rev_bands())


def test_a_missing_price_is_not_a_reversal():
    assert not poll.is_bear_reversal(price=float("nan"), day_open=110.0, bands=_rev_bands())


def test_the_live_predicate_agrees_with_the_stored_flag_at_the_close():
    """The reason the two are allowed to differ mid-session but not at it.

    At the close the current quote *is* the close, so the live predicate and
    `core.indicators.bear_close_above_upper`'s condition are the same
    comparison on the same numbers. Verified rather than asserted in prose,
    because a drift here means the poller highlights a pattern the backtest
    never measured (ADR 006's failure mode).
    """
    bands = _rev_bands(105.0)
    for open_, close in [
        (110.0, 106.0),  # down bar, holds the band -> both true
        (104.0, 106.0),  # up bar -> both false
        (110.0, 100.0),  # down bar, lost the band -> both false
        (110.0, 105.0),  # down bar, exactly at the band -> both true
    ]:
        live = poll.is_bear_reversal(price=close, day_open=open_, bands=bands)
        stored = (open_ > close) and (round(close, 4) >= round(bands.bb_upper, 4))
        assert live is stored, (open_, close)
