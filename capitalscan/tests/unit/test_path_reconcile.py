from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import DEFAULT_CONFIG
from capitalscan.research import path_reconcile as path_reconcile_mod
from capitalscan.research.path_reconcile import (
    CAPTURE_RATIO_MFE_FLOOR,
    EXPLAINED_COLUMNS,
    RECENT_BARS_REVISION_DAYS,
    ReconciliationReport,
    _drop_incomplete_reach_window_rows,
    _drop_recent_events,
    _drop_unstable_capture_ratio_rows,
    _incomplete_reach_window_event_ids,
    _unbackfilled_resolved_event_ids,
    assert_event_ids_match,
    diff_labels,
    reconcile,
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


def test_diff_labels_tolerates_float_noise_within_tolerance():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.010000000001]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert mismatches == {}


def test_diff_labels_mfe_tolerates_one_quantum_of_independent_numeric_12_6_rounding():
    # Reproduces the real reconciliation run (2026-08-03): two sides
    # independently round the same true value to numeric(12,6), landing
    # exactly one unit apart in the last decimal place (event
    # 2896328/ORCL: derived 0.027566 vs stored 0.027565) — must NOT flag.
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.027566]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.027565]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert mismatches == {}


def test_diff_labels_mfe_still_flags_a_difference_beyond_calibrated_tolerance():
    # 5e-4 is ~4x the widened 1.2e-4 tolerance (itself calibrated to the
    # real run's full measured tail, max 9.5e-5 — see _FLOAT_TOL's
    # comment) — a genuine difference, not rounding/near-tie-max-selection
    # noise.
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.028065]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.027565]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert list(mismatches["mfe"]["event_id"]) == [1]


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


def test_drop_unstable_capture_ratio_rows_removes_near_zero_mfe_events():
    # Real reconciliation run: derived -6880.28 vs actual -11758.23 on an
    # event whose mfe was essentially zero — a huge absolute/relative
    # difference driven entirely by dividing by a near-zero denominator,
    # not a computation defect. Must be dropped, not flagged.
    mismatches = {
        "capture_ratio": pd.DataFrame(
            {
                "event_id": [1, 2],
                "capture_ratio_derived": [-6880.28, 0.6],
                "capture_ratio_actual": [-11758.23, 0.5],
            }
        )
    }
    actual = pd.DataFrame({"event_id": [1, 2], "mfe": [0.00001, 0.02]})
    out = _drop_unstable_capture_ratio_rows(mismatches, actual)
    assert list(out["capture_ratio"]["event_id"]) == [2]


def test_drop_unstable_capture_ratio_rows_drops_the_column_entirely_when_all_unstable():
    mismatches = {
        "capture_ratio": pd.DataFrame(
            {
                "event_id": [1],
                "capture_ratio_derived": [-6880.28],
                "capture_ratio_actual": [-11758.23],
            }
        )
    }
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.00001]})
    out = _drop_unstable_capture_ratio_rows(mismatches, actual)
    assert "capture_ratio" not in out


def test_drop_unstable_capture_ratio_rows_boundary_at_the_floor():
    mismatches = {
        "capture_ratio": pd.DataFrame(
            {
                "event_id": [1, 2],
                "capture_ratio_derived": [1.0, 1.0],
                "capture_ratio_actual": [0.9, 0.9],
            }
        )
    }
    actual = pd.DataFrame(
        {"event_id": [1, 2], "mfe": [CAPTURE_RATIO_MFE_FLOOR, CAPTURE_RATIO_MFE_FLOOR / 2]}
    )
    out = _drop_unstable_capture_ratio_rows(mismatches, actual)
    assert list(out["capture_ratio"]["event_id"]) == [1]  # exactly at the floor is kept (>=)


def test_drop_unstable_capture_ratio_rows_no_op_when_column_absent():
    mismatches = {"mfe": pd.DataFrame({"event_id": [1], "mfe_derived": [0.1], "mfe_actual": [0.2]})}
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.2]})
    out = _drop_unstable_capture_ratio_rows(mismatches, actual)
    assert out == mismatches


