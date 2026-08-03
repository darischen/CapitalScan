from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.research.path_reconcile import diff_labels


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
