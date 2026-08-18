"""Closed enums, derived rather than transcribed (ADR 074, session 15.5).

The tests that matter here are the ones comparing each set against its
*source*. A test listing the expected strings would pass forever and would
not notice ADR 108 adding an eighth signal type, which is the failure mode
ADR 074 closes the enums to prevent: a type that is detectable,
backtestable, and invisible to every tool.
"""

from __future__ import annotations

from datetime import date

import pytest

from capitalscan.core.cells import LONG_SIGNALS, SHORT_SIGNALS, dd_bucket_labels
from capitalscan.core.config import SplitParams, StatsParams
from capitalscan.core.types import EntryKind, SignalType
from capitalscan.handlers import enums
from capitalscan.handlers.errors import DateOutOfWindow, HoldoutRequested, InvalidEnum
from capitalscan.jobs.compute import DD_BUCKETS

SP = StatsParams()


# ---------------------------------------------------------------------------
# Each set equals its source of truth
# ---------------------------------------------------------------------------


def test_signal_types_are_exactly_the_signal_type_enum():
    assert enums.signal_types() == tuple(m.value for m in SignalType)


def test_entry_kinds_are_exactly_the_entry_kind_enum():
    assert enums.entry_kinds() == tuple(m.value for m in EntryKind)


def test_dd_buckets_come_from_stats_params_not_a_literal():
    assert enums.dd_buckets(SP) == dd_bucket_labels(SP)


def test_dd_buckets_move_when_the_thresholds_move():
    """The derivation is real, not a coincidence at the default values."""
    shifted = StatsParams(dd_buckets=(0.05, 0.25))
    assert enums.dd_buckets(shifted) == ("0-5", "5-25", "25+")


def test_the_bucket_labels_agree_with_the_ones_events_are_stamped_with():
    """`compute.DD_BUCKETS` assigns; `dd_bucket_labels` reads back.

    Two implementations of the same edges, and a query built on the second
    that disagreed with the first would filter out every event rather than
    fail. They share `StatsParams`, and this asserts it rather than
    assuming it.
    """
    assigned = tuple(label for _, label in DD_BUCKETS) + ("35+",)
    assert enums.dd_buckets(SP) == assigned


def test_every_signal_type_has_a_grid_side():
    """`side_for_signal_type` covers the enum, so no type is unreachable.

    ADR 108 added a short-only type and broke the positional pairing the two
    tuples used to have. This fails if a ninth type lands in neither.
    """
    for value in enums.signal_types():
        assert enums.side_for_signal_type(value) in ("long", "short")


def test_the_side_mapping_is_the_grids_own_pairing():
    for value in LONG_SIGNALS:
        assert enums.side_for_signal_type(value) == "long"
    for value in SHORT_SIGNALS:
        assert enums.side_for_signal_type(value) == "short"


def test_target_pct_is_checked_against_reach_targets():
    for target in SP.reach_targets:
        assert enums.parse_target_pct(float(target), SP) == float(target)
    with pytest.raises(InvalidEnum, match="not measured"):
        enums.parse_target_pct(0.04, SP)


# ---------------------------------------------------------------------------
# Valid, invalid, near-miss
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parse,good,bad,near_miss",
    [
        (enums.parse_split, "validate", "test", "Validate"),
        (enums.parse_universe, "trade", "everything", "TRADE"),
        (enums.parse_entry_kind, "next_open", "market_open", "Next_Open"),
        (enums.parse_signal_type, "confluence_low", "confluence_mid", "CONFLUENCE_LOW"),
        (enums.parse_dd_bucket, "0-10", "0-15", "0_10"),
    ],
)
def test_each_enum_takes_a_valid_value_and_refuses_the_others(parse, good, bad, near_miss):
    assert parse(good) == good
    with pytest.raises(InvalidEnum):
        parse(bad)
    with pytest.raises(InvalidEnum):
        parse(near_miss)


