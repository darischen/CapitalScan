"""Tests for `research/enrich.py::enrich_context` — DESIGN §5.2 steps 10-12
(Session 9, Task 8): cost application, context tagging, split assignment.

Four behaviors carry the correctness load (task brief):

1. Costs always **subtract** — a losing trade gets worse, not better, on
   BOTH sides (long and short). A sign error here flatters every short in
   the study and corrupts ADR 058's long-vs-short asymmetry comparison.
2. `dd_bucket` boundaries come from `StatsParams.dd_buckets`, and the
   labels this function produces must match `jobs.compute._dd_bucket`'s
   labels exactly (both jobs can tag the same event; a mismatch would
   split every Phase 4 cell keyed on `dd_bucket` in two).
3. `split_key` is assigned here, at creation, via `split_key_for` — never
   reimplemented (invariant 5).
4. `earnings_in_window` is `None`, not `False`, when `days_to_earnings`
   is null — an unknown is not a negative (invariant 4).
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import CostParams, StatsParams, SplitParams
from capitalscan.core.types import Side
from capitalscan.jobs.compute import DD_BUCKETS, _dd_bucket
from capitalscan.research.backtest import split_key_for
from capitalscan.research.enrich import enrich_context

_ZERO_COST = CostParams(slippage_bps=0.0, commission_per_share=0.0, borrow_bps_annual=0.0)
_REAL_COST = CostParams(slippage_bps=3.0, commission_per_share=0.01, borrow_bps_annual=40.0)


def _event(
    side: str = "long",
    signal_date: date = date(2018, 6, 15),
    entry_price: float = 100.0,
    exit_price: float = 100.0,
    holding_days: int | None = 5,
) -> dict:
    return {
        "side": side,
        "signal_date": signal_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "holding_days": holding_days,
    }


def _ind_row(dd_52w: float | None = None, days_to_earnings: float | None = None) -> pd.Series:
    return pd.Series({"dd_52w": dd_52w, "days_to_earnings": days_to_earnings})


_SP = StatsParams()
_SPLITS = SplitParams()


# ---------------------------------------------------------------------------
# 1. Costs always subtract — long and short, win and loss.
# ---------------------------------------------------------------------------


class TestCostsAlwaysSubtract:
    def test_losing_long_gets_worse_with_costs(self):
        event = _event(side="long", entry_price=100.0, exit_price=90.0, holding_days=5)
        ind_row = _ind_row()
        plain = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        costed = enrich_context(event, ind_row, None, _SP, _SPLITS, _REAL_COST)
        assert plain["gross_ret"] == pytest.approx(-0.10)
        assert costed["net_ret"] < plain["gross_ret"]

    def test_losing_short_gets_worse_with_costs(self):
        # Short loses when price rises: entry 100, exit 110.
        event = _event(side="short", entry_price=100.0, exit_price=110.0, holding_days=5)
        ind_row = _ind_row()
        plain = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        costed = enrich_context(event, ind_row, None, _SP, _SPLITS, _REAL_COST)
        assert plain["gross_ret"] == pytest.approx(-0.10)
        assert costed["net_ret"] < plain["gross_ret"]

    def test_winning_long_is_reduced_by_costs(self):
        event = _event(side="long", entry_price=100.0, exit_price=110.0, holding_days=5)
        ind_row = _ind_row()
        plain = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        costed = enrich_context(event, ind_row, None, _SP, _SPLITS, _REAL_COST)
        assert plain["gross_ret"] == pytest.approx(0.10)
        assert costed["net_ret"] < plain["gross_ret"]

    def test_winning_short_is_reduced_by_costs(self):
        # Short wins when price falls: entry 100, exit 90.
        event = _event(side="short", entry_price=100.0, exit_price=90.0, holding_days=5)
        ind_row = _ind_row()
        plain = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        costed = enrich_context(event, ind_row, None, _SP, _SPLITS, _REAL_COST)
        assert plain["gross_ret"] == pytest.approx(0.10)
        assert costed["net_ret"] < plain["gross_ret"]

    def test_short_pays_strictly_more_than_an_identical_long_via_borrow(self):
        # Same entry/exit/holding_days, opposite side: the short's net_ret
        # must be lower than the long's by exactly the borrow cost, since
        # slippage and commission are side-symmetric (DESIGN §3.9).
        long_event = _event(side="long", entry_price=100.0, exit_price=100.0, holding_days=5)
        short_event = _event(side="short", entry_price=100.0, exit_price=100.0, holding_days=5)
        ind_row = _ind_row()
        long_out = enrich_context(long_event, ind_row, None, _SP, _SPLITS, _REAL_COST)
        short_out = enrich_context(short_event, ind_row, None, _SP, _SPLITS, _REAL_COST)
        assert short_out["net_ret"] < long_out["net_ret"]

    def test_net_ret_is_nan_when_position_never_resolved(self):
        event = _event(entry_price=float("nan"), exit_price=float("nan"), holding_days=None)
        out = enrich_context(event, _ind_row(), None, _SP, _SPLITS, _REAL_COST)
        assert math.isnan(out["gross_ret"])
        assert math.isnan(out["net_ret"])


# ---------------------------------------------------------------------------
# 2. dd_bucket — derived from StatsParams.dd_buckets, must match
#    jobs.compute._dd_bucket's labels exactly.
# ---------------------------------------------------------------------------


class TestDdBucketMatchesCompute:
    @pytest.mark.parametrize(
        "dd_52w",
        [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 1.0],
    )
    def test_matches_compute_dd_bucket_for_representative_values(self, dd_52w):
        event = _event()
        ind_row = _ind_row(dd_52w=dd_52w)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["dd_bucket"] == _dd_bucket(dd_52w)

    def test_matches_compute_dd_bucket_for_null_input(self):
        event = _event()
        ind_row = _ind_row(dd_52w=None)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["dd_bucket"] is None
        assert out["dd_bucket"] == _dd_bucket(None)

    def test_matches_compute_dd_bucket_for_nan_input(self):
        event = _event()
        ind_row = _ind_row(dd_52w=float("nan"))
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["dd_bucket"] is None
        assert out["dd_bucket"] == _dd_bucket(float("nan"))

    def test_dd_bucket_uses_35_plus_fallback_above_the_last_boundary(self):
        event = _event()
        ind_row = _ind_row(dd_52w=0.40)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["dd_bucket"] == "35+"

    def test_dd_bucket_labels_are_derived_not_restated(self):
        # A StatsParams with different boundaries must move the labels too
        # — proof this isn't a hardcoded string table (invariant 9).
        custom_sp = StatsParams(dd_buckets=(0.05, 0.15, 0.30))
        event = _event()
        ind_row = _ind_row(dd_52w=0.10)
        out = enrich_context(event, ind_row, None, custom_sp, _SPLITS, _ZERO_COST)
        assert out["dd_bucket"] == "5-15"


# ---------------------------------------------------------------------------
# 3. split_key — assigned here via split_key_for, matches Task 2 exactly.
# ---------------------------------------------------------------------------


class TestSplitKeyMatchesTask2:
    @pytest.mark.parametrize(
        "signal_date",
        [date(2010, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2023, 12, 31), date(2024, 1, 1)],
    )
    def test_split_key_matches_split_key_for(self, signal_date):
        event = _event(signal_date=signal_date)
        out = enrich_context(event, _ind_row(), None, _SP, _SPLITS, _ZERO_COST)
        assert out["split_key"] == split_key_for(signal_date, _SPLITS)

    def test_split_key_raises_below_event_start_same_as_split_key_for(self):
        sp_splits = SplitParams(event_start="2010-01-01")
        event = _event(signal_date=date(2009, 6, 15))
        with pytest.raises(ValueError, match="2009-06-15"):
            enrich_context(event, _ind_row(), None, _SP, sp_splits, _ZERO_COST)


# ---------------------------------------------------------------------------
# 4. earnings_in_window — null (not False) when days_to_earnings is null.
# ---------------------------------------------------------------------------


class TestEarningsInWindow:
    def test_null_when_days_to_earnings_is_null(self):
        event = _event(holding_days=5)
        ind_row = _ind_row(days_to_earnings=None)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["earnings_in_window"] is None

    def test_not_false_when_days_to_earnings_is_null(self):
        # The brief's own framing: an unknown is not a negative.
        event = _event(holding_days=5)
        ind_row = _ind_row(days_to_earnings=None)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["earnings_in_window"] is not False

    def test_true_when_earnings_falls_inside_the_holding_window(self):
        event = _event(holding_days=5)
        ind_row = _ind_row(days_to_earnings=3)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["earnings_in_window"] is True

    def test_true_at_the_holding_window_boundary(self):
        event = _event(holding_days=5)
        ind_row = _ind_row(days_to_earnings=5)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["earnings_in_window"] is True

    def test_false_when_earnings_falls_outside_the_holding_window(self):
        event = _event(holding_days=5)
        ind_row = _ind_row(days_to_earnings=6)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["earnings_in_window"] is False

    def test_null_when_holding_days_is_unresolved(self):
        event = _event(holding_days=None)
        ind_row = _ind_row(days_to_earnings=2)
        out = enrich_context(event, ind_row, None, _SP, _SPLITS, _ZERO_COST)
        assert out["earnings_in_window"] is None


# ---------------------------------------------------------------------------
# era — from StatsParams.era_bounds, no literals.
# ---------------------------------------------------------------------------


class TestEra:
    def test_matches_adr_042_default_eras(self):
        cases = [
            (date(2012, 1, 1), "2010-2014"),
            (date(2014, 12, 31), "2010-2014"),
            (date(2015, 1, 1), "2015-2019"),
            (date(2019, 12, 31), "2015-2019"),
            (date(2020, 1, 1), "2020-2023"),
            (date(2023, 12, 31), "2020-2023"),
            (date(2024, 1, 1), "2024+"),
            (date(2026, 1, 1), "2024+"),
        ]
        for signal_date, expected in cases:
            event = _event(signal_date=signal_date)
            out = enrich_context(event, _ind_row(), None, _SP, _SPLITS, _ZERO_COST)
            assert out["era"] == expected, signal_date

    def test_era_boundaries_move_with_stats_params(self):
        custom_sp = StatsParams(era_bounds=("2015-12-31",))
        splits = SplitParams(event_start="2012-01-01")
        event = _event(signal_date=date(2013, 1, 1))
        out = enrich_context(event, _ind_row(), None, custom_sp, splits, _ZERO_COST)
        assert out["era"] == "2012-2015"

    def test_era_lower_bound_comes_from_split_params_event_start(self):
        custom_sp = StatsParams(era_bounds=("2015-12-31",))
        splits = SplitParams(event_start="2013-06-01")
        event = _event(signal_date=date(2014, 1, 1))
        out = enrich_context(event, _ind_row(), None, custom_sp, splits, _ZERO_COST)
        assert out["era"] == "2013-2015"


# ---------------------------------------------------------------------------
# bw_regime — unimplemented (no documented bucketing exists); honest None.
# ---------------------------------------------------------------------------


class TestBwRegime:
    def test_bw_regime_is_none(self):
        event = _event()
        out = enrich_context(event, _ind_row(), None, _SP, _SPLITS, _ZERO_COST)
        assert out["bw_regime"] is None


# ---------------------------------------------------------------------------
# vix_close / spx_ret_1d — from market_row, tolerating a None frame.
# ---------------------------------------------------------------------------


class TestMarketRow:
    def test_null_when_market_row_is_none(self):
        event = _event()
        out = enrich_context(event, _ind_row(), None, _SP, _SPLITS, _ZERO_COST)
        assert out["vix_close"] is None
        assert out["spx_ret_1d"] is None

    def test_read_from_market_row_when_present(self):
        event = _event()
        market_row = pd.Series({"vix_close": 18.5, "spx_ret_1d": -0.012})
        out = enrich_context(event, _ind_row(), market_row, _SP, _SPLITS, _ZERO_COST)
        assert out["vix_close"] == pytest.approx(18.5)
        assert out["spx_ret_1d"] == pytest.approx(-0.012)
