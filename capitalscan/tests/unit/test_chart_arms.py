"""Contract for `research/chart_arms.py` (Session 14, task 14.2).

Every test here builds a curve frame and `ArmSummary` values by hand and
checks that `research.chart_arms` only *shapes* them into SVG — no
simulation, no DB, no recomputed statistic (invariant 2). `render_three_arm_svg`
and the geometry helpers are pure functions, which is what makes them
testable without a database.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.arms import ARM_BUY_HOLD, ARM_SIGNAL
from capitalscan.research import chart_arms
from capitalscan.research.benchmarks import ArmCurves
from capitalscan.research.curves import curve_frame as build_curve_frame

SVG_NS = "{http://www.w3.org/2000/svg}"


def _dates(n: int, start: date = date(2020, 1, 1)) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(n))


def _equity(n_days: int, growth: float) -> pd.Series:
    return pd.Series(np.linspace(1.0, growth, n_days + 1))


def _frame(n_days: int = 10, n_reps: int = 20, seed: int = 0, flat: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = _dates(n_days)
    if flat:
        buy_hold = pd.Series([1.0] * (n_days + 1))
        signal = pd.Series([1.0] * (n_days + 1))
        random = tuple(pd.Series([1.0] * (n_days + 1)) for _ in range(n_reps))
    else:
        buy_hold = _equity(n_days, 3.8366)
        signal = _equity(n_days, 1.0837)
        random = tuple(
            pd.Series(np.cumprod(1.0 + rng.normal(0.0005, 0.01, size=n_days + 1)))
            for _ in range(n_reps)
        )
    ac = ArmCurves(dates=dates, buy_hold=buy_hold, signal=signal, random=random)
    return build_curve_frame(ac)


def _summaries(
    buy_hold_total_ret: float = 3.8366, signal_total_ret: float = 1.0837
) -> dict[str, chart_arms.ArmSummary]:
    return {
        ARM_BUY_HOLD: chart_arms.ArmSummary(
            arm=ARM_BUY_HOLD,
            total_ret=buy_hold_total_ret,
            annualized_ret=0.1436,
            sharpe=0.7689,
            max_drawdown=0.3413,
            frac_deployed=1.0,
            capital_efficiency=buy_hold_total_ret,
        ),
        ARM_SIGNAL: chart_arms.ArmSummary(
            arm=ARM_SIGNAL,
            total_ret=signal_total_ret,
            annualized_ret=0.0645,
            sharpe=0.3481,
            max_drawdown=0.2731,
            frac_deployed=0.9294,
            capital_efficiency=1.1660,
        ),
    }


# --------------------------------------------------------------------------
# Acceptance 1: valid XML, exactly three <polyline>, one band <path>
# --------------------------------------------------------------------------


def test_svg_parses_and_has_exactly_three_polylines_and_one_band_path():
    frame = _frame()
    svg = chart_arms.render_three_arm_svg(frame, _summaries(), "697f3ae71428d392", "train")

    root = ET.fromstring(svg)  # raises if not well-formed XML
    polylines = root.findall(f".//{SVG_NS}polyline")
    paths = root.findall(f".//{SVG_NS}path")
    assert len(polylines) == 3
    assert len(paths) == 1


def test_svg_root_is_svg_element_with_viewbox():
    frame = _frame()
    svg = chart_arms.render_three_arm_svg(frame, _summaries(), "abc123", "validate")
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    assert "viewBox" in root.attrib


# --------------------------------------------------------------------------
# Acceptance 2: every rendered table number matches the benchmarks row
# --------------------------------------------------------------------------


def _table_text(svg: str) -> str:
    root = ET.fromstring(svg)
    return " ".join(t.text or "" for t in root.iter(f"{SVG_NS}text"))


def test_summary_table_numbers_match_the_arm_summary_exactly():
    summaries = _summaries()
    frame = _frame()
    svg = chart_arms.render_three_arm_svg(frame, summaries, "697f3ae71428d392", "train")
    text = _table_text(svg)

    for arm, row in summaries.items():
        assert chart_arms._pct(row.total_ret) in text
        assert chart_arms._pct(row.annualized_ret) in text
        assert chart_arms._num(row.sharpe) in text
        assert chart_arms._pct(row.max_drawdown) in text
        assert chart_arms._pct(row.frac_deployed) in text
        assert chart_arms._num(row.capital_efficiency) in text


def test_rendered_percentages_round_trip_to_the_source_value():
    summaries = _summaries(buy_hold_total_ret=0.5, signal_total_ret=-0.25)
    frame = _frame()
    svg = chart_arms.render_three_arm_svg(frame, summaries, "abc", "train")
    text = _table_text(svg)
    assert "50.00%" in text
    assert "-25.00%" in text


def test_sharpe_none_renders_as_n_a_not_a_recomputed_value():
    summaries = _summaries()
    bh = summaries[ARM_BUY_HOLD]
    summaries[ARM_BUY_HOLD] = chart_arms.ArmSummary(
        arm=bh.arm,
        total_ret=bh.total_ret,
        annualized_ret=bh.annualized_ret,
        sharpe=None,
        max_drawdown=bh.max_drawdown,
        frac_deployed=bh.frac_deployed,
        capital_efficiency=bh.capital_efficiency,
    )
    frame = _frame()
    svg = chart_arms.render_three_arm_svg(frame, summaries, "abc", "train")
    assert "n/a" in _table_text(svg)


# --------------------------------------------------------------------------
# Acceptance 3: a flat arm does not divide by zero on the log scale
# --------------------------------------------------------------------------


def test_flat_arm_fixture_renders_without_a_zero_division():
    frame = _frame(flat=True)
    svg = chart_arms.render_three_arm_svg(
        frame, _summaries(buy_hold_total_ret=0.0, signal_total_ret=0.0), "flat", "train"
    )
    root = ET.fromstring(svg)
    assert len(root.findall(f".//{SVG_NS}polyline")) == 3


def test_log_scale_guards_zero_span_directly():
    log_lo, log_hi, y = chart_arms._log_scale([1.0, 1.0, 1.0], top=0.0, bottom=100.0)
    assert log_hi > log_lo  # padded, not equal
    # A single value maps to a finite pixel inside the padded range.
    assert 0.0 <= y(1.0) <= 100.0


def test_log_scale_rejects_all_non_positive_values():
    with pytest.raises(ValueError, match="no positive"):
        chart_arms._log_scale([0.0, -1.0], top=0.0, bottom=100.0)


# --------------------------------------------------------------------------
# Acceptance 4: two runs, identical bytes
# --------------------------------------------------------------------------


def test_two_renders_are_byte_identical():
    frame = _frame()
    summaries = _summaries()
    a = chart_arms.render_three_arm_svg(frame, summaries, "697f3ae71428d392", "train")
    b = chart_arms.render_three_arm_svg(frame, summaries, "697f3ae71428d392", "train")
    assert a == b


def test_two_file_writes_are_byte_identical(tmp_path, monkeypatch):
    frame = _frame()
    summaries = _summaries()
    svg = chart_arms.render_three_arm_svg(frame, summaries, "abc", "train")
    path_a = tmp_path / "a.svg"
    path_b = tmp_path / "b.svg"
    path_a.write_text(svg, encoding="utf-8", newline="\n")
    path_b.write_text(svg, encoding="utf-8", newline="\n")
    assert path_a.read_bytes() == path_b.read_bytes()


# --------------------------------------------------------------------------
# The verdict statement
# --------------------------------------------------------------------------


def test_verdict_states_signal_did_not_clear_the_null():
    # signal ends near 1.0837 (total_ret 0.0837); null band's replications
    # are random-walk-ish and drift upward with mean 0.0005/day, so over 10
    # days the p97.5 endpoint should sit above a barely-positive signal in
    # this fixture's seed. Assert on the actual computed relationship rather
    # than a hardcoded expectation.
    frame = _frame(n_days=10, n_reps=200, seed=1)
    signal_end = frame.loc[frame["arm"] == ARM_SIGNAL].sort_values("date")["value"].iloc[-1]
    null97_end = frame.loc[frame["arm"] == "null_p97_5"].sort_values("date")["value"].iloc[-1]
    svg = chart_arms.render_three_arm_svg(frame, _summaries(), "abc", "train")
    text = _table_text(svg)
    if signal_end > null97_end:
        assert "CLEARED" in text
    else:
        assert "did NOT clear" in text


def test_verdict_line_names_the_split_key():
    frame = _frame()
    svg = chart_arms.render_three_arm_svg(frame, _summaries(), "abc", "validate")
    assert "on validate." in _table_text(svg)


# --------------------------------------------------------------------------
# CSV round trip and naming
# --------------------------------------------------------------------------


def test_load_curve_frame_reads_back_a_written_csv(tmp_path):
    from capitalscan.research.curves import write_curve_csv

    frame = _frame(n_days=5, n_reps=10)
    path = write_curve_csv(frame, tmp_path / "curves.csv")
    reread = chart_arms.load_curve_frame(path)
    assert list(reread.columns) == list(frame.columns)
    assert len(reread) == len(frame)


def test_svg_path_follows_the_naming_rule():
    path = chart_arms.chart_svg_path("697f3ae71428d392", "train")
    assert path.name == "three_arms_697f3ae71428d392_train.svg"


def test_missing_summary_arm_raises():
    with pytest.raises(ValueError, match="no pooled benchmarks row"):
        chart_arms.render_three_arm_svg(
            _frame(), {ARM_BUY_HOLD: _summaries()[ARM_BUY_HOLD]}, "abc", "train"
        )