def test_diff_labels_capture_ratio_adaptive_tolerance_scales_with_mfe():
    # Real reconciliation-run pattern: mfe=0.016 (well above
    # CAPTURE_RATIO_MFE_FLOOR but small enough that a fixed 5e-4 relative
    # tolerance is too tight for mfe's own 3e-5 noise). With mfe present
    # alongside capture_ratio, the tolerance must widen adaptively — a
    # capture_ratio difference driven purely by mfe-scale noise at this
    # mfe must NOT flag, even though it clears the old fixed 5e-4.
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.016], "capture_ratio": [0.301]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.016], "capture_ratio": [0.300]})
    mismatches = diff_labels(derived, actual, columns=["mfe", "capture_ratio"])
    assert "capture_ratio" not in mismatches


def test_diff_labels_capture_ratio_adaptive_tolerance_still_flags_real_difference():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.016], "capture_ratio": [0.5]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.016], "capture_ratio": [0.3]})
    mismatches = diff_labels(derived, actual, columns=["mfe", "capture_ratio"])
    assert list(mismatches["capture_ratio"]["event_id"]) == [1]


def test_diff_labels_capture_ratio_falls_back_to_fixed_tolerance_without_mfe():
    # No `mfe` column in the frame diffed — _capture_ratio_tolerance can't
    # adapt, so it must fall back to the fixed RELATIVE_TOLERANCE_COLUMNS
    # value rather than raising or silently comparing at 0 tolerance.
    derived = pd.DataFrame({"event_id": [1], "capture_ratio": [0.5000005]})
    actual = pd.DataFrame({"event_id": [1], "capture_ratio": [0.5]})
    mismatches = diff_labels(derived, actual, columns=["capture_ratio"])
    assert mismatches == {}


def test_drop_recent_events_excludes_rows_within_the_window():
    # Real reconciliation run: 42 events, all within RECENT_BARS_REVISION_DAYS
    # of "today", showing a uniform ~3e-4 mfe diff traced to a bars-refresh
    # job re-ingesting recent daily data after the path backfill ran.
    mismatches = {
        "mfe": pd.DataFrame(
            {"event_id": [1, 2], "mfe_derived": [0.05, 0.06], "mfe_actual": [0.04, 0.05]}
        )
    }
    today = date(2026, 8, 3)
    signal_dates = pd.Series(
        {1: pd.Timestamp("2026-07-29"), 2: pd.Timestamp("2020-01-01")}  # 1: recent, 2: old
    )
    out, dropped = _drop_recent_events(mismatches, ["mfe"], signal_dates, today)
    assert list(out["mfe"]["event_id"]) == [2]
    assert dropped == {"mfe": 1}


def test_drop_recent_events_drops_the_column_entirely_when_all_recent():
    mismatches = {
        "mfe": pd.DataFrame({"event_id": [1], "mfe_derived": [0.05], "mfe_actual": [0.04]})
    }
    today = date(2026, 8, 3)
    signal_dates = pd.Series({1: pd.Timestamp("2026-07-29")})
    out, dropped = _drop_recent_events(mismatches, ["mfe"], signal_dates, today)
    assert "mfe" not in out
    assert dropped == {"mfe": 1}


def test_drop_recent_events_boundary_at_the_window_edge():
    today = date(2026, 8, 3)
    cutoff = today - pd.Timedelta(days=RECENT_BARS_REVISION_DAYS)
    mismatches = {
        "mfe": pd.DataFrame(
            {"event_id": [1, 2], "mfe_derived": [0.05, 0.05], "mfe_actual": [0.04, 0.04]}
        )
    }
    # event 1: exactly at the cutoff date (excluded, `<` not `<=` — the
    # window is inclusive of "window_days ago"); event 2: one day older
    # (included).
    signal_dates = pd.Series(
        {1: pd.Timestamp(cutoff), 2: pd.Timestamp(cutoff) - pd.Timedelta(days=1)}
    )
    out, dropped = _drop_recent_events(mismatches, ["mfe"], signal_dates, today)
    assert list(out["mfe"]["event_id"]) == [2]


