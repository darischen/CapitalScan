from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.types import Side
from capitalscan.research.path_labels import derive_labels_from_path


def _path(rows):
    # rows: list of (day_offset, favorable, adverse, terminal)
    return pd.DataFrame(rows, columns=["day_offset", "favorable", "adverse", "terminal"])


def test_unresolved_position_returns_not_applicable_shape():
    path = _path([(1, 0.01, -0.01, 0.005)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=None,
        entry_price=100.0,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02, 0.03, 0.05, 0.10),
        horizons=(1, 2, 3, 5, 10),
    )
    assert out["mfe"] != out["mfe"]  # NaN
    assert out["time_to_mfe"] is None
    assert out["capture_ratio"] is None
    assert out["touched_2pct"] is None
    assert out["day_touched_2pct"] is None


def test_mfe_mae_bounded_by_holding_days_not_full_window():
    # Exit on day 2 (holding_days=2): day 4's bigger favorable move must
    # NOT count toward MFE — this is the exact "different windows" trap
    # docs/session10.md warns about.
    path = _path(
        [
            (1, 0.01, -0.005, 0.01),
            (2, 0.02, -0.01, 0.02),
            (3, 0.03, -0.01, 0.03),
            (4, 0.09, -0.01, 0.09),  # bigger move, but past the exit
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=2,
        entry_price=100.0,
        exit_price=102.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02, 0.03, 0.05, 0.10),
        horizons=(1, 2, 3, 5, 10),
    )
    assert out["mfe"] == pytest.approx(0.02)
    assert out["time_to_mfe"] == 2


def test_reachability_uses_full_max_hold_days_window_past_an_early_exit():
    # Exit on day 1 (holding_days=1), but a day-4 touch of 5% must still
    # register in touched_5pct/day_touched_5pct (reachability window is
    # [1, max_hold_days], independent of exit timing — DESIGN §5.6).
    path = _path(
        [
            (1, 0.01, -0.005, 0.01),
            (2, 0.02, -0.01, 0.02),
            (3, 0.03, -0.01, 0.03),
            (4, 0.06, -0.01, 0.06),
            (5, 0.02, -0.01, 0.02),
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=101.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.05,),
        horizons=(1,),
    )
    assert out["touched_5pct"] is True
    assert out["day_touched_5pct"] == 4


def test_next_open_entry_offset_shifts_the_reachability_window():
    # entry_offset=1 (NEXT_OPEN): day_offset 1 is the entry day itself,
    # so the reachability window with max_hold_days=2 must be day_offset
    # in [2, 3], NOT [1, 2].
    path = _path(
        [
            (1, 0.09, -0.01, 0.0),   # entry day itself — must be excluded
            (2, 0.01, -0.01, 0.01),
            (3, 0.02, -0.01, 0.02),
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=1,
        holding_days=2,
        entry_price=100.0,
        exit_price=101.0,
        side=Side.LONG,
        max_hold_days=2,
        targets=(0.05,),
        horizons=(1,),
    )
    assert out["touched_5pct"] is False  # the 9% move on day 1 doesn't count


def test_fwd_ret_horizon_reads_terminal_at_entry_offset_plus_horizon():
    path = _path([(1, 0.0, 0.0, 0.005), (2, 0.0, 0.0, 0.02), (3, 0.0, 0.0, 0.03)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=None,
        entry_price=100.0,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1, 2, 3),
    )
    assert out["fwd_ret_2d"] == pytest.approx(0.02)


def test_capture_ratio_null_when_mfe_non_positive():
    path = _path([(1, -0.01, -0.02, -0.01)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=99.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
    )
    assert out["capture_ratio"] is None


def test_reachability_does_not_use_breach_price_rounding_on_a_ratio():
    # Real reconciliation-run case (event 2824409/HD): favorable=0.019978
    # is 0.000022 below the 2% target. An earlier revision of this
    # function routed the comparison through `core.signals._breach`,
    # which rounds both operands to 4 DECIMAL PLACES (DESIGN §3.2) — a
    # rule sized for comparing dollar prices, not return ratios.
    # round(0.019978, 4) == 0.02, so _breach spuriously reported this as
    # touched, disagreeing with Session 9's own price-level comparison
    # (which never rounds the *ratio* at all). Plain `>=` at the stored
    # numeric(12,6) precision is the correct, faithful comparison — must
    # NOT flag as touched.
    path = _path([(1, 0.019978, -0.01, 0.01)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=105.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
    )
    assert out["touched_2pct"] is False
    assert out["day_touched_2pct"] is None


def test_reachability_exact_target_match_counts_as_touched():
    path = _path([(1, 0.05, -0.01, 0.05)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=105.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.05,),
        horizons=(1,),
    )
    assert out["touched_5pct"] is True


def test_derive_labels_from_path_is_deterministic():
    path = _path([(1, 0.01, -0.005, 0.01), (2, 0.03, -0.01, 0.02)])
    kwargs = dict(
        path=path, entry_offset=0, holding_days=2, entry_price=100.0,
        exit_price=102.0, side=Side.LONG, max_hold_days=5,
        targets=(0.02,), horizons=(1, 2),
    )
    first = derive_labels_from_path(**kwargs)
    second = derive_labels_from_path(**kwargs)
    assert first == second
