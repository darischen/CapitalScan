from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.returns import entry_offset_for, path_for_event
from capitalscan.core.types import EntryKind, Side


@pytest.mark.parametrize(
    "kind,expected",
    [
        (EntryKind.TOUCH, 0),
        (EntryKind.TOUCH_5M, 0),
        (EntryKind.TOUCH_30M, 0),
        (EntryKind.NEXT_OPEN, 1),
    ],
)
def test_entry_offset_for(kind, expected):
    assert entry_offset_for(kind) == expected


def _bars(rows):
    # rows: list of (high, low, close)
    return pd.DataFrame(rows, columns=["high", "low", "close"])


def test_path_for_event_long_day_offsets_are_1_based():
    fwd_bars = _bars([(110, 95, 105), (120, 100, 115), (108, 90, 95)])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=fwd_bars)
    assert list(path["day_offset"]) == [1, 2, 3]


def test_path_for_event_long_favorable_adverse_terminal_match_per_bar_formula():
    fwd_bars = _bars([(110, 95, 105)])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=fwd_bars)
    row = path.iloc[0]
    assert row["favorable"] == pytest.approx((110 - 100) / 100)
    assert row["adverse"] == pytest.approx((95 - 100) / 100)
    assert row["terminal"] == pytest.approx((105 - 100) / 100)


def test_path_for_event_short_flips_sign_like_realized_return():
    fwd_bars = _bars([(110, 95, 105)])
    path = path_for_event(entry_price=100.0, side=Side.SHORT, fwd_bars=fwd_bars)
    row = path.iloc[0]
    # Short: favorable is price going DOWN, adverse is price going UP.
    assert row["favorable"] == pytest.approx((100 - 95) / 100)
    assert row["adverse"] == pytest.approx((100 - 110) / 100)
    assert row["terminal"] == pytest.approx(-(105 - 100) / 100)


def test_path_for_event_mfe_is_unclamped_negative_when_price_never_recovers():
    # ADR 089: MFE is not clamped at zero.
    fwd_bars = _bars([(99, 90, 92)])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=fwd_bars)
    assert path.iloc[0]["favorable"] < 0


def test_path_for_event_empty_fwd_bars_returns_empty_frame_never_padded():
    empty = _bars([])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=empty)
    assert list(path.columns) == ["day_offset", "favorable", "adverse", "terminal"]
    assert len(path) == 0
