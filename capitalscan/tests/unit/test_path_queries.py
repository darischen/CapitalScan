from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from capitalscan.core.config import DEFAULT_CONFIG
from capitalscan.research.path_queries import (
    Direction,
    first_touch_day,
    grid_column_names,
    reach_grid,
    terminal_at,
    touched_by,
)


def _path(rows: list[tuple[int, float, float, float]]) -> pd.DataFrame:
    """(day_offset, favorable, adverse, terminal) tuples -> a path frame."""
    return pd.DataFrame(rows, columns=["day_offset", "favorable", "adverse", "terminal"])


# A hand-checkable event: favorable crosses 2% on day 2, 5% on day 4;
# adverse crosses -3% on day 3. Eleven days, so every horizon is covered.
HAND_PATH = _path(
    [
        (1, 0.010000, -0.005000, 0.008000),
        (2, 0.024000, -0.010000, 0.020000),
        (3, 0.031000, -0.032000, -0.030000),
        (4, 0.058000, -0.012000, 0.050000),
        (5, 0.061000, -0.008000, 0.055000),
        (6, 0.062000, -0.007000, 0.056000),
        (7, 0.063000, -0.006000, 0.057000),
        (8, 0.064000, -0.005000, 0.058000),
        (9, 0.065000, -0.004000, 0.059000),
        (10, 0.066000, -0.003000, 0.060000),
        (11, 0.067000, -0.002000, 0.061000),
    ]
)


def test_touched_by_within_the_horizon():
    assert touched_by(HAND_PATH, 0.02, 2, Direction.FAVORABLE, entry_offset=0) is True


def test_touched_by_false_when_the_horizon_ends_before_the_touch():
    # 5% first appears on day 4; a 3-day horizon must not see it.
    assert touched_by(HAND_PATH, 0.05, 3, Direction.FAVORABLE, entry_offset=0) is False


def test_first_touch_day_returns_the_earliest_breach():
    assert first_touch_day(HAND_PATH, 0.02, 5, Direction.FAVORABLE, entry_offset=0) == 2
    assert first_touch_day(HAND_PATH, 0.05, 5, Direction.FAVORABLE, entry_offset=0) == 4


def test_first_touch_day_is_null_when_untouched_never_zero():
    # §10.5: "Null means untouched, never zero."
    assert first_touch_day(HAND_PATH, 0.10, 5, Direction.FAVORABLE, entry_offset=0) is None


def test_adverse_direction_tests_the_other_tail():
    # adverse hits -3.2% on day 3; the 3% threshold stays a positive magnitude.
    assert touched_by(HAND_PATH, 0.03, 5, Direction.ADVERSE, entry_offset=0) is True
    assert first_touch_day(HAND_PATH, 0.03, 5, Direction.ADVERSE, entry_offset=0) == 3
    assert touched_by(HAND_PATH, 0.03, 2, Direction.ADVERSE, entry_offset=0) is False


def test_adverse_and_favorable_are_independent_at_the_same_threshold():
    # Day 3 breaches 3% on both tails, but at different days on each —
    # favorable first hits 3% on day 3 too, adverse on day 3. A single
    # shared code path that ignored `direction` would still pass a
    # favorable-only test, so pin both.
    assert first_touch_day(HAND_PATH, 0.03, 5, Direction.FAVORABLE, entry_offset=0) == 3
    assert first_touch_day(HAND_PATH, 0.06, 11, Direction.ADVERSE, entry_offset=0) is None


def test_next_open_entry_offset_shifts_the_whole_window():
    # entry_offset=1: the 2-day horizon covers day_offset 2 and 3, so the
    # day-2 touch of 2% counts as first_touch=1, not 2.
    assert first_touch_day(HAND_PATH, 0.02, 2, Direction.FAVORABLE, entry_offset=1) == 1
    # 5% lands on day_offset 4, which is entry-day 3 — outside a 2-day horizon.
    assert touched_by(HAND_PATH, 0.05, 2, Direction.FAVORABLE, entry_offset=1) is False


def test_partial_window_returns_none_not_false():
    # Three observed days, asked about a 5-day horizon, no touch inside
    # what exists: the honest answer is "unknown", never False.
    short = _path([(1, 0.005, -0.002, 0.004), (2, 0.006, -0.003, 0.005), (3, 0.007, -0.004, 0.006)])
    assert touched_by(short, 0.02, 5, Direction.FAVORABLE, entry_offset=0) is None
    assert first_touch_day(short, 0.02, 5, Direction.FAVORABLE, entry_offset=0) is None