def test_drop_recent_events_no_op_when_column_absent():
    mismatches = {"touched_2pct": pd.DataFrame({"event_id": [1]})}
    today = date(2026, 8, 3)
    signal_dates = pd.Series({1: pd.Timestamp("2020-01-01")})
    out, dropped = _drop_recent_events(mismatches, ["mfe"], signal_dates, today)
    assert out == mismatches
    assert dropped == {}


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


class _EmptyResultConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        return []


class _EmptyResultEngine:
    """Just enough of `Engine` for `reconcile()`'s `actual` query to come
    back with zero rows — no real database involved.
    """

    def connect(self):
        return _EmptyResultConn()


def test_reconcile_raises_on_zero_events_instead_of_a_vacuous_pass(monkeypatch):
    # Reproduces the real false-positive PASS: `run_id`-based lookup
    # matched zero events (a later run reusing the same config_hash had
    # silently relabeled every row's run_id — see derive_session9_labels'
    # docstring). A 0-event comparison must never read as a real pass.
    monkeypatch.setattr(
        path_reconcile_mod,
        "derive_session9_labels",
        lambda engine, config, config_hash: pd.DataFrame(),
    )

    def fake_read_sql(stmt, conn, params=None):
        return pd.DataFrame(columns=["event_id"])

    monkeypatch.setattr(path_reconcile_mod.pd, "read_sql", fake_read_sql)

    with pytest.raises(ValueError, match="zero events"):
        reconcile(_EmptyResultEngine(), DEFAULT_CONFIG, "nonexistent_config_hash")


def test_reachability_columns_are_explained_as_a_structural_boundary_difference():
    # Session 10 residual investigation (2026-08-04): touched_*pct/
    # day_touched_*pct disagreements at exact-boundary events trace to
    # Session 9's _breach-based price-level rounding vs Task 10.3's
    # ratio-only comparison (path_labels.py cannot re-read bar prices,
    # per its own acceptance criterion) — structural, not fixable within
    # 10.3's scope, so it's explained like fwd_ret_*d, not silently
    # dropped.
    for col in [
        "touched_2pct",
        "day_touched_2pct",
        "touched_3pct",
        "day_touched_3pct",
        "touched_5pct",
        "day_touched_5pct",
        "touched_10pct",
        "day_touched_10pct",
    ]:
        assert col in EXPLAINED_COLUMNS


def test_capture_ratio_is_explained_as_a_bars_revision_artifact():
    # Session 10 residual investigation (2026-08-04): the post-tolerance
    # capture_ratio residual (38 events) was verified 38/38 against `bars`
    # to be on tickers re-ingested by a specific post-backfill bars
    # revision run, the same mechanism RECENT_BARS_REVISION_DAYS already
    # excludes by date — this job just touched full ticker history
    # instead of a recent window, so the date heuristic missed it.
    assert "capture_ratio" in EXPLAINED_COLUMNS


def test_reconciliation_report_passes_when_only_explained_columns_mismatch():
    report = ReconciliationReport(
        config_hash="abc",
        total_events=10,
        mismatches={"fwd_ret_1d": pd.DataFrame({"event_id": [1]})},
        explained={"fwd_ret_1d": "known price-series convention difference"},
    )
    assert report.passes is True
    assert report.unexplained_mismatch_columns == []


def test_reconciliation_report_fails_on_an_unexplained_mismatch():
    report = ReconciliationReport(
        config_hash="abc",
        total_events=10,
        mismatches={"mfe": pd.DataFrame({"event_id": [1]})},
        explained={},
    )
    assert report.passes is False
    assert report.unexplained_mismatch_columns == ["mfe"]


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


