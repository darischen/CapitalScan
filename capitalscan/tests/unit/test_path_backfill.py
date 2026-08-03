from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core.types import EntryKind, Side
from capitalscan.research.path_backfill import (
    fwd_window_for_signal,
    rows_for_event,
)


def _ticker_bars(dates):
    # One row per calendar date given, high/low/close all equal to a
    # simple ramp so tests can assert on exact values.
    data = [
        {"ts": pd.Timestamp(d), "high": 100 + i, "low": 90 + i, "close": 95 + i}
        for i, d in enumerate(dates)
    ]
    frame = pd.DataFrame(data)
    return frame.set_index(frame["ts"], drop=False)


def test_fwd_window_for_signal_returns_up_to_window_days_after_signal():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 10
    assert window.index[0] == pd.Timestamp(date(2024, 1, 2))


def test_fwd_window_for_signal_truncates_near_end_of_history_never_pads():
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 3  # only 3 trading days exist after signal_date


def test_fwd_window_for_signal_raises_if_signal_date_not_in_bars():
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    with pytest.raises(ValueError):
        fwd_window_for_signal(bars, date(2024, 6, 1), window_days=10)


def test_rows_for_event_skips_unfilled_entries():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    rows, n = rows_for_event(
        event_id=1,
        entry_price=float("nan"),
        side=Side.LONG,
        signal_date=date(2024, 1, 1),
        ticker_bars=bars,
        window_days=10,
    )
    assert rows.empty
    assert n is None


def test_rows_for_event_full_window_sets_fwd_window_days():
    dates = [date(2024, 1, i) for i in range(1, 20)]
    bars = _ticker_bars(dates)
    rows, n = rows_for_event(
        event_id=7,
        entry_price=100.0,
        side=Side.LONG,
        signal_date=date(2024, 1, 1),
        ticker_bars=bars,
        window_days=10,
    )
    assert n == 10
    assert list(rows["event_id"].unique()) == [7]
    assert list(rows["day_offset"]) == list(range(1, 11))
