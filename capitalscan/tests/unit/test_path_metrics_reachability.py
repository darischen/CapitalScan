"""`path_metrics` reachability: pinned before it is vectorised.

**Why.** Profiled 2026-08-28 during arm 3: `path_metrics` is **41.2s of a
110s** single-ticker compute, called once per event, and underneath it the
process built **193,469 pandas Series for 7,428 events** — about 26 each.
The cause is a nested loop that re-scans the forward window once per
`reach_targets` entry (four of them) with `iterrows()`, materialising a
Series per bar to read **one column** and make **one comparison**:

    for target in targets:
        for day_number, (_, bar) in enumerate(fwd_bars.iterrows(), start=1):
            if _breach(float(bar[price_col]), level, bound):

That is the same defect `scan_candidates` had, where removing per-row Series
work gave 11.25x.

**These tests characterise the behaviour that must not move.** They are
written against the loop implementation and must pass unchanged against the
vectorised one — that is the whole point of writing them first.

Three details a naive vectorisation gets wrong:

1. **Both sides round to 4 decimals** before comparing (`core.signals._breach`,
   DESIGN §3.2): `round(price, 4) >= round(level, 4)`. Comparing raw floats
   differs on exactly the boundary cases the rounding exists to settle.
2. **`day_number` is 1-based**, and feeds `day_touched_*pct`. An off-by-one
   is silent and wrong.
3. **First breach wins.** The loop breaks; a vectorised version must take
   the first index, not the last or the extreme.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import ExitParams, StatsParams
from capitalscan.core.types import Side
from capitalscan.research.enrich import path_metrics

SP = StatsParams()
EP = ExitParams()
TARGETS = SP.reach_targets
HORIZONS = SP.fwd_ret_horizons


def _bars(highs, lows=None):
    n = len(highs)
    lows = lows if lows is not None else [h - 1 for h in highs]
    return pd.DataFrame(
        {
            "open": highs,
            "high": highs,
            "low": lows,
            "close": highs,
            "ts": pd.date_range("2026-01-02", periods=n, freq="D"),
        }
    )


def _run(bars, side=Side.LONG, entry=100.0, exit_idx=None, exit_price=100.0):
    return path_metrics(
        entry_price=entry,
        side=side,
        fwd_bars=bars,
        exit_idx=len(bars) - 1 if exit_idx is None else exit_idx,
        exit_price=exit_price,
        targets=TARGETS,
        adj_close_fwd=None,
        horizons=HORIZONS,
        capture_ratio_cap=EP.capture_ratio_cap,
    )


class TestTheDayNumberIsOneBased:
    def test_a_breach_on_the_first_bar_is_day_one(self):
        """The loop starts `enumerate(..., start=1)`. Day 0 would be wrong
        and silently shift every `day_touched_*` column."""
        out = _run(_bars([103.0, 100.0, 100.0]))
        assert out["touched_3pct"] is True or out["touched_3pct"] == 1
        assert out["day_touched_3pct"] == 1

    def test_a_breach_on_the_third_bar_is_day_three(self):
        out = _run(_bars([100.0, 100.0, 103.0]))
        assert out["day_touched_3pct"] == 3

    def test_no_breach_leaves_the_day_unset(self):
        out = _run(_bars([100.0, 100.5, 101.0]))
        assert not out["touched_3pct"]
        assert out["day_touched_3pct"] is None or pd.isna(out["day_touched_3pct"])


class TestFirstBreachWins:
    def test_the_earliest_bar_is_reported_not_the_highest(self):
        """The loop breaks on first breach. A vectorised version taking the
        max, or the last index, would silently change the answer."""
        out = _run(_bars([103.0, 110.0, 120.0]))
        assert out["day_touched_3pct"] == 1


class TestRoundingAtFourDecimals:
    """`_breach` rounds price and level to 4 dp before comparing (DESIGN
    §3.2), so a float artifact below a hundredth of a cent never decides an
    event. These are the cases a raw float comparison gets wrong."""

    def test_a_price_a_hair_under_the_level_still_breaches_after_rounding(self):
        # target 3% of 100 = 103.0 exactly; 102.99996 rounds to 103.0
        out = _run(_bars([102.99996]))
        assert out["touched_3pct"], "rounding to 4dp must admit this"

    def test_a_price_clearly_under_does_not_breach(self):
        out = _run(_bars([102.9]))
        assert not out["touched_3pct"]


class TestSideDirection:
    def test_long_looks_at_high_and_reaches_upward(self):
        out = _run(_bars([105.0]), side=Side.LONG)
        assert out["touched_3pct"]

    def test_short_looks_at_low_and_reaches_downward(self):
        """SHORT uses `low` and Bound.LOWER: the level is entry*(1-target)."""
        out = _run(_bars([100.0], lows=[96.0]), side=Side.SHORT, exit_price=96.0)
        assert out["touched_3pct"]

    def test_short_is_not_triggered_by_an_upward_move(self):
        out = _run(_bars([120.0], lows=[119.0]), side=Side.SHORT, exit_price=119.0)
        assert not out["touched_3pct"]


class TestEveryTargetIsEvaluated:
    def test_all_four_reach_targets_produce_columns(self):
        """`reach_targets` is (0.02, 0.03, 0.05, 0.10) — four full scans of
        the window per event in the loop version."""
        out = _run(_bars([104.0]))
        for t in TARGETS:
            suffix = f"{int(t * 100)}pct" if (t * 100) % 1 == 0 else None
            if suffix:
                assert f"touched_{suffix}" in out, f"missing touched_{suffix}"

    def test_a_partial_move_trips_only_the_nearer_targets(self):
        out = _run(_bars([104.0]))
        assert out["touched_2pct"] and out["touched_3pct"]
        assert not out["touched_5pct"] and not out["touched_10pct"]


class TestNaNAndEmpty:
    @pytest.mark.xfail(
        reason=(
            "The loop version RAISES here, and the vectorised one will not. "
            "`iterrows()` on a frame mixing float and datetime columns yields "
            "an object Series in which NaN is coerced to NaT, so "
            "`float(bar['high'])` raises TypeError: float() argument must be "
            "a string or a real number, not 'NaTType'. `_breach`'s own NaN "
            "guard never gets a chance to run. Vectorising removes the "
            "coercion -- np.round(nan) is nan and every comparison against it "
            "is False -- which is the semantic `_breach` already intends. "
            "Marked xfail rather than asserting the crash, so this test turns "
            "green on the fix and records it as a deliberate behaviour change "
            "rather than an accident. Probably unreachable today: "
            "`validate_bars` rejects null OHLC to `bar_rejects`."
        ),
        strict=True,
    )
    def test_a_nan_price_never_breaches(self):
        out = _run(_bars([np.nan, 100.0]))
        assert not out["touched_3pct"]

    def test_an_empty_window_yields_no_touches(self):
        out = path_metrics(
            entry_price=100.0,
            side=Side.LONG,
            fwd_bars=_bars([]).iloc[0:0],
            exit_idx=None,
            exit_price=100.0,
            targets=TARGETS,
            adj_close_fwd=None,
            horizons=HORIZONS,
            capture_ratio_cap=EP.capture_ratio_cap,
        )
        assert not out["touched_3pct"]
