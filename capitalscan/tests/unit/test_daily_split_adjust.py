"""Daily split back-adjustment: `daily_split_state` and `back_adjust_daily`.

**Why this exists.** `run_bars_daily` assumed the vendor returns
split-adjusted history. On 2026-08-27 that assumption failed in both
directions in the same week, and nothing raised:

    IESC  2-for-1 on 2026-08-24   vendor never adjusted; our copy mirrored it
    AVB   2.793 on 2026-08-17     vendor adjusted; our stored copy was stale

Every number below is the real one, read out of `bars` and
`corporate_actions`, so a future change that breaks the detection breaks
against the case that motivated it rather than against a toy.

No database and no network: both functions are pure frame arithmetic.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from capitalscan.jobs.ingest import (
    MIN_DETECTABLE_SPLIT_DISTANCE,
    back_adjust_daily,
    daily_split_state,
)


class TestDetection:
    def test_iesc_is_detected_as_unadjusted(self):
        """685.04 into 324.12 across a 2-for-1: jump 2.11, ratio 2."""
        assert daily_split_state(685.04, 324.12, 2.0) == "unadjusted"

    def test_avb_stored_history_is_detected_as_unadjusted(self):
        """184.06 into 65.9005 across 2.793. The jump equals the ratio
        almost exactly, which is what an unadjusted series looks like."""
        assert daily_split_state(184.06, 65.9005, 2.793) == "unadjusted"

    def test_a_correctly_adjusted_series_is_detected_as_adjusted(self):
        """MNST's real 2-for-1 on 2026-08-11: 45.7150 into 45.5300, a jump
        of 1.004. Yahoo had adjusted it and we stored it correctly."""
        assert daily_split_state(45.7150, 45.5300, 2.0) == "adjusted"

    def test_klac_ten_for_one_adjusted(self):
        assert daily_split_state(241.1640, 254.5400, 10.0) == "adjusted"

    def test_amcr_reverse_split_adjusted(self):
        """A 1-for-5 reverse split, ratio 0.2. The ratio being below 1 must
        not confuse the comparison."""
        assert daily_split_state(44.1000, 44.1600, 0.2) == "adjusted"

    def test_a_reverse_split_left_unadjusted_is_detected(self):
        """Constructed: ratio 0.2 means pre-split prices are 1/5 of post."""
        assert daily_split_state(8.82, 44.16, 0.2) == "unadjusted"


class TestUnresolved:
    """The blind spot, stated rather than hidden.

    A first pass at this detection used `ratio * 0.85 .. ratio * 1.15` and
    reported SCCO, HON, SPGI, CMCSA, FNF, UL and CBSH as unadjusted. All
    seven were false: when the ratio is near 1.0 that band brackets 1.0, so
    a correctly adjusted series matches the unadjusted hypothesis. CLAUDE.md
    records the same trap biting `_split_adjustment_factor`.
    """

    def test_scco_near_unity_ratio_is_unresolved_not_unadjusted(self):
        assert daily_split_state(199.06, 194.48, 1.012) == "unresolved"

    def test_spgi_near_unity_ratio_is_unresolved(self):
        assert daily_split_state(385.2980, 414.9700, 1.057) == "unresolved"

    def test_hon_near_unity_reverse_is_unresolved(self):
        assert daily_split_state(243.5343, 227.8000, 0.9535) == "unresolved"

    def test_the_boundary_is_the_named_constant(self):
        just_under = 1.0 + MIN_DETECTABLE_SPLIT_DISTANCE - 0.001
        just_over = 1.0 + MIN_DETECTABLE_SPLIT_DISTANCE + 0.001
        assert daily_split_state(100.0, 100.0 / just_under, just_under) == "unresolved"
        assert daily_split_state(100.0, 100.0 / just_over, just_over) == "unadjusted"

    def test_an_ambiguous_jump_matching_neither_hypothesis_is_unresolved(self):
        """Halfway between adjusted and unadjusted is not a coin flip to
        call: a real 30% drawdown around a 2-for-1 would land here."""
        assert daily_split_state(150.0, 100.0, 2.0) == "unresolved"

    def test_degenerate_inputs_are_unresolved_and_never_raise(self):
        assert daily_split_state(0.0, 100.0, 2.0) == "unresolved"
        assert daily_split_state(100.0, 0.0, 2.0) == "unresolved"
        assert daily_split_state(None, 100.0, 2.0) == "unresolved"
        assert daily_split_state(100.0, 100.0, None) == "unresolved"
        assert daily_split_state(100.0, 100.0, 0.0) == "unresolved"


def _frame(rows):
    return pd.DataFrame(rows)


def _bar(ts, close, volume=1000):
    return {
        "ticker": "IESC",
        "ts": pd.Timestamp(ts),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": close,
        "volume": volume,
    }


class TestAdjustment:
    def test_prices_before_the_ex_date_are_divided(self):
        f = _frame([_bar("2026-08-21", 685.04), _bar("2026-08-25", 313.23)])
        out = back_adjust_daily(f, date(2026, 8, 24), 2.0)
        assert out.loc[0, "close"] == 342.52
        assert out.loc[1, "close"] == 313.23, "on or after the ex-date is untouched"

    def test_the_ex_date_bar_itself_is_untouched(self):
        """It already trades on the new share count. Strictly `<`."""
        f = _frame([_bar("2026-08-24", 300.0)])
        assert back_adjust_daily(f, date(2026, 8, 24), 2.0).loc[0, "close"] == 300.0

    def test_volume_is_multiplied_not_divided(self):
        """A 2-for-1 doubles the share count, so pre-split volume in old
        shares is twice as many new ones. Inverting this leaves prices
        right and every volume figure wrong by ratio squared."""
        f = _frame([_bar("2026-08-21", 685.04, volume=115500)])
        assert back_adjust_daily(f, date(2026, 8, 24), 2.0).loc[0, "volume"] == 231000

    def test_every_price_column_moves_together(self):
        f = _frame([_bar("2026-08-21", 600.0)])
        out = back_adjust_daily(f, date(2026, 8, 24), 2.0)
        for col in ("open", "high", "low", "close", "adj_close"):
            assert out.loc[0, col] == 300.0

    def test_it_does_not_mutate_its_input(self):
        f = _frame([_bar("2026-08-21", 685.04)])
        back_adjust_daily(f, date(2026, 8, 24), 2.0)
        assert f.loc[0, "close"] == 685.04

    def test_a_reverse_split_raises_pre_split_prices(self):
        """ratio 0.2: dividing by 0.2 multiplies by five."""
        f = _frame([_bar("2026-01-14", 8.82, volume=1000)])
        out = back_adjust_daily(f, date(2026, 1, 15), 0.2)
        assert out.loc[0, "close"] == 44.1
        assert out.loc[0, "volume"] == 200

    def test_an_empty_frame_is_returned_unchanged(self):
        assert back_adjust_daily(pd.DataFrame(), date(2026, 8, 24), 2.0).empty

    def test_nothing_before_the_ex_date_is_a_no_op(self):
        f = _frame([_bar("2026-08-25", 313.23)])
        assert back_adjust_daily(f, date(2026, 8, 24), 2.0).loc[0, "close"] == 313.23

    def test_applying_it_makes_the_series_read_as_adjusted(self):
        """The round trip the whole thing is for: detect unadjusted, apply,
        and the same detector must now say adjusted."""
        assert daily_split_state(685.04, 324.12, 2.0) == "unadjusted"
        f = _frame([_bar("2026-08-21", 685.04), _bar("2026-08-25", 324.12)])
        out = back_adjust_daily(f, date(2026, 8, 24), 2.0)
        assert daily_split_state(out.loc[0, "close"], out.loc[1, "close"], 2.0) == "adjusted"
