"""ADR 108's live half: `poll.is_bear_reversal`.

The intraday analogue of the stored `bear_close_above_upper` flag. The two
must agree at the close and are allowed to differ before it, which is
exactly the distinction these tests pin.
"""

from __future__ import annotations

import pytest

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


# --------------------------------------------------------------------------
# ADR 117: how far from a reversal, not just whether
# --------------------------------------------------------------------------


def test_a_near_miss_and_a_runaway_are_no_longer_the_same_alert():
    """The defect ADR 117 fixes, stated as the two cases it separates.

    MPC on 2026-08-18 fired `confluence_high` at 364.70 against a band of
    358.56, a little above its open. A name trading far above its open is a
    different situation entirely, and the old notification produced
    byte-identical text for both because it tagged only confirmed
    reversals.
    """
    bands = _rev_bands(bb_upper=358.56)
    near = poll.reversal_state(364.70, 362.00, bands)
    runaway = poll.reversal_state(364.70, 340.00, bands)

    assert near.confirmed is False and runaway.confirmed is False
    assert poll._reversal_tag(near) != poll._reversal_tag(runaway)
    assert near.open_gap_atr < runaway.open_gap_atr


def test_the_gap_is_measured_in_atr_so_tickers_compare():
    """A $6 gap on MPC and a $0.03 gap on WBD are not comparable in dollars,
    and the reader is looking at five names at once."""
    wide = poll.reversal_state(110.0, 108.0, _rev_bands())  # atr_14 = 2.0
    assert wide.open_gap == pytest.approx(2.0)
    assert wide.open_gap_atr == pytest.approx(1.0)


def test_a_confirmed_reversal_reports_a_negative_gap():
    """Negative means price is below the open, which *is* the reversal.

    The sign carries the verdict, so a reader scanning a column of numbers
    does not need the label to know which fired.
    """
    state = poll.reversal_state(110.0, 114.0, _rev_bands())
    assert state.confirmed is True
    assert state.open_gap_atr == pytest.approx(-2.0)
    assert state.label == "confirmed"


def test_the_state_agrees_with_the_rule_it_describes():
    """`reversal_state` is presentation and `is_bear_reversal` is the rule.

    They are separate so a display change cannot alter a signal definition,
    which means they can drift. This is what stops them.
    """
    bands = _rev_bands()
    for price in (95.0, 104.9, 105.0, 110.0, 120.0):
        for day_open in (100.0, 105.0, 112.0, 130.0):
            assert poll.reversal_state(price, day_open, bands).confirmed is poll.is_bear_reversal(
                price, day_open, bands
            )


def test_an_unknown_open_is_reported_as_unknown_not_as_zero():
    """`None`, not `0.0`. A zero gap reads as "price is at the open", which
    is a measurement; a missing open is the absence of one."""
    state = poll.reversal_state(110.0, float("nan"), _rev_bands())
    assert state.open_gap is None and state.open_gap_atr is None
    assert "unavailable" in poll._reversal_tag(state)


def test_a_zero_atr_does_not_divide():
    from capitalscan.core.types import Bands

    flat = Bands(
        bb_lower=95.0,
        bb_mid=100.0,
        bb_upper=105.0,
        k_full=50.0,
        d_full=50.0,
        k_fast=50.0,
        atr_14=0.0,
    )
    state = poll.reversal_state(110.0, 108.0, flat)
    assert state.open_gap == pytest.approx(2.0)
    assert state.open_gap_atr is None


def test_price_back_inside_the_band_says_so():
    """`confluence_high` needs an upper-band touch, so this means the price
    fell back between the touch and the notification. Worth naming rather
    than rounding to "not confirmed"."""
    state = poll.reversal_state(100.0, 108.0, _rev_bands())
    assert state.above_band is False
    assert state.label == "n/a"
    assert "back inside the band" in poll._reversal_tag(state)


def test_the_confirmed_wording_is_unchanged():
    """Anything grepping notification history for the old phrase keeps
    matching. The tag is the interface here, not an implementation detail."""
    state = poll.reversal_state(110.0, 114.0, _rev_bands())
    assert poll._reversal_tag(state) == poll.REVERSAL_CONFIRMED_TAG
    assert "above band, below today's open" in poll.REVERSAL_CONFIRMED_TAG


def test_the_body_always_prints_all_three_numbers_the_rule_compares():
    """`bb_upper <= price < open`. Printing all three lets the reader apply
    the rule themselves, which is the whole point of showing near misses."""
    bands = _rev_bands(bb_upper=358.56)
    body = poll._reversal_body(poll.reversal_state(364.70, 362.0, bands), 364.70, 362.0, bands)
    assert "358.56" in body and "364.70" in body and "362.00" in body
    assert "not below the" in body


def test_the_body_says_what_it_cannot_evaluate():
    bands = _rev_bands()
    state = poll.reversal_state(110.0, float("nan"), bands)
    body = poll._reversal_body(state, 110.0, float("nan"), bands)
    assert "unavailable" in body
    assert "cannot be evaluated" in body


def test_state_json_carries_the_open_and_the_reversal_block():
    """Without `day_open` the stored report has the band and the price but
    not the third number the rule compares, so "how close was this?" can
    only be answered from a quote feed that is gone by then."""
    import pandas as pd

    bands = _rev_bands(bb_upper=358.56)
    ind = pd.Series({"k_full": 95.0, "atr_14": 2.0})
    payload = poll._state_json(bands, ind, 364.70, 362.0)

    assert payload["day_open"] == pytest.approx(362.0)
    assert payload["live_price"] == pytest.approx(364.70)
    assert payload["bear_reversal"]["confirmed"] is False
    assert payload["bear_reversal"]["above_band"] is True
    assert payload["bear_reversal"]["open_gap_atr"] == pytest.approx(1.35, abs=0.01)


def test_state_json_still_works_without_an_open():
    """The default keeps every existing caller valid."""
    import pandas as pd

    payload = poll._state_json(_rev_bands(), pd.Series({"k_full": 95.0}), 110.0)
    assert payload["day_open"] is None
    assert payload["bear_reversal"]["open_gap_atr"] is None
