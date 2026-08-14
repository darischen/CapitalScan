"""Session 14.4: DESIGN §6.12's volatility-scaled reachability ladder.

Covers `core.baselines.sigma_5d` (the pure scaling arithmetic) and
`research.reachability` (the per-event target/reached derivation and the
per-cell fraction). No database access anywhere in this file — the report-
time query in `research.reachability.load_events_for_reachability` is
exercised only against the live database by hand (see the session report),
never inside this suite (CLAUDE.md: unit tests never touch Postgres).
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.baselines import horizon_drift_vol_array, sigma_5d
from capitalscan.core.config import BaselineParams, StatsParams
from capitalscan.research import reachability as reach_mod
from capitalscan.research.reachability import (
    attach_scaled_targets,
    combined_reachability_table,
    fixed_reachability_by_cell,
    peak_ret_column,
    scaled_reachability_by_cell,
)

BP = BaselineParams()


class TestSigma5d:
    """Pure arithmetic (core/, invariant 1): no config reads, no IO."""

    def test_reuses_horizon_drift_vol_arrays_own_scale_factor(self):
        rv = np.array([0.16, 0.40, 0.64])
        _, expected = horizon_drift_vol_array(np.zeros_like(rv), rv, BP)
        got = sigma_5d(rv, BP)
        np.testing.assert_allclose(got, expected)

    def test_matches_design_worked_example(self):
        # DESIGN §6.2's own worked example: sigma_annual=0.40 -> sigma_5d ~= 5.6%.
        got = sigma_5d(np.array([0.40]), BP)
        assert abs(got[0] - 0.40 * np.sqrt(5 / 252)) < 1e-12
        assert abs(got[0] - 0.056) < 5e-4

    def test_double_volatility_doubles_sigma_5d(self):
        got = sigma_5d(np.array([0.20, 0.40]), BP)
        assert abs(got[1] - 2 * got[0]) < 1e-12

    def test_null_propagates_never_substituted(self):
        got = sigma_5d(np.array([0.30, np.nan]), BP)
        assert not np.isnan(got[0])
        assert np.isnan(got[1])

    def test_derived_from_horizon_days_not_hardcoded(self):
        bp10 = BaselineParams(horizon_days=10)
        rv = np.array([0.40])
        s5 = sigma_5d(rv, BP)
        s10 = sigma_5d(rv, bp10)
        # sqrt(10/252) != sqrt(5/252); a hardcoded 5-day divisor would make
        # these equal.
        assert abs(s10[0] - rv[0] * np.sqrt(10 / 252)) < 1e-12
        assert s10[0] != pytest.approx(s5[0])


class TestPeakRetColumn:
    def test_default_horizon_maps_to_peak_ret_5d(self):
        assert peak_ret_column(BP) == "peak_ret_5d"

    def test_unsupported_horizon_raises_rather_than_guessing(self):
        with pytest.raises(ValueError):
            peak_ret_column(BaselineParams(horizon_days=7))


def _events(rows: list[dict]) -> pd.DataFrame:
    base = {
        "signal_type": "bb_lower_touch",
        "side": "long",
        "dd_bucket": "0-10",
        "touched_2pct": False,
        "touched_3pct": False,
        "touched_5pct": False,
        "touched_10pct": False,
        "rv_20d": 0.30,
        "peak_ret_5d": 0.0,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


class TestAttachScaledTargets:
    def test_double_volatility_gets_double_absolute_target(self):
        events = _events(
            [
                {"rv_20d": 0.20, "peak_ret_5d": 0.10},
                {"rv_20d": 0.40, "peak_ret_5d": 0.10},
            ]
        )
        out = attach_scaled_targets(events, BP)
        t_lo = out.loc[0, "target_1.0sigma"]
        t_hi = out.loc[1, "target_1.0sigma"]
        assert abs(t_hi - 2 * t_lo) < 1e-12

    def test_null_rv20d_yields_null_target_never_substituted(self):
        events = _events([{"rv_20d": np.nan, "peak_ret_5d": 0.10}])
        out = attach_scaled_targets(events, BP)
        assert pd.isna(out.loc[0, "sigma_5d"])
        assert pd.isna(out.loc[0, "target_1.0sigma"])
        # never coerced to False - a null input must yield a null flag.
        assert out.loc[0, "reached_1.0sigma"] is pd.NA

    def test_scaled_and_fixed_agree_when_sigma_equals_fixed_target(self):
        # Choose rv_20d so sigma_5d works out to exactly 0.05 (touched_5pct's
        # own fixed target), and set peak_ret_5d just over it.
        target_fraction = 5 / BP.trading_days_per_year
        rv = 0.05 / np.sqrt(target_fraction)
        events = _events(
            [
                {"rv_20d": rv, "peak_ret_5d": 0.06, "touched_5pct": True},
                {"rv_20d": rv, "peak_ret_5d": 0.04, "touched_5pct": False},
            ]
        )
        out = attach_scaled_targets(events, BP)
        assert abs(out.loc[0, "sigma_5d"] - 0.05) < 1e-9
        assert bool(out.loc[0, "reached_1.0sigma"]) == out.loc[0, "touched_5pct"]
        assert bool(out.loc[1, "reached_1.0sigma"]) == out.loc[1, "touched_5pct"]

    def test_missing_rv20d_column_raises(self):
        events = _events([{}]).drop(columns=["rv_20d"])
        with pytest.raises(ValueError):
            attach_scaled_targets(events, BP)


class TestPerCellFractions:
    def test_scaled_fraction_is_mean_of_reached_dropping_nulls(self):
        events = _events(
            [
                {"rv_20d": 0.30, "peak_ret_5d": 1.0},  # reaches everything
                {"rv_20d": 0.30, "peak_ret_5d": -1.0},  # reaches nothing
                {"rv_20d": np.nan, "peak_ret_5d": 1.0},  # excluded, not a miss
            ]
        )
        out = attach_scaled_targets(events, BP)
        table = scaled_reachability_by_cell(out)
        row = table.loc[table["level_sigma"] == 1.0].iloc[0]
        assert row["n_obs"] == 2
        assert abs(row["fraction_reached"] - 0.5) < 1e-12

    def test_fixed_fraction_reads_stored_touched_columns(self):
        events = _events(
            [
                {"touched_2pct": True},
                {"touched_2pct": False},
            ]
        )
        table = fixed_reachability_by_cell(events, StatsParams())
        row = table.loc[table["target_pct"] == 0.02].iloc[0]
        assert row["n_obs"] == 2
        assert abs(row["fraction_reached"] - 0.5) < 1e-12


class TestNotInFDRFamily:
    """Session 14.4 acceptance: the scaled ladder is a diagnostic and must
    never enter the Benjamini-Hochberg family (DESIGN §6.12, session doc §2).
    """

    def test_combined_table_carries_no_p_or_q_value_column(self):
        events = _events(
            [
                {"rv_20d": 0.30, "peak_ret_5d": 0.10, "touched_2pct": True},
                {"rv_20d": 0.30, "peak_ret_5d": -0.10, "touched_2pct": False},
            ]
        )
        out = attach_scaled_targets(events, BP)
        table = combined_reachability_table(out, StatsParams())
        assert "p_value" not in table.columns
        assert "p_value_parametric" not in table.columns
        assert "q_value" not in table.columns
        assert set(table["ladder"].unique()) <= {"fixed", "scaled"}

    def test_module_never_references_benjamini_hochberg(self):
        # Structural guard: this module must not import or call the family
        # correction at all, not merely "not call it on this path."
        source = inspect.getsource(reach_mod)
        assert "benjamini_hochberg" not in source
