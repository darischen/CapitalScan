from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.research.path_reconcile import (
    _unbackfilled_resolved_event_ids,
    assert_event_ids_match,
    diff_labels,
)


def test_diff_labels_no_mismatches_returns_empty_dict():
    derived = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02], "mae": [-0.01, -0.02]})
    actual = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02], "mae": [-0.01, -0.02]})
    mismatches = diff_labels(derived, actual, columns=["mfe", "mae"])
    assert mismatches == {}


def test_diff_labels_flags_a_numeric_mismatch_outside_tolerance():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.05]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert "mfe" in mismatches
    assert list(mismatches["mfe"]["event_id"]) == [1]


def test_diff_labels_tolerates_float_noise_within_1e_minus_9():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.010000000001]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert mismatches == {}


def test_diff_labels_flags_boolean_and_null_mismatches():
    derived = pd.DataFrame({"event_id": [1, 2], "touched_2pct": [True, None]})
    actual = pd.DataFrame({"event_id": [1, 2], "touched_2pct": [False, None]})
    mismatches = diff_labels(derived, actual, columns=["touched_2pct"])
    assert list(mismatches["touched_2pct"]["event_id"]) == [1]


def test_diff_labels_both_null_is_not_a_mismatch():
    derived = pd.DataFrame({"event_id": [1], "day_touched_5pct": [None]})
    actual = pd.DataFrame({"event_id": [1], "day_touched_5pct": [None]})
    mismatches = diff_labels(derived, actual, columns=["day_touched_5pct"])
    assert mismatches == {}


def test_diff_labels_capture_ratio_tolerates_quantization_scale_noise():
    # 0.5000005 vs 0.5 differs by 1e-6, which is far above the 1e-9
    # absolute tolerance used for mfe/mae, but is exactly the scale of
    # noise capture_ratio = r_exit / mfe picks up from numeric(12,6)
    # quantization on mfe (finding #3 of the final review) — must NOT flag.
    derived = pd.DataFrame({"event_id": [1], "capture_ratio": [0.5000005]})
    actual = pd.DataFrame({"event_id": [1], "capture_ratio": [0.5]})
    mismatches = diff_labels(derived, actual, columns=["capture_ratio"])
    assert mismatches == {}


def test_diff_labels_capture_ratio_still_flags_a_real_difference():
    derived = pd.DataFrame({"event_id": [1], "capture_ratio": [0.6]})
    actual = pd.DataFrame({"event_id": [1], "capture_ratio": [0.5]})
    mismatches = diff_labels(derived, actual, columns=["capture_ratio"])
    assert list(mismatches["capture_ratio"]["event_id"]) == [1]


def test_diff_labels_capture_ratio_flags_any_nonzero_diff_against_zero():
    derived = pd.DataFrame({"event_id": [1], "capture_ratio": [0.001]})
    actual = pd.DataFrame({"event_id": [1], "capture_ratio": [0.0]})
    mismatches = diff_labels(derived, actual, columns=["capture_ratio"])
    assert list(mismatches["capture_ratio"]["event_id"]) == [1]


def test_assert_event_ids_match_passes_on_identical_sets():
    derived = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02]})
    actual = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02]})
    assert_event_ids_match(derived, actual)  # no raise


def test_assert_event_ids_match_raises_when_actual_missing_an_event_id():
    # event_id 2 present in derived but missing from actual — an inner
    # join in diff_labels would silently drop it (finding #5).
    derived = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    with pytest.raises(ValueError, match="event_id"):
        assert_event_ids_match(derived, actual)


def test_assert_event_ids_match_raises_when_derived_missing_an_event_id():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    actual = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02]})
    with pytest.raises(ValueError, match="event_id"):
        assert_event_ids_match(derived, actual)


def test_unbackfilled_resolved_event_ids_flags_holding_days_without_path():
    # event 1: resolved position, but fwd_window_days never got written —
    # a real backfill gap. event 2: resolved and backfilled — fine.
    # event 3: never filled (holding_days None) — not a gap by design.
    rows = pd.DataFrame(
        {
            "event_id": [1, 2, 3],
            "holding_days": [3, 5, None],
            "fwd_window_days": [None, 11, None],
        }
    )
    gap = _unbackfilled_resolved_event_ids(rows)
    assert list(gap) == [1]
