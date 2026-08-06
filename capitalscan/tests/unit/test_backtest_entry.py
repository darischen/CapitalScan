"""Tests for `research/enrich.py::resolve_entries` — DESIGN §5.2 step 7,
§5.4 (Session 9, Task 5).

Four things carry the correctness load here, all delegated to
`core.returns.entry_price_for` and re-verified at this orchestration layer:

1. The `TOUCH` gap rule (long and short): a bar that opened past the band
   never traded at the band, so it fills at the open, and `entry_gapped`
   records that it did.
2. `NEXT_OPEN` on a terminal bar (no next session) yields a null price and
   a null `entry_date`, never the current close.
3. `TOUCH_5M` / `TOUCH_30M` yield NaN when `hourly is None` — a coverage
   limitation carried in the data (DESIGN §3.8) — and the row is still
   produced, not dropped.
4. Slippage applies on top of the resolved price, adverse to the side: a
   long pays more, a short receives less.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import CostParams
from capitalscan.core.types import EntryKind
from capitalscan.research.enrich import resolve_entries

CP = CostParams()  # slippage_bps = 3.0
ZERO_SLIP = CostParams(slippage_bps=0.0)


def _bars(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _candidate(**overrides) -> pd.Series:
    row = {
        "ticker": "TSM",
        "signal_date": date(2026, 7, 30),
        "signal_type": "bb_lower_touch",
        "signal_types_all": ["bb_lower_touch"],
        "signal_strength": 1,
        "side": "long",
        "touch_level": 95.0,
    }
    row.update(overrides)
    return pd.Series(row)


def _by_kind(rows: list[dict]) -> dict:
    return {r["entry_kind"]: r for r in rows}


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def test_produces_one_row_per_entry_kind():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    rows = resolve_entries(_candidate(), bars, None, CP)
    kinds = {r["entry_kind"] for r in rows}
    assert kinds == {k.value for k in EntryKind}
    assert len(rows) == 4


# ---------------------------------------------------------------------------
# TOUCH gap rule (long)
# ---------------------------------------------------------------------------


def test_touch_long_fills_at_band_and_is_not_gapped_when_open_is_above_band():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 100.0,
                "high": 101.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(resolve_entries(_candidate(touch_level=95.0), bars, None, ZERO_SLIP))
    touch = rows[EntryKind.TOUCH.value]
    assert touch["entry_price"] == pytest.approx(95.0)
    assert touch["entry_gapped"] is False
    assert touch["entry_date"] == date(2026, 7, 30)


def test_touch_long_fills_at_open_and_is_gapped_when_open_is_below_band():
    # Opened at 92, below the 95 band — never traded at 95.
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 92.0,
                "high": 93.0,
                "low": 90.0,
                "close": 91.0,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(resolve_entries(_candidate(touch_level=95.0), bars, None, ZERO_SLIP))
    touch = rows[EntryKind.TOUCH.value]
    assert touch["entry_price"] == pytest.approx(92.0)
    assert touch["entry_gapped"] is True


# ---------------------------------------------------------------------------
# TOUCH gap rule (short) — mirrored
# ---------------------------------------------------------------------------


def test_touch_short_fills_at_band_and_is_not_gapped_when_open_is_below_band():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 100.0,
                "high": 106.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 1,
            },
        ]
    )
    candidate = _candidate(side="short", touch_level=105.0, signal_type="bb_upper_touch")
    rows = _by_kind(resolve_entries(candidate, bars, None, ZERO_SLIP))
    touch = rows[EntryKind.TOUCH.value]
    assert touch["entry_price"] == pytest.approx(105.0)
    assert touch["entry_gapped"] is False


def test_touch_short_fills_at_open_and_is_gapped_when_open_is_above_band():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 108.0,
                "high": 110.0,
                "low": 107.0,
                "close": 109.0,
                "volume": 1,
            },
        ]
    )
    candidate = _candidate(side="short", touch_level=105.0, signal_type="bb_upper_touch")
    rows = _by_kind(resolve_entries(candidate, bars, None, ZERO_SLIP))
    touch = rows[EntryKind.TOUCH.value]
    assert touch["entry_price"] == pytest.approx(108.0)
    assert touch["entry_gapped"] is True


# ---------------------------------------------------------------------------
# NEXT_OPEN
# ---------------------------------------------------------------------------


def test_next_open_uses_the_following_session_open():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
            {
                "ticker": "TSM",
                "ts": "2026-07-31",
                "open": 96.5,
                "high": 98.0,
                "low": 96.0,
                "close": 97.5,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(resolve_entries(_candidate(), bars, None, ZERO_SLIP))
    next_open = rows[EntryKind.NEXT_OPEN.value]
    assert next_open["entry_price"] == pytest.approx(96.5)
    assert next_open["entry_date"] == date(2026, 7, 31)


def test_next_open_is_null_on_a_terminal_bar_not_the_current_close():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(resolve_entries(_candidate(), bars, None, ZERO_SLIP))
    next_open = rows[EntryKind.NEXT_OPEN.value]
    assert np.isnan(next_open["entry_price"])
    assert next_open["entry_date"] is None


def test_next_open_entry_gapped_is_not_applicable():
    # NEXT_OPEN never references a band level, so "did the day gap past
    # the band" has no referent — None, not a defaulted False.
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
            {
                "ticker": "TSM",
                "ts": "2026-07-31",
                "open": 96.5,
                "high": 98.0,
                "low": 96.0,
                "close": 97.5,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(resolve_entries(_candidate(), bars, None, ZERO_SLIP))
    assert rows[EntryKind.NEXT_OPEN.value]["entry_gapped"] is None


# ---------------------------------------------------------------------------
# hourly coverage constraint (DESIGN §3.8)
# ---------------------------------------------------------------------------


def test_touch_5m_and_30m_are_null_but_still_produced_when_hourly_is_none():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(resolve_entries(_candidate(), bars, None, ZERO_SLIP))
    assert set(rows.keys()) == {k.value for k in EntryKind}
    assert np.isnan(rows[EntryKind.TOUCH_5M.value]["entry_price"])
    assert np.isnan(rows[EntryKind.TOUCH_30M.value]["entry_price"])
    # The gap fact is still known even though the hourly fill price isn't.
    assert rows[EntryKind.TOUCH_5M.value]["entry_gapped"] is False


def test_touch_30m_uses_the_close_of_the_first_breaching_hourly_bar_on_the_signal_day():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    hourly = pd.DataFrame(
        {
            "ticker": ["TSM", "TSM", "TSM"],
            "ts": pd.to_datetime(["2026-07-30 09:30", "2026-07-30 10:30", "2026-07-30 11:30"]),
            "open": [99.0, 96.0, 97.0],
            "high": [99.5, 96.5, 98.0],
            "low": [97.0, 94.0, 96.5],
            "close": [97.5, 96.0, 97.5],
        }
    )
    rows = _by_kind(resolve_entries(_candidate(), bars, hourly, ZERO_SLIP))
    assert rows[EntryKind.TOUCH_30M.value]["entry_price"] == pytest.approx(96.0)


def test_hourly_slice_is_scoped_to_the_signal_day_not_the_whole_ticker_history():
    # A prior day's hourly bars breach the level too; if the slice weren't
    # scoped to the signal day, the resolver would price off the wrong
    # session entirely.
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    hourly = pd.DataFrame(
        {
            "ticker": ["TSM", "TSM"],
            "ts": pd.to_datetime(["2026-07-29 09:30", "2026-07-30 09:30"]),
            "open": [80.0, 96.0],
            "high": [80.5, 96.5],
            "low": [79.0, 94.0],
            "close": [79.5, 96.0],
        }
    )
    rows = _by_kind(resolve_entries(_candidate(), bars, hourly, ZERO_SLIP))
    # Must pick the 2026-07-30 bar (close 96.0), never the prior day's
    # (close 79.5), which would also "breach" a 95.0 level.
    assert rows[EntryKind.TOUCH_30M.value]["entry_price"] == pytest.approx(96.0)


# ---------------------------------------------------------------------------
# slippage — adverse to the side, on top of the resolved price
# ---------------------------------------------------------------------------


def test_slippage_raises_the_fill_price_for_a_long():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 100.0,
                "high": 101.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    plain = _by_kind(resolve_entries(_candidate(touch_level=95.0), bars, None, ZERO_SLIP))
    slipped = _by_kind(resolve_entries(_candidate(touch_level=95.0), bars, None, CP))
    touch_plain = plain[EntryKind.TOUCH.value]["entry_price"]
    touch_slipped = slipped[EntryKind.TOUCH.value]["entry_price"]
    assert touch_slipped > touch_plain
    assert touch_slipped == pytest.approx(touch_plain * (1 + CP.slippage_bps / 1e4))


def test_slippage_lowers_the_fill_price_for_a_short():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 100.0,
                "high": 106.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 1,
            },
        ]
    )
    candidate = _candidate(side="short", touch_level=105.0, signal_type="bb_upper_touch")
    plain = _by_kind(resolve_entries(candidate, bars, None, ZERO_SLIP))
    slipped = _by_kind(resolve_entries(candidate, bars, None, CP))
    touch_plain = plain[EntryKind.TOUCH.value]["entry_price"]
    touch_slipped = slipped[EntryKind.TOUCH.value]["entry_price"]
    assert touch_slipped < touch_plain
    assert touch_slipped == pytest.approx(touch_plain * (1 - CP.slippage_bps / 1e4))


def test_slippage_applies_to_next_open_too():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
            {
                "ticker": "TSM",
                "ts": "2026-07-31",
                "open": 96.5,
                "high": 98.0,
                "low": 96.0,
                "close": 97.5,
                "volume": 1,
            },
        ]
    )
    plain = _by_kind(resolve_entries(_candidate(), bars, None, ZERO_SLIP))
    slipped = _by_kind(resolve_entries(_candidate(), bars, None, CP))
    plain_price = plain[EntryKind.NEXT_OPEN.value]["entry_price"]
    slipped_price = slipped[EntryKind.NEXT_OPEN.value]["entry_price"]
    assert slipped_price > plain_price


# ---------------------------------------------------------------------------
# stochastic-only signal: touch_level is None, never NaN
# ---------------------------------------------------------------------------


def test_stochastic_only_signal_with_none_touch_level_produces_nan_touch_price():
    # `core.types.SignalHit.touch_level` is None (never NaN) for a
    # stochastic-only hit — this must not raise or silently coerce.
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-30",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    rows = _by_kind(
        resolve_entries(
            _candidate(touch_level=None, signal_type="stoch_oversold"), bars, None, ZERO_SLIP
        )
    )
    assert np.isnan(rows[EntryKind.TOUCH.value]["entry_price"])
    assert rows[EntryKind.TOUCH.value]["entry_gapped"] is None
    # NEXT_OPEN never depended on touch_level and stays a real NaN price
    # only because this bar is terminal, not because of the None level.
    assert np.isnan(rows[EntryKind.NEXT_OPEN.value]["entry_price"])


# ---------------------------------------------------------------------------
# input errors
# ---------------------------------------------------------------------------


def test_raises_when_the_signal_bar_itself_is_missing():
    bars = _bars(
        [
            {
                "ticker": "TSM",
                "ts": "2026-07-29",
                "open": 96.0,
                "high": 97.0,
                "low": 94.0,
                "close": 95.5,
                "volume": 1,
            },
        ]
    )
    with pytest.raises(ValueError):
        resolve_entries(_candidate(signal_date=date(2026, 7, 30)), bars, None, ZERO_SLIP)