def test_incomplete_reach_window_ids_account_for_the_entry_offset():
    # max_hold_days=5. A `touch` entry (offset 0) needs day_offset 5;
    # a `next_open` entry (offset 1) needs day_offset 6 for the same
    # five-day reachability window. Event 2 has exactly the 5 days a
    # `touch` event needs but one short of what `next_open` needs — the
    # off-by-one this helper exists to get right.
    rows = pd.DataFrame(
        {
            "event_id": [1, 2, 3, 4],
            "entry_kind": ["touch", "next_open", "next_open", "touch"],
            "fwd_window_days": [5, 5, 6, 4],
        }
    )
    incomplete = _incomplete_reach_window_event_ids(rows, max_hold_days=5)
    assert list(incomplete) == [2, 4]


def test_incomplete_reach_window_ids_treat_a_null_window_as_incomplete():
    rows = pd.DataFrame({"event_id": [1], "entry_kind": ["touch"], "fwd_window_days": [None]})
    incomplete = _incomplete_reach_window_event_ids(rows, max_hold_days=5)
    assert list(incomplete) == [1]


def test_drop_incomplete_reach_window_rows_keeps_the_settled_boundary_cases():
    # Reproduces the 2026-08-05 measurement: event 1 is the CAT-shaped
    # still-accumulating case (4 forward days against a 5-day reach
    # window, path.favorable already past the target the stored label
    # says was never touched); event 2 is a settled full-window boundary
    # disagreement, which must survive so EXPLAINED_COLUMNS still covers
    # something a human verified.
    mismatches = {
        "touched_5pct": pd.DataFrame(
            {
                "event_id": [1, 2],
                "touched_5pct_derived": [True, True],
                "touched_5pct_actual": [False, False],
            }
        )
    }
    rows = pd.DataFrame(
        {
            "event_id": [1, 2],
            "entry_kind": ["touch", "next_open"],
            "fwd_window_days": [4, 11],
        }
    )
    out, dropped = _drop_incomplete_reach_window_rows(
        mismatches, ["touched_5pct"], rows, max_hold_days=5
    )
    assert list(out["touched_5pct"]["event_id"]) == [2]
    assert dropped == {"touched_5pct": 1}


def test_drop_incomplete_reach_window_rows_drops_the_column_when_all_incomplete():
    mismatches = {
        "day_touched_2pct": pd.DataFrame(
            {"event_id": [1], "day_touched_2pct_derived": [4], "day_touched_2pct_actual": [None]}
        )
    }
    rows = pd.DataFrame({"event_id": [1], "entry_kind": ["touch"], "fwd_window_days": [3]})
    out, dropped = _drop_incomplete_reach_window_rows(
        mismatches, ["day_touched_2pct"], rows, max_hold_days=5
    )
    assert "day_touched_2pct" not in out
    assert dropped == {"day_touched_2pct": 1}


def test_drop_recent_events_covers_the_reachability_family_too():
    # The stale-stored-label mechanism (event 2775909/CB shape): the path
    # window is complete, so _drop_incomplete_reach_window_rows keeps the
    # row; the date filter is what excludes it. Event 2 is the settled
    # boundary case that must survive both.
    mismatches = {
        "touched_5pct": pd.DataFrame(
            {
                "event_id": [1, 2],
                "touched_5pct_derived": [True, True],
                "touched_5pct_actual": [False, False],
            }
        )
    }
    today = date(2026, 8, 5)
    signal_dates = pd.Series({1: pd.Timestamp("2026-07-28"), 2: pd.Timestamp("2024-11-01")})
    out, dropped = _drop_recent_events(mismatches, ["touched_5pct"], signal_dates, today)
    assert list(out["touched_5pct"]["event_id"]) == [2]
    assert dropped == {"touched_5pct": 1}


def test_drop_incomplete_reach_window_rows_ignores_columns_with_no_mismatches():
    rows = pd.DataFrame({"event_id": [1], "entry_kind": ["touch"], "fwd_window_days": [3]})
    out, dropped = _drop_incomplete_reach_window_rows({}, ["touched_5pct"], rows, max_hold_days=5)
    assert out == {}
    assert dropped == {}
