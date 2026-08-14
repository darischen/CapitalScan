"""14.1's export, checked against the live database.

Read-only. `run_benchmarks(write=False, collect_curves=True)` performs no
INSERT and no DELETE, so this is safe beside a live poller, same pattern as
`test_benchmarks_measured.py`.

    uv run pytest capitalscan/tests/integration/test_curves_measured.py
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.arms import ARM_BUY_HOLD, ARM_SIGNAL
from capitalscan.jobs import db_io
from capitalscan.research import benchmarks, curves

LIVE_CONFIG_HASH = "697f3ae71428d392"
GATE_SPLIT = "validate"

# Small on purpose, same reasoning as `test_benchmarks_measured.py`'s
# DETERMINISM_REPLICATIONS: determinism and shape are properties of the
# seeding and the export logic, not of the sample size.
SMALL_REPLICATIONS = 5


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture(scope="module")
def live_data() -> None:
    engine = db_io.get_engine()
    with engine.connect() as conn:
        has_bars = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM bars WHERE interval = '1d')")
        ).scalar()
        has_events = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM events WHERE config_hash = :c)"),
            {"c": LIVE_CONFIG_HASH},
        ).scalar()
    if not has_bars or not has_events:
        pytest.skip(f"this database holds no bars/events for config_hash={LIVE_CONFIG_HASH}.")


@pytest.fixture(scope="module")
def report(live_data):
    engine = db_io.get_engine()
    _frame, rep = benchmarks.run_benchmarks(
        engine,
        LIVE_CONFIG_HASH,
        GATE_SPLIT,
        replications=SMALL_REPLICATIONS,
        run_id="curves-14-1-readonly",
        git_sha="0" * 40,
        write=False,
        collect_curves=True,
    )
    return rep


def test_collect_curves_false_leaves_report_curves_none(live_data):
    """The default path is untouched: `collect_curves` defaults to False and
    `report.curves` stays `None` unless explicitly requested."""
    engine = db_io.get_engine()
    _frame, rep = benchmarks.run_benchmarks(
        engine,
        LIVE_CONFIG_HASH,
        GATE_SPLIT,
        replications=SMALL_REPLICATIONS,
        run_id="curves-14-1-default",
        git_sha="0" * 40,
        write=False,
    )
    assert rep.curves is None


def test_curves_are_populated_when_requested(report):
    assert report.curves is not None
    assert len(report.curves.dates) == report.n_days
    assert len(report.curves.random) == SMALL_REPLICATIONS


def test_curve_endpoints_reproduce_arm_rows_total_ret_to_1e_minus_9(report):
    """14.1 acceptance: the curve export and the stored/`arm_rows` table
    describe the same simulation."""
    frame = curves.curve_frame(report.curves)
    arm_rows = report.arm_rows

    for arm in (ARM_BUY_HOLD, ARM_SIGNAL):
        expected = float(arm_rows.loc[arm_rows["arm"] == arm, "total_ret"].iloc[0])
        last_value = float(frame.loc[frame["arm"] == arm].iloc[-1]["value"])
        assert last_value - 1.0 == pytest.approx(expected, abs=1e-9)


def test_curve_has_one_row_per_trading_day_no_gaps(report):
    frame = curves.curve_frame(report.curves)
    expected_dates = [d.isoformat() for d in report.curves.dates]
    for arm in (ARM_BUY_HOLD, ARM_SIGNAL, curves.ARM_NULL_P50):
        got = list(frame.loc[frame["arm"] == arm, "date"])
        assert got == expected_dates


def test_band_contains_median_and_is_monotone(report):
    frame = curves.curve_frame(report.curves)
    p2_5 = frame.loc[frame["arm"] == curves.ARM_NULL_P2_5].set_index("date")["value"]
    p50 = frame.loc[frame["arm"] == curves.ARM_NULL_P50].set_index("date")["value"]
    p97_5 = frame.loc[frame["arm"] == curves.ARM_NULL_P97_5].set_index("date")["value"]
    for d in p50.index:
        assert p2_5[d] <= p50[d] <= p97_5[d]


def test_two_runs_of_run_benchmarks_produce_identical_curves(live_data):
    """Gate item 3 / 14.1 acceptance: two runs, byte-identical CSVs ignoring
    the header timestamp. Checked here at the frame level, which is what the
    CSV is written from."""
    engine = db_io.get_engine()
    _f1, r1 = benchmarks.run_benchmarks(
        engine,
        LIVE_CONFIG_HASH,
        GATE_SPLIT,
        replications=SMALL_REPLICATIONS,
        run_id="curves-14-1-det-a",
        git_sha="a" * 40,
        write=False,
        collect_curves=True,
    )
    _f2, r2 = benchmarks.run_benchmarks(
        engine,
        LIVE_CONFIG_HASH,
        GATE_SPLIT,
        replications=SMALL_REPLICATIONS,
        run_id="curves-14-1-det-b",
        git_sha="b" * 40,
        write=False,
        collect_curves=True,
    )
    frame_a = curves.curve_frame(r1.curves)
    frame_b = curves.curve_frame(r2.curves)
    assert frame_a.equals(frame_b)
    np.testing.assert_array_equal(frame_a["value"].to_numpy(), frame_b["value"].to_numpy())
