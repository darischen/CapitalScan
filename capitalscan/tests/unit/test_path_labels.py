from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.config import ExitParams
from capitalscan.core.types import Side
from capitalscan.research.path_labels import derive_labels_from_path

_CAPTURE_RATIO_CAP = ExitParams().capture_ratio_cap


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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["mfe"] != out["mfe"]  # NaN
    assert out["time_to_mfe"] is None
    assert out["capture_ratio"] is None
    assert out["touched_2pct"] is None
    assert out["day_touched_2pct"] is None


def test_mfe_mae_bounded_by_holding_days_not_full_window():
    # Exit on day 2 (holding_days=2): day 4's bigger favorable move must
    # NOT count toward MFE — this is the exact "different windows" trap
    # docs/sessions/session10.md warns about.
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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["touched_5pct"] is True
    assert out["day_touched_5pct"] == 4


def test_next_open_entry_offset_shifts_the_reachability_window():
    # entry_offset=1 (NEXT_OPEN): day_offset 1 is the entry day itself,
    # so the reachability window with max_hold_days=2 must be day_offset
    # in [2, 3], NOT [1, 2].
    path = _path(
        [
            (1, 0.09, -0.01, 0.0),  # entry day itself — must be excluded
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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
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
        exit_price=101.0,  # consistent with favorable=0.019978: ~101 = 100 * (1 + 0.01)
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
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
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["touched_5pct"] is True


def test_derive_labels_from_path_is_deterministic():
    path = _path([(1, 0.01, -0.005, 0.01), (2, 0.03, -0.01, 0.02)])
    kwargs = dict(
        path=path,
        entry_offset=0,
        holding_days=2,
        entry_price=100.0,
        exit_price=102.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1, 2),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    first = derive_labels_from_path(**kwargs)
    second = derive_labels_from_path(**kwargs)
    assert first == second


# Task 10.5: New label families - giveback
def test_giveback_null_when_position_unresolved():
    # holding_days=None: position never resolved, so giveback is null
    path = _path([(1, 0.02, -0.01, 0.01)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=None,
        entry_price=100.0,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["giveback"] is None


def test_giveback_null_when_empty_path():
    # Empty path (no forward days): mfe is NaN, so giveback is NaN
    path = _path([])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=100.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["giveback"] != out["giveback"]  # NaN


def test_giveback_computed_as_mfe_minus_realized_return():
    # mfe=0.03, realized_return (exit at 101 from entry 100) = 0.01
    # giveback = 0.03 - 0.01 = 0.02
    path = _path([(1, 0.02, -0.01, 0.01), (2, 0.03, -0.01, 0.02)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=2,
        entry_price=100.0,
        exit_price=101.0,  # realized_return = (101 - 100) / 100 = 0.01
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1, 2),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["mfe"] == pytest.approx(0.03)
    assert out["giveback"] == pytest.approx(0.02)  # 0.03 - 0.01


def test_giveback_non_negative_on_long_position():
    # Peak (mfe) is always >= exit level, so giveback >= 0
    # mfe=0.05, realized_return=0.05, giveback=0
    path = _path([(1, 0.05, -0.01, 0.05)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=105.0,  # exit at the peak
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["mfe"] == pytest.approx(0.05)
    assert out["giveback"] == pytest.approx(0.0)


def test_giveback_clamps_rounding_noise_to_zero_instead_of_raising():
    # Reproduces a live reconciliation crash (2026-08-04): mfe read back
    # from `path`'s numeric(12,6) storage rounded down just enough that
    # `mfe - r_exit` landed at -4.0676580399801043e-07, tripping a
    # zero-tolerance assert before the fix. r_exit is computed at full
    # float64 precision from entry/exit price, so it can sit fractionally
    # above a rounded mfe with no real invariant violation behind it.
    path = _path([(1, 0.062834, -0.01, 0.062834)])
    entry_price = 100.0
    # Chosen so realized_return computes to slightly more than mfe=0.062834
    # (within _GIVEBACK_ROUNDING_TOL), matching the live case's shape.
    exit_price = entry_price * (1 + 0.0628344067658)
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=entry_price,
        exit_price=exit_price,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["giveback"] == 0.0


def test_giveback_still_raises_past_rounding_tolerance():
    # A negative giveback larger than the rounding-noise tolerance is a
    # real invariant violation (the "peak" wasn't actually the peak) and
    # must still crash loudly, not get silently clamped away.
    path = _path([(1, 0.05, -0.01, 0.05)])
    with pytest.raises(AssertionError):
        derive_labels_from_path(
            path=path,
            entry_offset=0,
            holding_days=1,
            entry_price=100.0,
            exit_price=110.0,  # realized_return=0.10, far above mfe=0.05
            side=Side.LONG,
            max_hold_days=5,
            targets=(0.02,),
            horizons=(1,),
            capture_ratio_cap=_CAPTURE_RATIO_CAP,
        )


def test_giveback_with_short_position():
    # Short position: price fell favorably to 0.02 down, then rose back to 0.01 down at exit
    # For SHORT: favorable = (entry - low) / entry, so falling price is positive favorable
    # mfe = max favorable over holding period = 0.02 (price fell 2%)
    # realized_return = 0.01 (exited with 1% profit, since price rose back to 1% down)
    # giveback = 0.02 - 0.01 = 0.01 (gave back 1% from the peak)
    path = _path([(1, 0.02, 0.01, 0.01)])  # favorable=0.02, adverse=0.01, terminal=0.01
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=99.0,  # price fell 1%: realized_return = -(99-100)/100 = 0.01
        side=Side.SHORT,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    assert out["mfe"] == pytest.approx(0.02)
    assert out["giveback"] == pytest.approx(0.01)  # 0.02 - 0.01


def test_giveback_verifiable_by_hand_from_path():
    # Task 10.5 acceptance criterion: "Every new label is derivable by hand
    # from the path rows of a sampled event, verified on at least ten events
    # covering touched, untouched, and partial-window cases."
    # Multi-day path with peak on day 2, exit on day 3.
    path = _path(
        [
            (1, 0.005, -0.01, 0.002),
            (2, 0.035, -0.02, 0.030),  # peak reaches 3.5%
            (3, 0.028, -0.015, 0.025),  # exit day, terminal at 2.5%
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=3,  # exit on day 3
        entry_price=100.0,
        exit_price=102.5,  # exit at terminal mark of 2.5%
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02, 0.03),
        horizons=(1, 2, 3),
        capture_ratio_cap=_CAPTURE_RATIO_CAP,
    )
    # Hand calculation:
    # mfe = max(0.005, 0.035, 0.028) = 0.035
    # realized_return = (102.5 - 100) / 100 = 0.025
    # giveback = 0.035 - 0.025 = 0.010
    assert out["mfe"] == pytest.approx(0.035)
    assert out["giveback"] == pytest.approx(0.010)
    # Verify monotonicity: touched_2pct=True (peak reached 3.5%), touched_3pct=True
    assert out["touched_2pct"] is True
    assert out["touched_3pct"] is True
    assert out["day_touched_2pct"] == 2  # touched on day 2
    assert out["day_touched_3pct"] == 2  # touched on day 2