def test_a_rejection_names_the_valid_values():
    """A failure that does not say what was expected costs a round trip."""
    with pytest.raises(InvalidEnum) as exc:
        enums.parse_dd_bucket("0-15")
    assert "0-10" in str(exc.value) and "35+" in str(exc.value)


def test_case_is_not_forgiven():
    """A case-insensitive fallback would be rejected by Postgres later.

    The error would then name a check constraint instead of an argument,
    three layers from the caller who typed it.
    """
    with pytest.raises(InvalidEnum):
        enums.parse_universe("Trade")


# ---------------------------------------------------------------------------
# signal_types lists
# ---------------------------------------------------------------------------


def test_none_means_all_types():
    assert enums.parse_signal_types(None) is None


def test_an_empty_list_is_refused_rather_than_treated_as_all():
    """None and [] are different intents and only one is expressible.

    An empty list can only return zero rows, which is far more likely to be
    a bug in the caller's code than a request.
    """
    with pytest.raises(InvalidEnum, match="selects nothing"):
        enums.parse_signal_types([])


def test_a_list_with_one_bad_member_is_refused_whole():
    with pytest.raises(InvalidEnum):
        enums.parse_signal_types(["confluence_low", "not_a_type"])


# ---------------------------------------------------------------------------
# limit
# ---------------------------------------------------------------------------


def test_limit_caps_at_two_hundred():
    """ADR 074: 200 regardless of what the caller passes, and not an error."""
    assert enums.clamp_limit(10_000) == enums.MAX_LIMIT == 200


def test_limit_passes_a_reasonable_value_through():
    assert enums.clamp_limit(25) == 25


def test_limit_none_takes_the_default():
    assert enums.clamp_limit(None) == enums.DEFAULT_LIMIT


def test_a_nonpositive_limit_clamps_rather_than_raising():
    """`limit=0` from a loop counter should return a row and be noticed."""
    assert enums.clamp_limit(0) == 1
    assert enums.clamp_limit(-5) == 1


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


FIRST = date(2010, 1, 4)
LAST = date(2026, 8, 17)


def test_a_date_inside_the_window_passes():
    enums.check_date_window(date(2020, 6, 1), FIRST, LAST)
    enums.check_date_window(FIRST, FIRST, LAST)
    enums.check_date_window(LAST, FIRST, LAST)


@pytest.mark.parametrize("value", [date(2009, 12, 31), date(2026, 8, 18)])
def test_a_date_outside_the_window_raises_and_names_the_window(value):
    with pytest.raises(DateOutOfWindow) as exc:
        enums.check_date_window(value, FIRST, LAST)
    assert "2010-01-04..2026-08-17" in str(exc.value)


def test_no_window_means_no_check():
    """An empty `bars` table cannot bound anything, and should not pretend to."""
    enums.check_date_window(date(1999, 1, 1), None, None)


# ---------------------------------------------------------------------------
# splits
# ---------------------------------------------------------------------------


def test_split_bounds_are_contiguous_and_do_not_overlap():
    sp = SplitParams()
    train_low, train_high = enums.split_bounds("train", sp)
    val_low, val_high = enums.split_bounds("validate", sp)
    assert train_low == date.fromisoformat(sp.event_start)
    assert train_high == date.fromisoformat(sp.train_end)
    assert val_low == date(2022, 1, 1)
    assert val_high == date.fromisoformat(sp.validate_end)
    assert train_high < val_low


def test_split_bounds_stop_short_of_holdout():
    """Validate ends where holdout begins, and nothing here reaches past it."""
    _, val_high = enums.split_bounds("validate")
    assert val_high < date(2024, 1, 1)


def test_holdout_raises_with_the_reason_rather_than_a_bare_enum_error():
    with pytest.raises(HoldoutRequested) as exc:
        enums.parse_split("holdout")
    message = str(exc.value)
    assert "evaluated exactly once" in message
    assert "holdout" not in message.split("Allowed:")[-1]
