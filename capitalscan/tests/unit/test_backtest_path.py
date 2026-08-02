"""Tests for `research/enrich.py::path_metrics` — DESIGN §5.6 (Session 9,
Task 7).

Two windows, and conflating them is the whole difficulty of this task:

- MFE/MAE are computed over `[t+1, exit_idx]` — the bars the position was
  actually open for. `fwd_bars` already starts at t+1 (the same frame
  `resolve_exit_for_entry` slices for `core.exits.resolve_exit`), and
  `exit_idx` is 0-based *within* that frame (the same convention
  `ExitResult.exit_idx` and `test_backtest_exit.py` already use), so the
  held window is `fwd_bars.iloc[:exit_idx + 1]`.
- Reachability (`touched_*pct` / `day_touched_*pct`) is computed over the
  FULL `fwd_bars`, regardless of where `exit_idx` landed — "would a limit
  order at +5% have filled" doesn't care when the position actually closed.

Three things carry the correctness load here (task brief):

1. MFE is not clamped at zero (ADR 089) — `core.returns.mfe_mae` already
   gets this right; this suite only checks `path_metrics` doesn't reclamp
   it on the way out.
2. The reachability window is the full 5 bars even when the exit fired
   early — `test_reachability_uses_full_window_past_an_early_exit` is the
   brief's own pinned fixture: exit on bar 2 (`exit_idx=1`), a touch on
   bar 4 that the MFE window (bars 1-2) never sees.
3. `capture_ratio` is null when `MFE <= 0` — a zero MFE is division by
   zero, not merely an odd ratio, so the boundary is `<=`, not `<`.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import StatsParams
from capitalscan.core.types import Side
from capitalscan.research.enrich import path_metrics

_TARGETS = StatsParams().reach_targets
_HORIZONS = StatsParams().fwd_ret_horizons


def _fwd_bars(highs, lows, opens=None, closes=None, start="2026-07-30"):
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


def _adj_close(values, start="2026-07-29"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="B"))


# ---------------------------------------------------------------------------
# MFE is not clamped at zero (ADR 089) — the sharpest requirement.
# ---------------------------------------------------------------------------


def test_mfe_is_not_clamped_at_zero():
    # Gaps down on bar 1 and never recovers: every high stays below entry,
    # so MFE is genuinely negative.
    fwd_bars = _fwd_bars(highs=[95.0, 96.0], lows=[90.0, 91.0])
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=1,
        exit_price=96.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["mfe"] == pytest.approx(-0.04)
    assert result["mfe"] < 0


def test_capture_ratio_is_null_when_mfe_is_negative():
    fwd_bars = _fwd_bars(highs=[95.0, 96.0], lows=[90.0, 91.0])
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=1,
        exit_price=96.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["capture_ratio"] is None


def test_capture_ratio_is_null_when_mfe_is_exactly_zero():
    # MFE == 0 is a division by zero, not merely an odd ratio — the
    # boundary is <=, not <.
    fwd_bars = _fwd_bars(highs=[100.0], lows=[97.0])
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=0,
        exit_price=98.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["mfe"] == pytest.approx(0.0)
    assert result["capture_ratio"] is None


def test_capture_ratio_is_realized_return_over_mfe_when_mfe_is_positive():
    fwd_bars = _fwd_bars(highs=[104.0, 106.0], lows=[99.0, 100.0])
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=1,
        exit_price=103.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    # MFE over [t+1, exit_idx] = bars 0-1 -> max high 106 -> MFE = 0.06.
    # R_exit = (103 - 100) / 100 = 0.03. capture_ratio = 0.03 / 0.06 = 0.5.
    assert result["mfe"] == pytest.approx(0.06)
    assert result["capture_ratio"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Reachability: full 5-bar window regardless of when the exit fired. The
# brief's own pinned fixture: exit on bar 2, touch on bar 4.
# ---------------------------------------------------------------------------


def test_reachability_uses_full_window_past_an_early_exit():
    # 5 forward bars. The exit fires on bar 2 (exit_idx=1, 0-based -> the
    # position was open for 2 days). A +5% touch (105.0) happens on bar 4
    # only — bars 1-2 (the MFE window) never reach it, so a bug that scoped
    # reachability to the MFE window would report touched_5pct=False here.
    fwd_bars = _fwd_bars(
        highs=[101.0, 102.0, 103.0, 105.5, 100.0],
        lows=[99.0, 99.0, 99.0, 99.0, 99.0],
    )
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=1,
        exit_price=102.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["touched_5pct"] is True
    assert result["day_touched_5pct"] == 4
    # And the MFE window genuinely never saw it: MFE is bounded by bars 1-2
    # (highs 101, 102), nowhere near the 105.5 touch on bar 4.
    assert result["mfe"] == pytest.approx(0.02)


def test_reachability_target_never_touched_is_false_with_null_day():
    fwd_bars = _fwd_bars(highs=[100.5] * 5, lows=[99.5] * 5)
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=4,
        exit_price=100.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["touched_2pct"] is False
    # None, not NaN: `events.day_touched_2pct` is `integer` in the schema,
    # and a NaN written to an integer column is a write error, not a null.
    assert result["day_touched_2pct"] is None
    assert not isinstance(result["day_touched_2pct"], float)


def test_reachability_first_touching_bar_wins_not_the_deepest():
    # Bar 2 touches +3% (103.0) first; bar 4 goes deeper (110.0), but the
    # day recorded must be the first bar that reached the level, not the
    # bar with the largest excursion.
    fwd_bars = _fwd_bars(
        highs=[101.0, 103.5, 101.0, 110.0, 101.0],
        lows=[99.0] * 5,
    )
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=4,
        exit_price=101.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["touched_3pct"] is True
    assert result["day_touched_3pct"] == 2


def test_reachability_short_uses_lows_against_a_level_below_entry():
    # Short side: the favorable direction is down, so the +5% target is a
    # level *below* entry (95.0), and it's the lows that can touch it.
    fwd_bars = _fwd_bars(
        highs=[101.0] * 5,
        lows=[99.0, 94.5, 96.0, 99.0, 99.0],
    )
    result = path_metrics(
        entry_price=100.0,
        side=Side.SHORT,
        fwd_bars=fwd_bars,
        exit_idx=4,
        exit_price=99.0,
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert result["touched_5pct"] is True
    assert result["day_touched_5pct"] == 2


# ---------------------------------------------------------------------------
# Column-name derivation from StatsParams.reach_targets — must yield exactly
# the four `events` schema names.
# ---------------------------------------------------------------------------


def test_reach_target_column_names_match_the_events_schema():
    fwd_bars = _fwd_bars(highs=[100.5] * 5, lows=[99.5] * 5)
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=4,
        exit_price=100.0,
        targets=StatsParams().reach_targets,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    for name in (
        "touched_2pct",
        "touched_3pct",
        "touched_5pct",
        "touched_10pct",
        "day_touched_2pct",
        "day_touched_3pct",
        "day_touched_5pct",
        "day_touched_10pct",
    ):
        assert name in result


# ---------------------------------------------------------------------------
# fwd_ret_*d — unconditional forward returns, independent of the MFE/exit
# window, sourced from total-return adjusted close via core.returns.forward_returns.
# ---------------------------------------------------------------------------


def test_fwd_ret_columns_present_for_every_configured_horizon():
    fwd_bars = _fwd_bars(highs=[100.5] * 5, lows=[99.5] * 5)
    adj_close = _adj_close([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0])
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=4,
        exit_price=100.0,
        targets=_TARGETS,
        adj_close_fwd=adj_close,
        horizons=_HORIZONS,
    )
    for h in _HORIZONS:
        assert f"fwd_ret_{h}d" in result
    # fwd_ret_10d in particular: the horizon that exceeds max_hold_days=5,
    # which is exactly why it cannot come from `fwd_bars` and must be
    # sourced from a longer adj_close window.
    assert result["fwd_ret_10d"] == pytest.approx(0.10)
    assert result["fwd_ret_1d"] == pytest.approx(0.01)


def test_fwd_ret_is_null_at_the_tail_never_filled():
    fwd_bars = _fwd_bars(highs=[100.5] * 5, lows=[99.5] * 5)
    # Only 2 rows of adj_close -> fwd_ret_10d cannot be computed.
    adj_close = _adj_close([100.0, 101.0])
    result = path_metrics(
        entry_price=100.0,
        side=Side.LONG,
        fwd_bars=fwd_bars,
        exit_idx=4,
        exit_price=100.0,
        targets=_TARGETS,
        adj_close_fwd=adj_close,
        horizons=_HORIZONS,
    )
    assert math.isnan(result["fwd_ret_10d"])


def test_fwd_ret_is_computed_even_when_the_position_never_resolved():
    # Unconditional means unconditional: even an unresolved exit
    # (exit_idx=None) shouldn't blank out the baseline forward returns,
    # since they don't depend on entry/exit at all.
    adj_close = _adj_close([100.0, 101.0, 102.0])
    result = path_metrics(
        entry_price=float("nan"),
        side=Side.LONG,
        fwd_bars=pd.DataFrame(columns=["open", "high", "low", "close"]),
        exit_idx=None,
        exit_price=float("nan"),
        targets=_TARGETS,
        adj_close_fwd=adj_close,
        horizons=[1, 2],
    )
    assert result["fwd_ret_1d"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Unresolved exit (exit_idx=None) — nulls the exit-dependent fields rather
# than raising or fabricating a result.
# ---------------------------------------------------------------------------


def test_unresolved_exit_nulls_mfe_and_reachability_without_raising():
    result = path_metrics(
        entry_price=float("nan"),
        side=Side.LONG,
        fwd_bars=pd.DataFrame(columns=["open", "high", "low", "close"]),
        exit_idx=None,
        exit_price=float("nan"),
        targets=_TARGETS,
        adj_close_fwd=None,
        horizons=_HORIZONS,
    )
    assert np.isnan(result["mfe"])
    assert np.isnan(result["mae"])
    assert result["time_to_mfe"] is None
    assert result["capture_ratio"] is None
    assert result["touched_5pct"] is None
    assert result["day_touched_5pct"] is None
