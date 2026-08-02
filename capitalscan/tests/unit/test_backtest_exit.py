"""Tests for `research/enrich.py::resolve_exit_for_entry` — DESIGN §5.2
step 8 (Session 9, Task 6).

`core.exits.resolve_exit` already carries the correctness load for exit
logic (its own suite plus the property tests). This module's job is
slicing frames and shaping a dict — nothing more (BUILD §9.3: no second
band comparison). What *is* new correctness surface at this layer:

1. `ind_at_entry` must always reach `resolve_exit`. Band levels for
   forward bar i come from bar i-1, which `resolve_exit` derives by
   shifting `fwd_ind` — the first forward bar has no i-1 row inside that
   frame, so `ind_at_entry` is the only source of a prior-bar band level
   for it. Omitting it doesn't raise; it silently skips band exits on the
   first bar. `test_first_forward_bar_triggers_a_band_exit` and its
   companion `test_omitting_ind_at_entry_would_have_missed_the_same_exit`
   prove this both ways: our function catches the exit, and the same
   fixture fed straight to `core.exits.resolve_exit` without
   `ind_at_entry` times out instead.
2. `entry_idx` is positional into `bars`/`indicators`, and must be the
   bar the position actually opened on — for `NEXT_OPEN` that is one bar
   later than the signal bar. `test_entry_idx_offsets_the_forward_window`
   proves the slice starts at `entry_idx + 1`, not the signal bar.
3. Two "can't resolve" cases that must return a null-shaped dict rather
   than raise: a `NaN` entry price (the position never filled) and a
   completely empty forward window (the signal fired on the last
   available bar).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import exits as core_exits
from capitalscan.core.config import ExitParams
from capitalscan.core.types import ExitReason, Side
from capitalscan.research.enrich import resolve_exit_for_entry


def _frame(cols: dict, start: str = "2026-07-30") -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(cols, index=idx)


def _entry(price: float = 100.0) -> dict:
    return {
        "entry_kind": "touch",
        "entry_date": date(2026, 7, 30),
        "entry_price": price,
        "entry_gapped": False,
    }


# ---------------------------------------------------------------------------
# The sharpest requirement: ind_at_entry always reaches resolve_exit, so the
# FIRST forward bar can trigger a band exit.
# ---------------------------------------------------------------------------


def _first_bar_band_fixture():
    """Entry bar at position 0; three forward bars follow. Only the entry
    bar's own indicator row (`ind_at_entry`) carries a reachable upper band
    (102.0) — the forward bars' own indicator rows carry an unreachable one
    (999.0), so a band exit here can only have come from the i-1 rule
    correctly using `ind_at_entry` for the first forward bar.
    """
    bars = _frame(
        {
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [100.5, 102.5, 100.5, 100.5],
            "low": [99.5, 99.5, 99.5, 99.5],
            "close": [100.0, 101.0, 100.0, 100.0],
        }
    )
    indicators = _frame(
        {
            "bb_upper": [102.0, 999.0, 999.0, 999.0],
            "bb_mid": [1.0, 1.0, 1.0, 1.0],  # far away, out of reach downward
            "bb_lower": [1.0, 1.0, 1.0, 1.0],
            "k_full": [50.0, 50.0, 50.0, 50.0],  # nowhere near exit_stoch_threshold
        }
    )
    # stop_mode="none" and the default 4% target (104.0) both stay clear of
    # bar 1's 102.5 high, so nothing but the band exit can fire here.
    ep = ExitParams(stop_mode="none", max_hold_days=3)
    return bars, indicators, ep


def test_first_forward_bar_triggers_a_band_exit():
    bars, indicators, ep = _first_bar_band_fixture()
    result = resolve_exit_for_entry(_entry(), 0, Side.LONG, bars, indicators, ep)
    assert result["exit_reason"] == ExitReason.UPPER_BAND.value
    assert result["exit_price"] == pytest.approx(102.0)
    assert result["exit_idx"] == 0
    assert result["holding_days"] == 1


def test_omitting_ind_at_entry_would_have_missed_the_same_exit():
    # Same fixture, called straight against core.exits.resolve_exit with
    # ind_at_entry omitted — proves the exit above depends on us passing it,
    # not on some other coincidence in the fixture.
    bars, indicators, ep = _first_bar_band_fixture()
    fwd_bars = bars.iloc[1:4]
    fwd_ind = indicators.iloc[1:4]
    result = core_exits.resolve_exit(
        entry_price=100.0,
        entry_idx=0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        fwd_ind=fwd_ind,
        atr_at_entry=float("nan"),
        ep=ep,
        # ind_at_entry intentionally omitted (defaults to None).
    )
    assert result.reason is ExitReason.TIMEOUT


# ---------------------------------------------------------------------------
# holding_days == exit_idx + 1
# ---------------------------------------------------------------------------


def test_holding_days_equals_exit_idx_plus_one():
    bars, indicators, ep = _first_bar_band_fixture()
    result = resolve_exit_for_entry(_entry(), 0, Side.LONG, bars, indicators, ep)
    assert result["holding_days"] == result["exit_idx"] + 1


# ---------------------------------------------------------------------------
# entry_idx offsets the forward window — off-by-one here shifts every exit.
# ---------------------------------------------------------------------------


def test_entry_idx_offsets_the_forward_window():
    # 6 bars: positions 0-1 are noise that must NOT be read as the entry or
    # its forward window. entry_idx=2. A target hit (104.0) sits on bar
    # position 3 — reachable only if the window starts at entry_idx+1=3.
    bars = _frame(
        {
            "open": [999.0, 999.0, 100.0, 100.0, 100.0, 100.0],
            "high": [0.0, 0.0, 100.5, 104.5, 100.5, 100.5],
            "low": [999.0, 999.0, 99.5, 99.5, 99.5, 99.5],
            "close": [999.0, 999.0, 100.0, 104.0, 100.0, 100.0],
        }
    )
    indicators = _frame(
        {
            "bb_upper": [1.0, 1.0, 999.0, 999.0, 999.0, 999.0],
            "bb_mid": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            "bb_lower": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            "k_full": [1.0, 1.0, 50.0, 50.0, 50.0, 50.0],
        }
    )
    ep = ExitParams(stop_mode="none", max_hold_days=3)
    result = resolve_exit_for_entry(_entry(), 2, Side.LONG, bars, indicators, ep)
    assert result["exit_reason"] == ExitReason.TARGET.value
    assert result["exit_idx"] == 0  # first bar of the forward window (position 3)
    assert result["exit_date"] == date(2026, 8, 4)


# ---------------------------------------------------------------------------
# truncated window (end-of-data) resolves instead of raising
# ---------------------------------------------------------------------------


def test_truncated_forward_window_times_out_instead_of_raising():
    # 3 bars total: entry_idx=0, so only 2 forward bars exist even though
    # max_hold_days asks for 5 (e.g. a signal near the end of the ingested
    # history, or a delisting).
    bars = _frame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [100.5, 100.5, 100.5],
            "low": [99.5, 99.5, 99.5],
            "close": [100.0, 100.0, 100.7],
        }
    )
    indicators = _frame(
        {
            "bb_upper": [999.0, 999.0, 999.0],
            "bb_mid": [1.0, 1.0, 1.0],
            "bb_lower": [1.0, 1.0, 1.0],
            "k_full": [50.0, 50.0, 50.0],
        }
    )
    ep = ExitParams(stop_mode="none", max_hold_days=5)
    result = resolve_exit_for_entry(_entry(), 0, Side.LONG, bars, indicators, ep)
    assert result["exit_reason"] == ExitReason.TIMEOUT.value
    assert result["holding_days"] == 2
    assert result["exit_price"] == pytest.approx(100.7)


# ---------------------------------------------------------------------------
# genuinely empty forward window (signal on the last available bar)
# ---------------------------------------------------------------------------


def test_empty_forward_window_does_not_raise():
    bars = _frame(
        {
            "open": [100.0],
            "high": [100.5],
            "low": [99.5],
            "close": [100.0],
        }
    )
    indicators = _frame(
        {
            "bb_upper": [999.0],
            "bb_mid": [1.0],
            "bb_lower": [1.0],
            "k_full": [50.0],
        }
    )
    ep = ExitParams()
    result = resolve_exit_for_entry(_entry(), 0, Side.LONG, bars, indicators, ep)
    assert result["exit_reason"] is None
    assert result["exit_idx"] is None
    assert result["holding_days"] is None
    assert np.isnan(result["exit_price"])
    assert result["exit_date"] is None


# ---------------------------------------------------------------------------
# NaN entry price — the position never filled
# ---------------------------------------------------------------------------


def test_nan_entry_price_returns_unresolved_without_raising():
    bars, indicators, ep = _first_bar_band_fixture()
    result = resolve_exit_for_entry(_entry(price=float("nan")), 0, Side.LONG, bars, indicators, ep)
    assert result["exit_reason"] is None
    assert result["exit_idx"] is None
    assert result["holding_days"] is None
    assert result["ambiguous"] is None
    assert np.isnan(result["exit_price"])
    assert result["exit_date"] is None


# ---------------------------------------------------------------------------
# no derived long/short stochastic threshold (ADR 092) — sanity check that
# the two fields are read independently, not one from the other
# ---------------------------------------------------------------------------


def test_short_side_uses_its_own_stoch_threshold_not_a_mirror_of_the_long():
    bars = _frame(
        {
            "open": [100.0, 100.0],
            "high": [100.5, 100.5],
            "low": [99.5, 99.5],  # stays well clear of the 96.0 short target
            "close": [100.0, 99.8],
        }
    )
    indicators = _frame(
        {
            "bb_upper": [999.0, 999.0],
            "bb_mid": [1.0, 1.0],
            "bb_lower": [1.0, 1.0],
            "k_full": [15.0, 15.0],  # below a custom short threshold of 18, not 20
        }
    )
    ep = ExitParams(stop_mode="none", exit_stoch_threshold_short=18.0, max_hold_days=1)
    result = resolve_exit_for_entry(
        _entry(price=100.0), 0, Side.SHORT, bars, indicators, ep
    )
    assert result["exit_reason"] == ExitReason.STOCH_80.value