def test_partial_window_still_reports_a_touch_it_did_observe():
    # A missing later day cannot un-touch a threshold already crossed.
    short = _path([(1, 0.005, -0.002, 0.004), (2, 0.031, -0.003, 0.030)])
    assert touched_by(short, 0.03, 5, Direction.FAVORABLE, entry_offset=0) is True
    assert first_touch_day(short, 0.03, 5, Direction.FAVORABLE, entry_offset=0) == 2


def test_empty_path_is_unknown_at_every_horizon():
    empty = _path([])
    assert touched_by(empty, 0.02, 5, Direction.FAVORABLE, entry_offset=0) is None
    assert first_touch_day(empty, 0.02, 5, Direction.FAVORABLE, entry_offset=0) is None


def test_exact_target_counts_as_touched():
    # Boundary at the stored numeric(12,6) precision: >= not >.
    exact = _path([(1, 0.020000, -0.020000, 0.019000)])
    assert touched_by(exact, 0.02, 1, Direction.FAVORABLE, entry_offset=0) is True
    assert touched_by(exact, 0.02, 1, Direction.ADVERSE, entry_offset=0) is True


def test_no_breach_price_rounding_on_a_ratio():
    # The Session 9 bug this module must not reintroduce: 0.019978 is
    # 0.000022 below the 2% target, and _breach's 4-decimal price rounding
    # would call it touched.
    near = _path([(1, 0.019978, -0.001, 0.019)])
    assert touched_by(near, 0.02, 1, Direction.FAVORABLE, entry_offset=0) is False


def test_terminal_at_reads_the_mark_at_entry_plus_horizon():
    assert terminal_at(HAND_PATH, 3, entry_offset=0) == pytest.approx(-0.030)
    assert terminal_at(HAND_PATH, 3, entry_offset=1) == pytest.approx(0.050)


def test_terminal_at_is_none_past_the_window():
    assert terminal_at(HAND_PATH, 10, entry_offset=1) == pytest.approx(0.061)
    assert terminal_at(HAND_PATH, 11, entry_offset=1) is None


def test_grid_covers_thresholds_horizons_and_both_directions():
    config = DEFAULT_CONFIG
    grid = reach_grid(HAND_PATH, entry_offset=0, config=config)
    expected = 2 * len(config.stats.reach_targets) * len(config.stats.fwd_ret_horizons) * 2
    assert len(grid) == expected
    assert set(grid) == set(grid_column_names(config))
    assert grid["touched_2pct_by_2d"] is True
    assert grid["first_touch_2pct_by_2d"] == 2
    assert grid["adverse_touched_3pct_by_5d"] is True
    assert grid["adverse_first_touch_3pct_by_5d"] == 3


def test_grid_column_names_use_pct_suffix_for_the_10pct_float_trap():
    # 0.10 * 100 == 10.000000000000002; a naive f-string would emit
    # `touched_10.000000000000002pct_by_5d`.
    names = grid_column_names(DEFAULT_CONFIG)
    assert "touched_10pct_by_5d" in names
    assert not any("10.0" in name for name in names)


def test_adding_a_threshold_widens_the_grid_with_no_code_change():
    # §4 gate item 4: a config edit and a re-run, nothing else.
    base = DEFAULT_CONFIG
    stats = dataclasses.replace(base.stats, reach_targets=base.stats.reach_targets + (0.07,))
    widened = dataclasses.replace(base, stats=stats)

    before = reach_grid(HAND_PATH, entry_offset=0, config=base)
    after = reach_grid(HAND_PATH, entry_offset=0, config=widened)

    added = set(after) - set(before)
    assert added == {
        f"{prefix}{kind}_7pct_by_{h}d"
        for prefix in ("", "adverse_")
        for kind in ("touched", "first_touch")
        for h in base.stats.fwd_ret_horizons
    }
    # And the new cells carry real values, not placeholders: 7% is never
    # reached on the favorable tail of HAND_PATH (max 0.067).
    assert after["touched_7pct_by_10d"] is False
    assert after["first_touch_7pct_by_10d"] is None
    # Every pre-existing cell is untouched by the widening.
    assert all(
        after[col] == before[col] or (after[col] is None and before[col] is None) for col in before
    )
