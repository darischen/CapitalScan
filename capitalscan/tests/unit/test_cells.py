"""Session 12.1 and 12.2: `cell_key` parity and the headline grid.

Every expectation in the `cell_key` tests below was **read out of Postgres
first**, not derived by reasoning about what `concat_ws` and `to_char`
ought to do. `tests/integration/test_cell_key_sql.py` re-checks the same
function live across the full parameter cross-product; this tier pins the
answers so a unit run catches a divergence without a database.

The 2026-08-09 peak-label defect is the reason for that ordering: its test
transcribed the statement under test and inherited its bug.
"""

from __future__ import annotations

import pytest

from capitalscan.core.cells import (
    CellSpec,
    cell_id_for,
    cell_key,
    dd_bucket_labels,
    era_labels,
    headline_grid,
    holdout_era,
    reported_eras,
    suppression_reason,
)
from capitalscan.core.config import SplitParams, StatsParams


class TestCellKeyString:
    """Oracle strings, read from Postgres 18 on 2026-08-11."""

    def test_fully_specified_cell(self):
        assert (
            cell_key(
                "stoch_oversold", "long", "10-20", 1, "at_touch", "validate", "2010-2014", 10, 0.03
            )
            == "stoch_oversold|long|10-20|1|at_touch|validate|2010-2014|h10|t0.03"
        )

    def test_null_dd_bucket_coalesces_to_all(self):
        key = cell_key(
            "confluence_high", "short", None, 3, "next_open", "train", "2015-2019", 5, 0.10
        )
        assert key == "confluence_high|short|all|3|next_open|train|2015-2019|h5|t0.1"

    def test_null_strength_coalesces_to_all(self):
        key = cell_key(
            "bb_lower_touch", "long", "0-10", None, "next_open", "train", "2020-2023", 5, 0.02
        )
        assert key.split("|")[3] == "all"

    def test_null_era_coalesces_to_pooled(self):
        key = cell_key("bb_lower_touch", "long", "0-10", None, "next_open", "train", None, 5, 0.02)
        assert key == "bb_lower_touch|long|0-10|all|next_open|train|pooled|h5|t0.02"

    def test_session_12_call_shape_is_null_strength_and_null_era(self):
        """ADR 102 removed `signal_strength` as a dimension; every headline
        cell is pooled across eras (ADR 103). Both slots therefore coalesce
        on every Session 12 call, which is the shape most likely to rot
        unnoticed."""
        key = cell_key("confluence_low", "long", "10-20", None, "next_open", "train", None, 5, 0.05)
        parts = key.split("|")
        assert parts[3] == "all"
        assert parts[6] == "pooled"


class TestTargetFormatting:
    """`to_char(p_target, 'FM990.999')`, verified against Postgres."""

    @pytest.mark.parametrize(
        "target,expected",
        [(0.02, "t0.02"), (0.03, "t0.03"), (0.05, "t0.05"), (0.10, "t0.1")],
    )
    def test_reach_targets(self, target, expected):
        key = cell_key(
            "bb_lower_touch", "long", "0-10", None, "next_open", "train", None, 5, target
        )
        assert key.rsplit("|", 1)[1] == expected

    def test_every_stats_params_reach_target_is_covered(self):
        """Guards the parametrization above against a `StatsParams` edit."""
        assert StatsParams().reach_targets == (0.02, 0.03, 0.05, 0.10)

    def test_trailing_zeros_are_stripped_but_the_point_survives(self):
        """`FM` suppresses padding zeros; the pattern's `.` is emitted
        regardless. `1.0` renders `1.`, not `1` and not `1.000`."""
        assert cell_key("s", "long", "0-10", None, "e", "train", None, 5, 1.0).endswith("|t1.")
        assert cell_key("s", "long", "0-10", None, "e", "train", None, 5, 0.0).endswith("|t0.")

    def test_rounding_is_half_up_not_bankers(self):
        """Postgres `to_char` rounds half away from zero. Python's built-in
        `round` is half-to-even and returns `0.123` here, which is exactly
        the divergence this test exists to catch."""
        assert cell_key("s", "long", "0-10", None, "e", "train", None, 5, 0.1235).endswith(
            "|t0.124"
        )
        assert cell_key("s", "long", "0-10", None, "e", "train", None, 5, 0.12345).endswith(
            "|t0.123"
        )
        assert cell_key("s", "long", "0-10", None, "e", "train", None, 5, 0.0005).endswith(
            "|t0.001"
        )

    def test_negative_target_keeps_its_sign(self):
        assert cell_key("s", "long", "0-10", None, "e", "train", None, 5, -0.05).endswith("|t-0.05")


