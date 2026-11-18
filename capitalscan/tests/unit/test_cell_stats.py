"""Session 12.3 and 12.4: the `cell_stats` writer's aggregation contract.

Everything here runs on synthetic frames. The measurement against real
events lives in `research/cell_stats.py::run_cell_stats` and is recorded in
`RESULTS.md`; what these tests pin is the arithmetic that turns a set of
events into a row, and the five consumer rules the session brief lists,
each of which is a silent-wrong-number failure rather than a crash.
"""

from __future__ import annotations

import contextlib

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.cells import CellSpec
from capitalscan.core.config import Config, StatsParams, VarianceParams
from capitalscan.research import cell_stats
from capitalscan.research.cell_stats import (
    apply_benjamini_hochberg,
    cell_exit_mix,
    cell_n_eff,
    compute_grid,
    hit_flags,
    pooled_rho,
    select_grid_events,
)

ERAS = ("2010-2014", "2015-2019", "2020-2023")


def _rho_era(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"era": list(values), "rho_empirical": [float(v) for v in values.values()]})


def _events(n: int, **overrides) -> pd.DataFrame:
    """A minimal event frame with every column the writer reads."""
    rng = np.random.default_rng(7)
    base = {
        "ticker": [f"T{i % 5}" for i in range(n)],
        "signal_date": pd.bdate_range("2015-01-05", periods=n),
        "signal_type": "bb_lower_touch",
        "side": "long",
        "dd_bucket": "0-10",
        "era": "2015-2019",
        "split_key": "train",
        "entry_kind": "next_open",
        "is_cluster_head": True,
        "cluster_id": [f"c{i}" for i in range(n)],
        "fwd_window_days": 11,
        "cofire_count": 1,
        "fwd_ret_5d": rng.normal(0.01, 0.03, n),
        "net_ret": rng.normal(0.008, 0.03, n),
        "mfe": np.abs(rng.normal(0.03, 0.01, n)),
        "mae": -np.abs(rng.normal(0.02, 0.01, n)),
        "time_to_mfe": rng.integers(1, 6, n),
        "capture_ratio": rng.uniform(0, 1, n),
        "exit_reason": "timeout",
        "earnings_in_window": False,
        "touched_2pct": True,
        "day_touched_2pct": 2,
        "touched_3pct": True,
        "day_touched_3pct": 3,
        "touched_5pct": False,
        "day_touched_5pct": None,
        "touched_10pct": False,
        "day_touched_10pct": None,
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestPooledRho:
    """ADR 102: a pooled cell weights each era's `rho_empirical` by that
    era's event count, which is what makes the pooled number reproduce the
    published table."""

    def test_single_era_returns_that_eras_rho(self):
        events = _events(10, era="2015-2019")
        assert pooled_rho(events, _rho_era({"2015-2019": 0.36})) == pytest.approx(0.36)

    def test_weighting_is_by_event_count_not_by_era_count(self):
        events = _events(100, era=["2010-2014"] * 90 + ["2020-2023"] * 10)
        rho = pooled_rho(events, _rho_era({"2010-2014": 0.42, "2020-2023": 0.47}))
        # Event-weighted: 0.9*0.42 + 0.1*0.47 = 0.425. A plain mean over the
        # two era rows would give 0.445 and quietly overstate the correction.
        assert rho == pytest.approx(0.425)
        assert rho != pytest.approx(0.445)

    def test_missing_era_row_raises_rather_than_defaulting(self):
        """A missing `rho_era` row must not silently become 0, which would
        set the correction to nothing and hand back `n_eff == n`."""
        events = _events(10, era="2015-2019")
        with pytest.raises(ValueError, match="rho_era"):
            pooled_rho(events, _rho_era({"2010-2014": 0.42}))


class TestEffectiveSampleSize:
    def test_matches_the_design_formula(self):
        events = _events(100, cofire_count=11, era="2015-2019")
        k_bar, rho_bar, n_eff = cell_n_eff(events, _rho_era({"2015-2019": 0.4}))
        assert k_bar == pytest.approx(11.0)
        assert rho_bar == pytest.approx(0.4)
        assert n_eff == pytest.approx(100 / (1 + 10 * 0.4))

    def test_no_cofiring_leaves_n_untouched(self):
        events = _events(50, cofire_count=1, era="2015-2019")
        _, _, n_eff = cell_n_eff(events, _rho_era({"2015-2019": 0.4}))
        assert n_eff == pytest.approx(50.0)

    def test_negative_measured_rho_is_clamped_not_passed_through(self):
        """`rho_bar_for_correction` exists so a noisy negative measurement
        cannot produce `n_eff > n`, which is the unsafe direction."""
        events = _events(50, cofire_count=10, era="2015-2019")
        _, _, n_eff = cell_n_eff(events, _rho_era({"2015-2019": -0.05}))
        assert n_eff == pytest.approx(50.0)


class TestHitFlagsAreSideAware:
    def test_long_hits_on_a_rise(self):
        events = _events(3, side="long", fwd_ret_5d=[0.05, 0.01, -0.05])
        assert hit_flags(events, target=0.02).tolist() == [True, False, False]

    def test_short_hits_on_a_fall(self):
        """A short event wins when the price drops. Reusing the long
        comparison here would report the short cells' edge with the sign
        reversed and every number would still look plausible."""
        events = _events(3, side="short", fwd_ret_5d=[0.05, 0.01, -0.05])
        assert hit_flags(events, target=0.02).tolist() == [False, False, True]

    def test_a_mixed_side_frame_is_rejected(self):
        """Side is a grid dimension, so a cell is single-sided by
        construction. A mixed frame means the caller grouped wrong."""
        events = _events(2, side=["long", "short"])
        with pytest.raises(ValueError, match="side"):
            hit_flags(events, target=0.02)

    def test_null_forward_return_is_not_a_miss(self):
        """Invariant 4: a null propagates. Counting it as False would
        deflate `p_hit` by the null rate and never show up as an error."""
        events = _events(3, fwd_ret_5d=[0.05, None, 0.05])
        flags = hit_flags(events, target=0.02)
        assert flags.isna().sum() == 1
        assert flags.dropna().tolist() == [True, True]


class TestGridEventSelection:
    def test_null_dd_bucket_events_are_excluded_and_counted(self):
        """`cell_key` coalesces a null `dd_bucket` to `'all'`, so an
        unfiltered null merges into an aggregate cell instead of dropping.
        This fixture is built so inclusion would move the cell's n."""
        events = pd.concat(
            [_events(20, dd_bucket="0-10"), _events(5, dd_bucket=None)], ignore_index=True
        )
        selected, report = select_grid_events(events, StatsParams())
        assert len(selected) == 20
        assert report.excluded_null_dd == 5

    def test_deep_drawdown_buckets_are_excluded_and_counted(self):
        events = pd.concat(
            [
                _events(20, dd_bucket="0-10"),
                _events(7, dd_bucket="20-35"),
                _events(3, dd_bucket="35+"),
            ],
            ignore_index=True,
        )
        selected, report = select_grid_events(events, StatsParams())
        assert len(selected) == 20
        assert report.excluded_deep_dd == 10

    def test_exclusions_are_stated_not_silent(self):
        events = pd.concat(
            [_events(10, dd_bucket="0-10"), _events(4, dd_bucket=None)], ignore_index=True
        )
        _, report = select_grid_events(events, StatsParams())
        assert "4" in report.summary()
        assert "dd_bucket" in report.summary()

    def test_unclosed_forward_windows_are_excluded(self):
        """An event whose forward window has not closed carries frozen
        labels. Today's live events are exactly this case."""
        events = pd.concat(
            [_events(15, fwd_window_days=11), _events(6, fwd_window_days=2)], ignore_index=True
        )
        selected, report = select_grid_events(events, StatsParams(), min_fwd_window=6)
        assert len(selected) == 15
        assert report.excluded_open_window == 6

    def test_null_forward_window_is_excluded(self):
        events = pd.concat(
            [_events(15, fwd_window_days=11), _events(6, fwd_window_days=None)], ignore_index=True
        )
        selected, _ = select_grid_events(events, StatsParams(), min_fwd_window=6)
        assert len(selected) == 15


class TestExitMix:
    def test_fractions_sum_to_one(self):
        events = _events(10, exit_reason=["timeout"] * 6 + ["target"] * 3 + ["stop"])
        mix = cell_exit_mix(events)
        assert sum(mix.values()) == pytest.approx(1.0)

    def test_fractions_match_a_hand_count(self):
        events = _events(10, exit_reason=["timeout"] * 6 + ["target"] * 3 + ["stop"])
        mix = cell_exit_mix(events)
        assert mix["timeout"] == pytest.approx(0.6)
        assert mix["target"] == pytest.approx(0.3)
        assert mix["stop"] == pytest.approx(0.1)

    def test_absent_reasons_are_omitted_rather_than_zeroed(self):
        events = _events(4, exit_reason="target")
        assert cell_exit_mix(events) == {"target": 1.0}

    def test_tied_counts_break_deterministically(self):
        """`value_counts` does not order ties stably, so two runs over the
        same data produced `{'timeout', 'stop'}` in different orders. The
        mapping was identical and `jsonb` normalizes key order, so nothing
        stored was ever wrong — but a frame that differs run to run makes
        the determinism check report a difference that is not one.

        Observed live: `confluence_high|short|10-20` in 2015-2019, where
        `timeout` and `stop` both land on 0.3125.
        """
        tied = _events(4, exit_reason=["timeout", "stop", "target", "target"])
        assert list(cell_exit_mix(tied)) == ["target", "stop", "timeout"]

    def test_ordering_is_by_descending_share_then_name(self):
        events = _events(6, exit_reason=["stop"] * 3 + ["target", "timeout", "upper_band"])
        assert list(cell_exit_mix(events)) == ["stop", "target", "timeout", "upper_band"]


class TestBenjaminiHochberg:
    def test_family_is_cells_times_targets(self):
        """DESIGN §6.8: all headline cells across all ladder targets for
        one config. Twelve cells times four targets is 48 tests."""
        rows = pd.DataFrame(
            {
                "p_value_parametric": np.linspace(0.001, 0.9, 48),
                "suppressed": False,
                "era": None,
            }
        )
        out = apply_benjamini_hochberg(rows, StatsParams())
        assert out["q_value"].notna().sum() == 48

    def test_q_is_never_below_p(self):
        rows = pd.DataFrame(
            {
                "p_value_parametric": np.linspace(0.001, 0.9, 48),
                "suppressed": False,
                "era": None,
            }
        )
        out = apply_benjamini_hochberg(rows, StatsParams())
        assert (out["q_value"] >= out["p_value_parametric"] - 1e-12).all()

    def test_q_values_are_monotone_in_sorted_p_order(self):
        rng = np.random.default_rng(3)
        rows = pd.DataFrame(
            {"p_value_parametric": rng.uniform(0, 1, 48), "suppressed": False, "era": None}
        )
        out = apply_benjamini_hochberg(rows, StatsParams()).sort_values("p_value_parametric")
        assert out["q_value"].is_monotonic_increasing

    def test_suppressed_cells_carry_no_q_value(self):
        """A suppressed cell renders no number, and a q-value is a number."""
        rows = pd.DataFrame(
            {
                "p_value_parametric": [0.01, 0.02, None],
                "suppressed": [False, False, True],
                "era": [None, None, None],
            }
        )
        out = apply_benjamini_hochberg(rows, StatsParams())
        assert pd.isna(out.loc[2, "q_value"])

    def test_suppressed_cells_do_not_enlarge_the_family(self):
        """Including them would inflate `m` and weaken every q-value in the
        family, which is a correctness bug disguised as conservatism."""
        rendered = pd.DataFrame(
            {"p_value_parametric": [0.01, 0.02], "suppressed": False, "era": None}
        )
        padded = pd.DataFrame(
            {
                "p_value_parametric": [0.01, 0.02, None, None],
                "suppressed": [False, False, True, True],
                "era": None,
            }
        )
        a = apply_benjamini_hochberg(rendered, StatsParams())["q_value"].tolist()
        b = apply_benjamini_hochberg(padded, StatsParams())["q_value"].dropna().tolist()
        assert a == pytest.approx(b)

    def test_era_rows_are_descriptive_and_enter_no_family(self):
        """ADR 103 and ADR 099: era and breadth rows carry no `q_value` and
        do not enter the correction. Letting them in would quadruple `m`."""
        rows = pd.DataFrame(
            {
                "p_value_parametric": [0.01, 0.02, 0.03],
                "suppressed": False,
                "era": [None, "2015-2019", "2020-2023"],
            }
        )
        out = apply_benjamini_hochberg(rows, StatsParams())
        assert out.loc[0, "q_value"] == pytest.approx(0.01)  # family of one
        assert pd.isna(out.loc[1, "q_value"])
        assert pd.isna(out.loc[2, "q_value"])


class TestHoldoutEraIsRefusedStructurally:
    """12.4 acceptance: a test must assert no row carries era 2024+, **and
    it must fail if the exclusion is removed**.

    That is only possible if the exclusion lives in code. Leaving it to the
    caller's `eras` argument means nothing to remove and nothing to catch.
    """

    def test_compute_grid_refuses_the_holdout_era(self):
        events = _events(50, era="2015-2019")
        with pytest.raises(ValueError, match="holdout"):
            compute_grid(
                events,
                _rho_era({"2015-2019": 0.4}),
                Config(),
                era="2024+",
                specs=(CellSpec(side="long", signal_type="bb_lower_touch", dd_bucket="0-10"),),
            )

    def test_a_reported_era_is_accepted(self):
        """The control. Without it, a `compute_grid` that refused *every*
        era would pass the test above."""
        events = _events(50, era="2015-2019")
        grid = compute_grid(
            events,
            _rho_era({"2015-2019": 0.4}),
            Config(),
            era="2015-2019",
            specs=(CellSpec(side="long", signal_type="bb_lower_touch", dd_bucket="0-10"),),
        )
        assert (grid["era"] == "2015-2019").all()

    def test_era_rows_carry_no_q_value(self):
        """ADR 103: descriptive, entering no test family."""
        events = _events(50, era="2015-2019")
        grid = compute_grid(
            events,
            _rho_era({"2015-2019": 0.4}),
            Config(),
            era="2015-2019",
            specs=(CellSpec(side="long", signal_type="bb_lower_touch", dd_bucket="0-10"),),
        )
        assert grid["q_value"].isna().all()


SPEC = CellSpec(side="long", signal_type="bb_lower_touch", dd_bucket="0-10")


def _monthly(n_months: int, per_month: int, **overrides) -> pd.DataFrame:
    """`per_month` fires in each of `n_months` months, one cluster per fire.

    Every fire in a month shares its outcome: alternate months rise and
    fall 5%, so `p_hit` is 0.5 at the 2%, 3% and 5% targets and 0 at 10%.
    The month block is then the whole dependence, and `n_eff` is the
    number of months.
    """
    months = pd.date_range("2012-01-01", periods=n_months, freq="MS")
    dates = [m + pd.Timedelta(days=d) for m in months for d in range(per_month)]
    rets = [0.05 if i % 2 == 0 else -0.05 for i in range(n_months) for _ in range(per_month)]
    overrides.setdefault("fwd_ret_5d", rets)
    return _events(n_months * per_month, signal_date=dates, **overrides)


def _row(grid: pd.DataFrame, target: float) -> pd.Series:
    return grid.loc[np.isclose(grid["target_pct"], target)].iloc[0]


class TestRobustVarianceRow:
    """ADR 209: the row's `n_eff`, interval and suppression come from the
    month x cluster variance on the hit flags, not from ADR 098."""

    def test_n_eff_is_robust(self):
        """600 fires, 10 per month perfectly correlated within the month:
        60 independent observations. ADR 098 with `cofire_count = 1` would
        have reported 600."""
        grid = compute_grid(_monthly(60, 10), None, Config(), specs=(SPEC,))
        row = _row(grid, 0.03)
        assert row["n_events"] == 600
        assert row["n_eff"] == pytest.approx(60.0, rel=0.10)
        assert row["p_hit"] == pytest.approx(0.5)

    def test_independent_fires_keep_their_count(self):
        """One fire per month and per cluster: no shared dependence, so the
        robust `n_eff` is exactly `n`."""
        grid = compute_grid(_monthly(40, 1), None, Config(), specs=(SPEC,))
        assert _row(grid, 0.02)["n_eff"] == pytest.approx(40.0)

    def test_inflation_widens_interval(self):
        events = _monthly(60, 10)
        base = _row(compute_grid(events, None, Config(), specs=(SPEC,)), 0.03)
        wide = _row(compute_grid(events, None, Config(), specs=(SPEC,), inflation=1.2), 0.03)
        width = base["ci_high"] - base["ci_low"]
        wide_width = wide["ci_high"] - wide["ci_low"]
        assert wide_width / width == pytest.approx(1.2, abs=1e-9)
        assert wide["n_eff"] / base["n_eff"] == pytest.approx(1 / 1.44)

    def test_interval_is_wald_on_the_robust_variance(self):
        row = _row(compute_grid(_monthly(60, 10), None, Config(), specs=(SPEC,)), 0.03)
        half = 1.959963984540054 * np.sqrt(0.25 / row["n_eff"])
        assert row["ci_low"] == pytest.approx(0.5 - half)
        assert row["ci_high"] == pytest.approx(0.5 + half)

    def test_all_miss_cell_suppressed(self):
        """At the 10% target every fire missed: the variance is zero and
        `n_eff` undefined. The cell suppresses with a reason, no raise."""
        row = _row(compute_grid(_monthly(60, 10), None, Config(), specs=(SPEC,)), 0.10)
        assert row["suppressed"]
        assert row["suppress_reason"] == "no variance: every fire missed"
        assert row["n_eff"] == 0.0
        for column in ("p_hit", "edge", "ci_low", "ci_high", "p_value_parametric"):
            assert pd.isna(row[column]), f"{column} rendered on a suppressed cell"

    def test_all_hit_cell_suppressed(self):
        events = _monthly(60, 2, fwd_ret_5d=0.2)
        row = _row(compute_grid(events, None, Config(), specs=(SPEC,)), 0.10)
        assert row["suppressed"]
        assert row["suppress_reason"] == "no variance: every fire hit"

    def test_null_outcomes_are_dropped_before_the_variance(self):
        """Invariant 4: a null flag is no measurement. It must not count as a
        miss, nor as a fire in the month and cluster counts."""
        nulled = _monthly(40, 1)
        nulled.loc[0, "fwd_ret_5d"] = None
        row = _row(compute_grid(nulled, None, Config(), specs=(SPEC,)), 0.02)
        assert row["n_events"] == 40
        assert row["n_time_blocks"] == 39
        assert row["n_eff"] == pytest.approx(39.0)

    def test_every_fire_counted(self):
        """`n_events` counts fires, not cluster heads (ADR 209)."""
        events = _monthly(60, 10)
        events["is_cluster_head"] = [i % 10 == 0 for i in range(len(events))]
        row = _row(compute_grid(events, None, Config(), specs=(SPEC,)), 0.03)
        assert row["n_events"] == 600

    def test_provenance_columns(self):
        grid = compute_grid(_monthly(60, 10), None, Config(), specs=(SPEC,), inflation=1.18)
        row = _row(grid, 0.03)
        assert row["variance_method"] == "cgm_month_cluster"
        assert row["n_time_blocks"] == 60
        assert row["n_clusters"] == 600
        assert row["se_inflation"] == pytest.approx(1.18)

    def test_mean_cofire_is_still_stored(self):
        grid = compute_grid(_monthly(10, 3, cofire_count=7), None, Config(), specs=(SPEC,))
        assert _row(grid, 0.02)["mean_cofire"] == pytest.approx(7.0)


class _Stop(Exception):
    pass


class _FakeEngine:
    def connect(self):
        return contextlib.nullcontext(None)


def _capture_sql(monkeypatch, module) -> list[str]:
    sqls: list[str] = []

    def fake_read_sql(statement, conn, params=None):
        sqls.append(str(statement))
        return pd.DataFrame({"signal_date": pd.Series([], dtype="datetime64[ns]")})

    monkeypatch.setattr(module.pd, "read_sql", fake_read_sql)
    return sqls


class TestEventPopulation:
    def test_cell_stats_loads_every_fire(self, monkeypatch):
        """`run_cell_stats` asks `load_grid_events` for every fire."""
        seen: dict = {}

        def fake_load(engine, config_hash, split_key, **kwargs):
            seen.update(kwargs)
            raise _Stop

        monkeypatch.setattr(cell_stats, "load_grid_events", fake_load)
        with pytest.raises(_Stop):
            cell_stats.run_cell_stats(None, "h", "train")
        assert seen["cluster_heads_only"] is False

    def test_grid_sql_filters_heads_only_when_asked(self, monkeypatch):
        sqls = _capture_sql(monkeypatch, cell_stats)
        cell_stats.load_grid_events(_FakeEngine(), "h", "train", cluster_heads_only=False)
        cell_stats.load_grid_events(_FakeEngine(), "h", "train", cluster_heads_only=True)
        assert "AND is_cluster_head" not in sqls[0]
        assert "cluster_id" in sqls[0]
        assert "AND is_cluster_head" in sqls[1]

    def test_benchmarks_keep_heads(self, monkeypatch):
        """`benchmarks` keeps ADR 102's head filter (a BACKLOG follow-up)."""
        from capitalscan.core.types import Side
        from capitalscan.research import benchmarks

        sqls = _capture_sql(monkeypatch, benchmarks)
        benchmarks.load_signal_events(_FakeEngine(), "h", "train", Side.LONG)
        assert "AND is_cluster_head" in sqls[0]


class TestPanelBounds:
    def test_validate_panel_covers_the_placebo_window_and_stops_before_holdout(self):
        dates = pd.to_datetime(["2022-03-01", "2022-06-01", "2023-11-01"])
        start, end = cell_stats.panel_bounds(
            _events(3, signal_date=dates), Config(), VarianceParams(), 5
        )
        assert start == "2010-01-04"
        assert end == "2024-01-31"

    def test_train_panel_reaches_the_placebo_end(self):
        dates = pd.to_datetime(["2011-03-01", "2015-06-01", "2021-11-01"])
        start, end = cell_stats.panel_bounds(
            _events(3, signal_date=dates), Config(), VarianceParams(), 5
        )
        assert start == "2009-01-01"
        assert end == "2023-12-31"


class TestSuppressedCellRow:
    def test_suppressed_cell_returns_nulls_for_every_rendered_number(self):
        """Ten independent fires: `n_eff` 10, under the floor of 30."""
        row = _row(compute_grid(_monthly(10, 1), None, Config(), specs=(SPEC,)), 0.02)
        assert row["suppressed"]
        assert row["suppress_reason"].startswith("n_eff 10.0 below")
        for column in ("p_hit", "edge", "ci_low", "ci_high"):
            assert pd.isna(row[column]), f"{column} rendered on a suppressed cell"

    def test_suppressed_cell_still_reports_its_counts(self):
        """`n_events` and `n_eff` are how a reader sees *why* it
        suppressed. Nulling them too would make the row unreadable."""
        row = _row(compute_grid(_monthly(10, 1), None, Config(), specs=(SPEC,)), 0.02)
        assert row["n_events"] == 10
        assert row["n_eff"] > 0
