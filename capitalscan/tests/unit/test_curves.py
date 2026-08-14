"""Contract for `research/curves.py` (Session 14, task 14.1).

`research.benchmarks.run_benchmarks(collect_curves=True)` is what actually
computes the equity curves; this file never calls it and never touches a
database. Every test here builds an `ArmCurves` by hand and checks that
`research.curves` only *shapes* it — percentile band, tidy frame, CSV — with
no simulation of its own (invariant 2).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.arms import ARM_BUY_HOLD, ARM_SIGNAL
from capitalscan.research import curves
from capitalscan.research.benchmarks import ArmCurves


def _dates(n: int, start: date = date(2020, 1, 1)) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(n))


def _equity(n_days: int, growth: float) -> pd.Series:
    """A smooth curve from 1.0 to `growth` over `n_days + 1` points."""
    return pd.Series(np.linspace(1.0, growth, n_days + 1))


def _arm_curves(n_days: int = 5, n_reps: int = 20, seed: int = 0) -> ArmCurves:
    rng = np.random.default_rng(seed)
    dates = _dates(n_days)
    buy_hold = _equity(n_days, 1.30)
    signal = _equity(n_days, 1.10)
    random = tuple(
        pd.Series(np.cumprod(1.0 + rng.normal(0.0005, 0.01, size=n_days + 1)))
        for _ in range(n_reps)
    )
    return ArmCurves(dates=dates, buy_hold=buy_hold, signal=signal, random=random)


# --------------------------------------------------------------------------
# Row shape: one row per trading day, no gaps
# --------------------------------------------------------------------------


def test_curve_frame_has_one_row_per_trading_day_per_series():
    ac = _arm_curves(n_days=7)
    frame = curves.curve_frame(ac)

    for arm in (ARM_BUY_HOLD, ARM_SIGNAL, curves.ARM_NULL_P50):
        rows = frame.loc[frame["arm"] == arm]
        assert len(rows) == 7
        assert list(rows["date"]) == [d.isoformat() for d in ac.dates]


def test_curve_frame_columns_are_exactly_date_arm_value():
    ac = _arm_curves(n_days=3, n_reps=5)
    frame = curves.curve_frame(ac)
    assert list(frame.columns) == curves.CURVE_COLUMNS


def test_a_mismatched_equity_length_raises():
    ac = _arm_curves(n_days=5)
    bad = ArmCurves(
        dates=ac.dates,
        buy_hold=pd.Series([1.0, 1.1, 1.2]),  # wrong length
        signal=ac.signal,
        random=ac.random,
    )
    with pytest.raises(ValueError, match="equity has"):
        curves.curve_frame(bad)


def test_no_null_replications_raises():
    ac = _arm_curves(n_days=5)
    empty = ArmCurves(dates=ac.dates, buy_hold=ac.buy_hold, signal=ac.signal, random=())
    with pytest.raises(ValueError, match="no null replications"):
        curves.curve_frame(empty)


# --------------------------------------------------------------------------
# Endpoint equivalence with `benchmarks.total_ret`
# --------------------------------------------------------------------------


def test_curve_endpoints_reproduce_total_ret_to_1e_minus_9():
    """14.1 acceptance: the exported curve's last value, minus one, is the
    arm's total_ret to the same precision `ArmSimulation.total_ret` computes
    it (equity[-1] / equity[0] - 1, and equity[0] == 1.0 always)."""
    ac = _arm_curves(n_days=10)
    frame = curves.curve_frame(ac)

    for arm, equity in ((ARM_BUY_HOLD, ac.buy_hold), (ARM_SIGNAL, ac.signal)):
        expected_total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
        last_row = frame.loc[frame["arm"] == arm].iloc[-1]
        assert float(last_row["value"]) - 1.0 == pytest.approx(expected_total_ret, abs=1e-9)


# --------------------------------------------------------------------------
# The null band
# --------------------------------------------------------------------------


def test_the_band_contains_the_median_and_is_monotone_every_date():
    ac = _arm_curves(n_days=15, n_reps=50)
    frame = curves.curve_frame(ac)

    p2_5 = frame.loc[frame["arm"] == curves.ARM_NULL_P2_5].set_index("date")["value"]
    p50 = frame.loc[frame["arm"] == curves.ARM_NULL_P50].set_index("date")["value"]
    p97_5 = frame.loc[frame["arm"] == curves.ARM_NULL_P97_5].set_index("date")["value"]

    for d in p50.index:
        assert p2_5[d] <= p50[d] <= p97_5[d]


def test_the_band_is_the_percentile_of_the_replications_at_each_day():
    ac = _arm_curves(n_days=4, n_reps=40)
    frame = curves.curve_frame(ac)

    day_idx = 2
    day = ac.dates[day_idx].isoformat()
    values_that_day = np.array([float(r.iloc[day_idx + 1]) for r in ac.random])

    p50_row = frame.loc[(frame["arm"] == curves.ARM_NULL_P50) & (frame["date"] == day)]
    expected = float(np.percentile(values_that_day, 50.0))
    assert float(p50_row["value"].iloc[0]) == pytest.approx(expected)


# --------------------------------------------------------------------------
# Determinism: two runs, identical bytes ignoring the header timestamp
# --------------------------------------------------------------------------


def test_curve_frame_is_deterministic_across_two_calls():
    ac = _arm_curves(n_days=6, n_reps=10)
    a = curves.curve_frame(ac)
    b = curves.curve_frame(ac)
    pd.testing.assert_frame_equal(a, b)


def test_two_csv_writes_are_byte_identical_ignoring_the_header_timestamp(tmp_path):
    ac = _arm_curves(n_days=6, n_reps=10)
    frame = curves.curve_frame(ac)

    path_a = tmp_path / "a.csv"
    path_b = tmp_path / "b.csv"
    curves.write_curve_csv(frame, path_a)
    curves.write_curve_csv(frame, path_b)

    lines_a = path_a.read_text(encoding="utf-8").splitlines()
    lines_b = path_b.read_text(encoding="utf-8").splitlines()
    assert lines_a[0].startswith("# generated_at=")
    assert lines_b[0].startswith("# generated_at=")
    assert lines_a[1:] == lines_b[1:]


def test_csv_path_follows_the_naming_rule():
    path = curves.curve_csv_path("697f3ae71428d392", "train")
    assert path.name == "equity_curves_697f3ae71428d392_train.csv"


def test_export_curves_writes_a_readable_csv(tmp_path):
    ac = _arm_curves(n_days=5, n_reps=10)
    frame, path = curves.export_curves(ac, "abc123", "train", output_dir=tmp_path)
    assert path.exists()
    reread = pd.read_csv(path, comment="#")
    assert list(reread.columns) == curves.CURVE_COLUMNS
    assert len(reread) == len(frame)
