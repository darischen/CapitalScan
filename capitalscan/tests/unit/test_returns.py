"""Unit tests for core/returns.py.

Two things here decide correctness. The `TOUCH` gap rule (DESIGN §5.4): a
bar that opened past the band never traded at the band, so filling at the
band invents a price nobody could have got. And the coverage constraint
(DESIGN §3.8): `TOUCH_5M` and `TOUCH_30M` return null before the hourly
archive begins, never a fabricated value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import returns as ret
from capitalscan.core.types import EntryKind, Side


def _bars(highs, lows, opens=None, closes=None, start="2026-07-01"):
    n = len(highs)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": opens if opens is not None else lows,
            "high": highs,
            "low": lows,
            "close": closes if closes is not None else highs,
        },
        index=idx,
    )


def _bar(open_=100.0, high=101.0, low=99.0, close=100.0, ts="2026-07-29"):
    return pd.Series(
        {"open": open_, "high": high, "low": low, "close": close},
        name=pd.Timestamp(ts),
    )


# ---------------------------------------------------------------------------
# realized_return
# ---------------------------------------------------------------------------


def test_realized_return_long_is_positive_when_price_rises():
    assert ret.realized_return(100.0, 104.0, Side.LONG) == pytest.approx(0.04)


def test_realized_return_short_is_positive_when_price_falls():
    assert ret.realized_return(100.0, 96.0, Side.SHORT) == pytest.approx(0.04)


def test_realized_return_short_is_the_sign_flip_of_long():
    assert ret.realized_return(100.0, 104.0, Side.SHORT) == pytest.approx(-0.04)


# ---------------------------------------------------------------------------
# forward_returns
# ---------------------------------------------------------------------------


def test_forward_returns_column_names_match_the_events_table():
    close = pd.Series([100.0] * 10, index=pd.date_range("2026-07-01", periods=10, freq="B"))
    out = ret.forward_returns(close, [1, 2, 3, 5, 10])
    assert list(out.columns) == [
        "fwd_ret_1d",
        "fwd_ret_2d",
        "fwd_ret_3d",
        "fwd_ret_5d",
        "fwd_ret_10d",
    ]


def test_forward_returns_look_forward_not_backward():
    close = pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2026-07-01", periods=3, freq="B"))
    out = ret.forward_returns(close, [1])
    assert out["fwd_ret_1d"].iloc[0] == pytest.approx(0.10)
    assert out["fwd_ret_1d"].iloc[1] == pytest.approx(0.10)


def test_forward_returns_are_null_at_the_tail_never_filled():
    close = pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2026-07-01", periods=3, freq="B"))
    out = ret.forward_returns(close, [1, 2])
    assert np.isnan(out["fwd_ret_1d"].iloc[-1])
    assert np.isnan(out["fwd_ret_2d"].iloc[-1])
    assert np.isnan(out["fwd_ret_2d"].iloc[-2])


def test_forward_returns_preserve_the_index():
    idx = pd.date_range("2026-07-01", periods=4, freq="B")
    out = ret.forward_returns(pd.Series([100.0] * 4, index=idx), [1])
    assert out.index.equals(idx)


# ---------------------------------------------------------------------------
# mfe_mae — path metrics over the forward window (DESIGN §5.6)
# ---------------------------------------------------------------------------


def test_mfe_is_the_best_high_relative_to_entry():
    bars = _bars(highs=[102.0, 105.0, 103.0], lows=[99.0, 100.0, 98.0])
    mfe, _, _ = ret.mfe_mae(100.0, Side.LONG, bars)
    assert mfe == pytest.approx(0.05)


def test_mae_is_the_worst_low_relative_to_entry():
    bars = _bars(highs=[102.0, 105.0, 103.0], lows=[99.0, 100.0, 98.0])
    _, mae, _ = ret.mfe_mae(100.0, Side.LONG, bars)
    assert mae == pytest.approx(-0.02)


def test_time_to_mfe_is_one_based_from_entry():
    bars = _bars(highs=[102.0, 105.0, 103.0], lows=[99.0, 100.0, 98.0])
    _, _, t = ret.mfe_mae(100.0, Side.LONG, bars)
    assert t == 2


def test_time_to_mfe_takes_the_first_bar_reaching_the_peak():
    bars = _bars(highs=[105.0, 105.0], lows=[99.0, 99.0])
    _, _, t = ret.mfe_mae(100.0, Side.LONG, bars)
    assert t == 1


def test_mfe_can_be_negative_when_the_path_never_recovers():
    # DESIGN §5.6 stores capture_ratio null when MFE <= 0, so MFE is not
    # clamped at zero. A gap down that never comes back has negative MFE.
    bars = _bars(highs=[95.0, 96.0], lows=[90.0, 91.0])
    mfe, mae, _ = ret.mfe_mae(100.0, Side.LONG, bars)
    assert mfe == pytest.approx(-0.04)
    assert mae == pytest.approx(-0.10)


def test_short_mfe_uses_lows_and_mae_uses_highs():
    bars = _bars(highs=[102.0, 105.0], lows=[99.0, 96.0])
    mfe, mae, t = ret.mfe_mae(100.0, Side.SHORT, bars)
    assert mfe == pytest.approx(0.04)  # low of 96 is favorable for a short
    assert mae == pytest.approx(-0.05)  # high of 105 is adverse
    assert t == 2


def test_mfe_mae_rejects_an_empty_window():
    with pytest.raises(ValueError):
        ret.mfe_mae(100.0, Side.LONG, _bars(highs=[], lows=[]))


# ---------------------------------------------------------------------------
# entry_price_for — TOUCH gap rule (DESIGN §5.4)
# ---------------------------------------------------------------------------


def test_touch_long_fills_at_the_band_when_the_bar_opened_above_it():
    bar = _bar(open_=100.0, low=94.0)
    price = ret.entry_price_for(EntryKind.TOUCH, bar, None, 95.0, side=Side.LONG)
    assert price == pytest.approx(95.0)


def test_touch_long_fills_at_the_open_when_the_bar_gapped_through():
    # Opened at 92, below the 95 band. It never traded at 95, so filling
    # there would invent a better price than was available.
    bar = _bar(open_=92.0, low=90.0)
    price = ret.entry_price_for(EntryKind.TOUCH, bar, None, 95.0, side=Side.LONG)
    assert price == pytest.approx(92.0)


def test_touch_short_fills_at_the_open_when_the_bar_gapped_above():
    bar = _bar(open_=108.0, high=110.0)
    price = ret.entry_price_for(EntryKind.TOUCH, bar, None, 105.0, side=Side.SHORT)
    assert price == pytest.approx(108.0)


def test_touch_short_fills_at_the_band_when_the_bar_opened_below_it():
    bar = _bar(open_=100.0, high=106.0)
    price = ret.entry_price_for(EntryKind.TOUCH, bar, None, 105.0, side=Side.SHORT)
    assert price == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# entry_price_for — NEXT_OPEN
# ---------------------------------------------------------------------------


def test_next_open_uses_the_following_bar_open():
    bar = _bar(low=94.0)
    nxt = _bar(open_=96.5, ts="2026-07-30")
    assert ret.entry_price_for(EntryKind.NEXT_OPEN, bar, nxt, 95.0) == pytest.approx(96.5)


def test_next_open_is_null_on_a_terminal_bar():
    bar = _bar(low=94.0)
    assert np.isnan(ret.entry_price_for(EntryKind.NEXT_OPEN, bar, None, 95.0))


# ---------------------------------------------------------------------------
# entry_price_for — hourly coverage constraint (DESIGN §3.8)
# ---------------------------------------------------------------------------


def test_touch_5m_is_null_without_hourly_data():
    bar = _bar(low=94.0)
    assert np.isnan(ret.entry_price_for(EntryKind.TOUCH_5M, bar, None, 95.0, hourly=None))


def test_touch_30m_is_null_without_hourly_data():
    bar = _bar(low=94.0)
    assert np.isnan(ret.entry_price_for(EntryKind.TOUCH_30M, bar, None, 95.0, hourly=None))


def _hourly():
    idx = pd.date_range("2026-07-29 09:30", periods=3, freq="h", tz="America/New_York")
    return pd.DataFrame(
        {
            "open": [99.0, 96.0, 97.0],
            "high": [99.5, 96.5, 98.0],
            "low": [97.0, 94.0, 96.5],
            "close": [97.5, 96.0, 97.5],
        },
        index=idx,
    )


def test_touch_30m_uses_the_close_of_the_first_breaching_hourly_bar():
    bar = _bar(low=94.0)
    price = ret.entry_price_for(
        EntryKind.TOUCH_30M, bar, None, 95.0, side=Side.LONG, hourly=_hourly()
    )
    assert price == pytest.approx(96.0)


def test_touch_5m_interpolates_between_the_band_and_that_bars_close():
    bar = _bar(low=94.0)
    price = ret.entry_price_for(
        EntryKind.TOUCH_5M, bar, None, 95.0, side=Side.LONG, hourly=_hourly()
    )
    # 5 minutes into a 60-minute bar: 95 + (96 - 95) * 5/60
    assert price == pytest.approx(95.0 + (96.0 - 95.0) * 5 / 60)


def test_touch_5m_is_null_when_no_hourly_bar_breaches():
    bar = _bar(low=94.0)
    price = ret.entry_price_for(
        EntryKind.TOUCH_5M, bar, None, 90.0, side=Side.LONG, hourly=_hourly()
    )
    assert np.isnan(price)


def test_hourly_kinds_pick_the_first_breaching_bar_not_the_deepest():
    idx = pd.date_range("2026-07-29 09:30", periods=2, freq="h", tz="America/New_York")
    hourly = pd.DataFrame(
        {
            "open": [96.0, 93.0],
            "high": [96.5, 93.5],
            "low": [94.5, 90.0],  # both breach 95; the first one is the entry
            "close": [95.5, 92.0],
        },
        index=idx,
    )
    price = ret.entry_price_for(
        EntryKind.TOUCH_30M, bar=_bar(low=90.0), next_bar=None, touch_level=95.0, hourly=hourly
    )
    assert price == pytest.approx(95.5)
