"""Session 11.4 acceptance: the statistical self-validation gate
(DESIGN §6.13, ADR 087).

These tests run the same functions `cscan stats self-validate` runs, at a
reduced replication count so the fast tier stays fast. The full-count run
and its recorded numbers live in `RESULTS.md`; what is pinned here is that
the gate's machinery behaves, not that a particular seed produced a
particular rate.

The load-bearing test in this file is `test_broken_variant_is_caught`. A
guard nobody has seen fail is not known to work, and every other assertion
here would still pass on a null test that could never fail.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import DEFAULT_BASELINE, StatsParams
from capitalscan.research.selfvalidation import (
    analytical_recovery_baseline,
    attach_outcomes,
    cell_statistics,
    confirm_broken_variant_fails,
    null_panel,
    run_null_test,
    run_recovery_test,
    synthetic_events,
)

# Three worlds is enough to exercise the replication path and keep the fast
# tier fast; the recorded gate result uses ten.
REPLICATIONS = 3
SEED = 20260811


@lru_cache(maxsize=4)
def _panel(seed: int) -> pd.DataFrame:
    """Generating 50 tickers x 2,500 days costs about a second, and half
    the tests in this file want the same panel. Cached rather than
    regenerated, which is safe only because the generator is pure and
    seeded — the property `TestNullTest::test_seeding_is_reproducible`
    pins."""
    return null_panel(seed)


@lru_cache(maxsize=2)
def _fixture(seed: int):
    """Events with outcomes, plus the ticker-year baselines they join to."""
    from capitalscan.research.baselines import ticker_year_baselines

    bars = _panel(seed)
    sp = StatsParams()
    events = attach_outcomes(synthetic_events(bars, seed), bars)
    baselines = ticker_year_baselines(bars, tuple(sp.reach_targets), DEFAULT_BASELINE)
    return events, baselines, sp


class TestNullTest:
    def test_null_test_passes_and_reports_its_rate(self):
        result = run_null_test(seed=SEED, replications=REPLICATIONS)
        # The gate, and the reason the rate is reported rather than a
        # boolean: a run at 4.9% and a run at 0.2% are different states of
        # the world even though both pass.
        assert result.passed
        assert result.rate <= result.threshold
        assert result.threshold == StatsParams().fdr_alpha
        # 12 cells x 4 ladder targets per world, less the cells with no
        # variance (every fire missed, mostly at 10%), which the shipped
        # writer suppresses and the null test skips (ADR 209).
        assert 0 < result.n_tests <= REPLICATIONS * 48
        assert 0.0 <= result.rate <= 1.0
        assert len(result.rate_by_replication) == REPLICATIONS

    def test_correction_is_calibrated_or_conservative(self):
        # ADR 209's test is one-sided, so calibration is the share of raw
        # p-values under alpha before any FDR correction: near alpha when
        # the month x cluster variance is the right size, well above it when
        # every published interval is too narrow. Measured 2026-11-18 at
        # three replications: 3.9% correct, 22.7% broken.
        result = run_null_test(seed=SEED, replications=REPLICATIONS)
        assert result.upper_tail_rate <= result.alpha
        # The uninflated estimator's z spread runs a little above 1 here
        # (IQR-based, 1.20 at three replications). Cells with three to ten
        # month blocks make the cluster-robust variance noisy, which is
        # part of why ADR 209 inflates by a placebo factor. The bound
        # catches a variance gone wrong by a factor, not that.
        assert result.z_sd < 1.5

    def test_broken_variant_is_caught(self):
        # Session gate item 3. Standard errors on `n` instead of `n_eff`
        # (the iid variance in place of month x cluster) ignore the
        # clustering entirely; the null test must notice.
        correct, broken = confirm_broken_variant_fails(seed=SEED, replications=REPLICATIONS)
        assert correct.passed
        assert broken.rate > broken.threshold
        assert broken.upper_tail_rate > correct.upper_tail_rate
        assert broken.z_sd > correct.z_sd
        assert broken.min_p_value < correct.min_p_value

    def test_seeding_is_reproducible(self):
        first = run_null_test(seed=SEED, replications=1)
        second = run_null_test(seed=SEED, replications=1)
        pd.testing.assert_frame_equal(first.cells, second.cells)
        assert (first.rate, first.rho_bar) == (second.rate, second.rho_bar)

    def test_a_different_seed_is_a_different_world(self):
        first = run_null_test(seed=SEED, replications=1)
        other = run_null_test(seed=SEED + 7, replications=1)
        # Each world skips its own degenerate cells, so compare on the
        # (cell, target) pairs both report.
        both = first.cells.merge(other.cells, on=["cell", "target_pct"])
        assert not np.allclose(both["p_hit_x"], both["p_hit_y"])

    def test_rejects_zero_replications(self):
        with pytest.raises(ValueError):
            run_null_test(replications=0)


class TestNullIsActuallyNull:
    """The events must be independent of every future return, by construction."""

    def test_event_generation_never_reads_a_price(self):
        # Same calendar, same tickers, completely different prices: if the
        # generator conditioned on anything about the path, the event set
        # would move. It does not, which is what makes the panel a null.
        bars = _panel(SEED)
        scrambled = bars.copy()
        rng = np.random.default_rng(0)
        for column in ("open", "high", "low", "close", "adj_close"):
            scrambled[column] = rng.permutation(scrambled[column].to_numpy())

        first = synthetic_events(bars, SEED)
        second = synthetic_events(scrambled, SEED)
        pd.testing.assert_frame_equal(first, second)

    def test_events_leave_room_for_the_forward_window(self):
        # BUILD.md's "filter on window completeness, always": an event whose
        # forward window has not closed carries no outcome, and counting it
        # as a miss would fabricate a negative edge.
        bars = _panel(SEED)
        events = attach_outcomes(synthetic_events(bars, SEED), bars)
        assert events["fwd_ret"].notna().all()

    def test_cells_are_assigned_per_firing_day(self):
        # The clustering the n_eff correction exists to handle only reaches
        # a cell if co-firing names land in it together.
        events = synthetic_events(_panel(SEED), SEED)
        per_day = events.groupby("signal_date")["cell"].nunique()
        assert (per_day == 1).all()

    def test_outcomes_come_from_the_baseline_series(self):
        # Measuring the outcome on split-adjusted close while the baseline
        # reads total-return adj_close is the most effective way to
        # manufacture a fake edge. Hand-check one event against adj_close.
        bars = _panel(SEED)
        events = attach_outcomes(synthetic_events(bars, SEED), bars)
        row = events.iloc[0]
        series = bars[bars["ticker"] == row["ticker"]].sort_values("ts").reset_index(drop=True)
        position = int(series.index[series["ts"] == row["signal_date"]][0])
        horizon = DEFAULT_BASELINE.horizon_days
        expected = (
            series["adj_close"].iloc[position + horizon] / series["adj_close"].iloc[position] - 1.0
        )
        assert row["fwd_ret"] == pytest.approx(expected)


class TestCellStatistics:
    def test_family_is_cells_crossed_with_ladder_targets(self):
        # DESIGN §6.8: the family is all headline-grid cells across all
        # ladder targets for one config. Correcting inside each target
        # separately would under-correct by the number of targets.
        events, baselines, sp = _fixture(SEED)
        cells = cell_statistics(events, baselines, tuple(sp.reach_targets), sp)
        assert set(cells["target_pct"]) == set(sp.reach_targets)
        assert not cells.duplicated(["cell", "target_pct"]).any()
        # Cells with no variance (every fire missed) drop out, as the
        # shipped writer suppresses them (ADR 209), so the family is at
        # most, not exactly, cells x targets.
        assert len(cells) <= cells["cell"].nunique() * len(sp.reach_targets)
        assert set(cells.loc[cells["target_pct"] == sp.reach_targets[0], "cell"]) == set(range(12))

    def test_every_row_carries_n_eff_and_an_interval(self):
        # Invariant 8: every response carrying a probability carries n_eff
        # and a confidence interval.
        events, baselines, sp = _fixture(SEED)
        cells = cell_statistics(events, baselines, tuple(sp.reach_targets), sp)
        assert cells[["n_eff", "ci_lower", "ci_upper", "q_value"]].notna().all().all()
        assert (cells["ci_lower"] <= cells["ci_upper"]).all()
        # A robust `n_eff` may exceed `n` (negative within-month
        # correlation), so only positivity is pinned here.
        assert (cells["n_eff"] > 0).all()

    def test_q_values_never_fall_below_p_values(self):
        events, baselines, sp = _fixture(SEED)
        cells = cell_statistics(events, baselines, tuple(sp.reach_targets), sp)
        assert (cells["q_value"] >= cells["p_value"] - 1e-12).all()

    def test_cells_carry_month_blocks_and_clusters(self):
        # ADR 209's variance needs both groupings. A frame with one cluster
        # per fire and one block per month would still compute; this pins
        # that the generator's co-firing reaches the month blocks.
        events, baselines, sp = _fixture(SEED)
        cells = cell_statistics(events, baselines, tuple(sp.reach_targets), sp)
        assert (cells["n_time_blocks"] < cells["n"]).all()
        assert (cells["n_clusters"] <= cells["n"]).all()

    def test_matches_the_shipped_edge_test(self):
        # The p-value must be the one `research.cell_stats` stores:
        # `one_sided_p_from_edge` on the month x cluster variance of each
        # fire's edge, recomputed here for one cell from the primitives.
        from capitalscan.core.variance import (
            cluster_robust_variance,
            month_block,
            one_sided_p_from_edge,
        )
        from capitalscan.research.baselines import attach_event_baselines

        events, baselines, sp = _fixture(SEED)
        target = sp.reach_targets[0]
        cells = cell_statistics(events, baselines, (target,), sp)
        row = cells.iloc[0]
        joined = attach_event_baselines(events, baselines, target)
        group = joined.loc[joined["cell"] == row["cell"]].dropna(subset=["baseline_empirical"])
        d = (group["fwd_ret"] >= target).astype("float64").to_numpy() - group[
            "baseline_empirical"
        ].to_numpy(dtype="float64")
        cluster = pd.factorize(group["cluster_id"].astype(str))[0]
        v_d = cluster_robust_variance(d, month_block(group["signal_date"]), cluster)[0]
        assert row["p_value"] == pytest.approx(one_sided_p_from_edge(float(d.mean()), v_d))

    def test_events_carry_the_real_cluster_rule(self):
        # `cluster_id` comes from `jobs.compute._tag_clusters`: one ticker's
        # fires within `max_hold_days` calendar days share a cluster.
        events = synthetic_events(_panel(SEED), SEED)
        assert events["cluster_id"].notna().all()
        assert events["cluster_id"].nunique() <= len(events)
        per_cluster = events.groupby("cluster_id")["ticker"].nunique()
        assert (per_cluster == 1).all()

    def test_raw_n_variant_reports_smaller_standard_errors(self):
        events, baselines, sp = _fixture(SEED)
        correct = cell_statistics(events, baselines, tuple(sp.reach_targets), sp)
        broken = cell_statistics(
            events, baselines, tuple(sp.reach_targets), sp, broken_se_on_raw_n=True
        )
        # Cell by cell the month x cluster SE may come out below the iid one
        # (negative within-month correlation, `n_eff > n`), so the pin is
        # on the bulk: the iid SE is smaller in most cells and at the median.
        assert (broken["se"] < correct["se"]).mean() > 0.75
        assert broken["se"].median() < correct["se"].median()
        assert broken["p_value"].min() < correct["p_value"].min()


class TestRecoveryTest:
    def test_recovers_the_injected_drift(self):
        result = run_recovery_test(seed=SEED + 1)
        assert result.passed
        assert result.gap_pct_points <= 1.0
        assert result.n_ticker_years > 0

    def test_analytical_target_carries_the_ito_correction(self):
        # The generator draws drift in log space, so a 30% log drift is a
        # 38% arithmetic drift at 40% vol. Asserting against DESIGN §6.2's
        # uncorrected 40.1% would pass by about half a point of luck.
        from capitalscan.core.baselines import parametric_baseline
        from capitalscan.research.selfvalidation import (
            RECOVERY_MU_ANNUAL,
            RECOVERY_SIGMA_ANNUAL,
            RECOVERY_TARGET,
        )

        uncorrected = parametric_baseline(
            RECOVERY_TARGET, RECOVERY_MU_ANNUAL, RECOVERY_SIGMA_ANNUAL, DEFAULT_BASELINE
        )
        analytical = analytical_recovery_baseline()
        assert abs(uncorrected - 0.401) < 0.001  # DESIGN §6.2's worked example
        assert analytical > uncorrected
        assert abs(analytical - uncorrected) < 0.02

    def test_reproducible(self):
        first = run_recovery_test(seed=SEED + 1)
        second = run_recovery_test(seed=SEED + 1)
        assert first.measured == second.measured