class TestConcatWsDropsRatherThanCoalesces:
    """The four documented coalescing paths are not the only null paths.

    `concat_ws` **omits** a null argument instead of emitting an empty
    field, so a null in any non-coalesced slot yields a *shorter* key. That
    is a collision hazard, not a formatting quirk, and Python has to
    reproduce it exactly or parity fails on the one input nobody tries.
    """

    def test_null_signal_type_drops_the_field(self):
        key = cell_key(None, "long", "0-10", None, "next_open", "train", None, 5, 0.02)
        assert key == "long|0-10|all|next_open|train|pooled|h5|t0.02"
        assert key.count("|") == 7

    def test_null_horizon_drops_the_field(self):
        key = cell_key(
            "bb_lower_touch", "long", "0-10", None, "next_open", "train", None, None, 0.02
        )
        assert key == "bb_lower_touch|long|0-10|all|next_open|train|pooled|t0.02"

    def test_null_target_drops_the_field(self):
        key = cell_key("bb_lower_touch", "long", "0-10", None, "next_open", "train", None, 5, None)
        assert key == "bb_lower_touch|long|0-10|all|next_open|train|pooled|h5"


class TestDdBucketLabels:
    def test_labels_derive_from_stats_params_thresholds(self):
        assert dd_bucket_labels(StatsParams()) == ("0-10", "10-20", "20-35", "35+")

    def test_labels_follow_a_thresholds_edit(self):
        sp = StatsParams(dd_buckets=(0.15, 0.30))
        assert dd_bucket_labels(sp) == ("0-15", "15-30", "30+")


class TestHeadlineGrid:
    """ADR 102: `2 sides x 3 signal families x 2 buckets = 12`."""

    def test_exactly_twelve_cells(self):
        assert len(headline_grid(StatsParams())) == 12

    def test_matches_the_adr_102_table(self):
        expected = {
            ("long", "bb_lower_touch", "0-10"),
            ("long", "bb_lower_touch", "10-20"),
            ("long", "stoch_oversold", "0-10"),
            ("long", "stoch_oversold", "10-20"),
            ("long", "confluence_low", "0-10"),
            ("long", "confluence_low", "10-20"),
            ("short", "bb_upper_touch", "0-10"),
            ("short", "bb_upper_touch", "10-20"),
            ("short", "stoch_overbought", "0-10"),
            ("short", "stoch_overbought", "10-20"),
            ("short", "confluence_high", "0-10"),
            ("short", "confluence_high", "10-20"),
        }
        assert {
            (c.side, c.signal_type, c.dd_bucket) for c in headline_grid(StatsParams())
        } == expected

    def test_signal_type_pairs_with_side_rather_than_crossing_it(self):
        """Twelve cells, not thirty-six. A long cell reads `bb_lower_touch`
        and its short counterpart reads `bb_upper_touch`; no cell pairs a
        long side with an upper-band touch."""
        grid = headline_grid(StatsParams())
        assert all(
            c.signal_type.endswith(("_lower_touch", "_oversold", "_low"))
            for c in grid
            if c.side == "long"
        )
        assert all(
            c.signal_type.endswith(("_upper_touch", "_overbought", "_high"))
            for c in grid
            if c.side == "short"
        )

    def test_deep_drawdown_buckets_are_excluded(self):
        """ADR 101: `20-35` and `35+` are measured and permanently
        suppressed. They never enter the grid."""
        buckets = {c.dd_bucket for c in headline_grid(StatsParams())}
        assert buckets == {"0-10", "10-20"}

    def test_grid_follows_a_stats_params_bucket_edit(self):
        """No literals: the two headline buckets are the first two labels
        `StatsParams.dd_buckets` produces."""
        grid = headline_grid(StatsParams(dd_buckets=(0.15, 0.30)))
        assert {c.dd_bucket for c in grid} == {"0-15", "15-30"}

    def test_enumeration_order_is_stable(self):
        assert headline_grid(StatsParams()) == headline_grid(StatsParams())

    def test_cell_ids_are_distinct(self):
        ids = {
            cell_id_for(
                c, entry_kind="next_open", split_key="train", horizon_days=5, target_pct=0.02
            )
            for c in headline_grid(StatsParams())
        }
        assert len(ids) == 12

    def test_cell_id_passes_null_strength_and_null_era(self):
        spec = CellSpec(side="long", signal_type="bb_lower_touch", dd_bucket="0-10")
        cid = cell_id_for(
            spec, entry_kind="next_open", split_key="train", horizon_days=5, target_pct=0.02
        )
        assert cid == "bb_lower_touch|long|0-10|all|next_open|train|pooled|h5|t0.02"

    def test_cell_id_accepts_an_era_for_the_descriptive_rows(self):
        """12.4's era rows reuse the same key builder with `era` supplied."""
        spec = CellSpec(side="long", signal_type="bb_lower_touch", dd_bucket="0-10")
        cid = cell_id_for(
            spec,
            entry_kind="next_open",
            split_key="train",
            horizon_days=5,
            target_pct=0.02,
            era="2015-2019",
        )
        assert cid.split("|")[6] == "2015-2019"


class TestEraLabels:
    """ADR 103. Era 2024+ **is** the holdout split — both begin
    2024-01-01 — so reporting a headline cell for it reports on holdout."""

    def test_labels_match_the_event_pipeline(self):
        """One implementation. `research.enrich._era` stamps
        `events.era`; a label built differently here would silently match
        nothing."""
        assert era_labels(StatsParams(), SplitParams()) == (
            "2010-2014",
            "2015-2019",
            "2020-2023",
            "2024+",
        )

    def test_reported_eras_exclude_the_open_era(self):
        assert reported_eras(StatsParams(), SplitParams()) == (
            "2010-2014",
            "2015-2019",
            "2020-2023",
        )

    def test_the_open_era_begins_exactly_where_holdout_begins(self):
        """The load-bearing relationship, and the one that can drift.

        `reported_eras` drops the *last* era on the reasoning that it
        coincides with holdout. That reasoning depends on
        `SplitParams.validate_end` equalling the final `era_bounds` entry.
        Move one without the other and the open era stops being the
        holdout era, at which point dropping it is wrong and keeping it is
        a firewall breach.
        """
        sp, splits = StatsParams(), SplitParams()
        assert splits.validate_end == sp.era_bounds[-1]

    def test_labels_follow_an_era_bounds_edit(self):
        sp = StatsParams(era_bounds=("2014-12-31", "2019-12-31"))
        assert era_labels(sp, SplitParams()) == ("2010-2014", "2015-2019", "2020+")
        assert reported_eras(sp, SplitParams()) == ("2010-2014", "2015-2019")

    def test_labels_follow_an_event_start_edit(self):
        splits = SplitParams(event_start="2012-01-01")
        assert era_labels(StatsParams(), splits)[0] == "2012-2014"

    def test_holdout_era_is_named_separately(self):
        assert holdout_era(StatsParams(), SplitParams()) == "2024+"
        assert holdout_era(StatsParams(), SplitParams()) not in reported_eras(
            StatsParams(), SplitParams()
        )


class TestSuppression:
    def test_below_the_floor_returns_a_reason(self):
        reason = suppression_reason(14.0, StatsParams())
        assert reason is not None
        assert "14" in reason and "30" in reason

    def test_at_the_floor_renders(self):
        assert suppression_reason(30.0, StatsParams()) is None

    def test_above_the_floor_renders(self):
        assert suppression_reason(717.0, StatsParams()) is None

    def test_floor_comes_from_stats_params(self):
        """Lowering `min_n_eff` to rescue a cell is the named temptation in
        the session brief. This test does not prevent it, but it makes the
        floor's source unambiguous: a literal 30 here would let the two
        disagree."""
        assert suppression_reason(20.0, StatsParams(min_n_eff=15)) is None
        assert suppression_reason(20.0, StatsParams(min_n_eff=25)) is not None
