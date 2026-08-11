"""Session 11.3 acceptance: `rho_bar` per era (ADR 098, DESIGN §6.3).

The two estimators are checked differently on purpose. The empirical one is
verified against arithmetic done in the test — correlations from
`numpy.corrcoef`, weights counted by hand — because its weighting rule is
the part ADR 098 argues for and the part a plausible-looking implementation
gets wrong. The factor-implied one is verified against a closed form on data
generated from the model it assumes, because there the right answer is known
exactly.

The direction of the bias between them is generated, not assumed. ADR 098
treats the factor value as a diagnostic precisely because it omits residual
co-movement and therefore understates `rho_bar`; `TestFactorImplied`
constructs residual co-movement and confirms the gap comes out positive.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.stats import effective_sample_size, rho_bar_for_correction
from capitalscan.research.rho import (
    RHO_ERA_COLUMNS,
    cofire_days,
    cofire_pair_days,
    compute_rho_eras,
    empirical_rho_bar,
    factor_betas,
    factor_implied_rho_bar,
    overlapping_horizon_returns,
    rho_era_rows,
    rho_for_era,
)
from capitalscan.research.synthetic import (
    analytical_pair_correlation,
    single_factor_bars,
)

DATES = pd.bdate_range("2015-01-05", periods=200, freq="C")


def _events(rows: list[tuple[str, int]]) -> pd.DataFrame:
    """`(ticker, day_index)` pairs as an events frame, one signal type."""
    return pd.DataFrame(
        {
            "ticker": [t for t, _ in rows],
            "signal_date": [DATES[d] for _, d in rows],
            "signal_type": "CONFLUENCE_LOW",
        }
    )


def _correlated_returns(seed: int = 98) -> pd.DataFrame:
    """Four tickers with deliberately unequal pairwise correlations.

    A and B share almost everything; C shares a little; D moves against the
    other three. Built from explicit weights on a common factor so the
    structure is readable, then measured — never assumed — by the tests.
    """
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 1, len(DATES))
    return pd.DataFrame(
        {
            "AAA": 0.95 * common + 0.31 * rng.normal(0, 1, len(DATES)),
            "BBB": 0.93 * common + 0.37 * rng.normal(0, 1, len(DATES)),
            "CCC": 0.15 * common + 0.99 * rng.normal(0, 1, len(DATES)),
            "DDD": -0.90 * common + 0.44 * rng.normal(0, 1, len(DATES)),
        },
        index=DATES,
    )


class TestCofirePairDays:
    def test_counts_distinct_tickers_per_signal_day(self):
        rows = [("AAA", 0), ("BBB", 0), ("CCC", 0), ("AAA", 1), ("BBB", 1)]
        counts = cofire_pair_days(_events(rows))
        assert counts[("AAA", "BBB")] == 2
        assert counts[("AAA", "CCC")] == 1
        assert counts[("BBB", "CCC")] == 1

    def test_multiple_rows_per_ticker_per_day_collapse(self):
        # One event per entry kind is still one co-firing, matching
        # `add_cofire_count`'s distinct-ticker rule.
        events = _events([("AAA", 0), ("AAA", 0), ("BBB", 0), ("BBB", 0)])
        assert cofire_pair_days(events)[("AAA", "BBB")] == 1

    def test_different_signal_types_do_not_co_fire(self):
        events = _events([("AAA", 0), ("BBB", 0)])
        events.loc[1, "signal_type"] = "CONFLUENCE_HIGH"
        assert cofire_pair_days(events) == {}

    def test_solo_days_produce_no_pairs(self):
        assert cofire_pair_days(_events([("AAA", 0), ("BBB", 1)])) == {}


class TestCofireDays:
    """`n_cofire_days` counts days, not pair-days."""

    def test_a_broad_day_counts_once(self):
        # Five names firing together is one co-firing day and ten pairs.
        # Storing the pair total in a column named `n_cofire_days` reads as
        # ten days of evidence where there is one.
        events = _events([(t, 0) for t in ("AAA", "BBB", "CCC", "DDD", "EEE")])
        assert cofire_days(events) == 1
        assert sum(cofire_pair_days(events).values()) == 10

    def test_solo_days_do_not_count(self):
        assert cofire_days(_events([("AAA", 0), ("BBB", 1)])) == 0

    def test_entry_kind_fan_out_collapses(self):
        events = _events([("AAA", 0), ("AAA", 0), ("BBB", 0), ("BBB", 0)])
        assert cofire_days(events) == 1

    def test_different_signal_types_are_not_co_firing(self):
        events = _events([("AAA", 0), ("BBB", 0)])
        events.loc[1, "signal_type"] = "CONFLUENCE_HIGH"
        assert cofire_days(events) == 0

    def test_counts_each_calendar_day_once_across_signal_types(self):
        # Two signal types both co-firing on one date is one day, not two.
        events = _events([("AAA", 0), ("BBB", 0), ("CCC", 0), ("DDD", 0)])
        events.loc[[2, 3], "signal_type"] = "CONFLUENCE_HIGH"
        assert cofire_days(events) == 1
        assert cofire_days(_events([("AAA", 0), ("BBB", 0), ("AAA", 1), ("BBB", 1)])) == 2


class TestEmpiricalRhoBar:
    """Hand-verified weighting on three tickers with known correlations."""

    def _pair_counts(self):
        # 100 days of AAA+BBB, 2 days of AAA+CCC, 1 day of BBB+CCC.
        rows = [(t, d) for d in range(0, 100) for t in ("AAA", "BBB")]
        rows += [(t, d) for d in (150, 151) for t in ("AAA", "CCC")]
        rows += [(t, 160) for t in ("BBB", "CCC")]
        return _events(rows)

    def test_weighted_mean_matches_hand_arithmetic(self):
        returns = _correlated_returns()
        events = self._pair_counts()
        counts = cofire_pair_days(events)
        assert (counts[("AAA", "BBB")], counts[("AAA", "CCC")], counts[("BBB", "CCC")]) == (
            100,
            2,
            1,
        )

        rho, _, _, pairs = empirical_rho_bar(returns, counts)

        # The spreadsheet: correlations from numpy, weights counted above.
        def corr(a: str, b: str) -> float:
            return float(np.corrcoef(returns[a].to_numpy(), returns[b].to_numpy())[0, 1])

        expected = (
            100 * corr("AAA", "BBB") + 2 * corr("AAA", "CCC") + 1 * corr("BBB", "CCC")
        ) / 103
        assert rho == pytest.approx(expected, abs=1e-12)
        assert pairs == [("AAA", "BBB"), ("AAA", "CCC"), ("BBB", "CCC")]

    def test_weighting_visibly_changes_the_answer(self):
        # ADR 098's argument in one assertion: a pair co-firing 100 times
        # contributes 100 times the clustering of a pair co-firing once, and
        # the unweighted mean gives them equal say.
        returns = _correlated_returns()
        counts = cofire_pair_days(self._pair_counts())
        rho, values, _, _ = empirical_rho_bar(returns, counts)
        unweighted = float(np.nanmean(values))
        assert abs(rho - unweighted) > 0.20

    def test_pairs_that_never_co_fired_are_excluded(self):
        # DDD moves hard against the other three and never co-fires. Its
        # pairs must not enter the mean.
        returns = _correlated_returns()
        events = self._pair_counts()
        solo = _events([("DDD", d) for d in range(170, 190)])
        with_solo = pd.concat([events, solo], ignore_index=True)

        base, _, _, base_pairs = empirical_rho_bar(returns, cofire_pair_days(events))
        withd, _, _, with_pairs = empirical_rho_bar(returns, cofire_pair_days(with_solo))
        assert with_pairs == base_pairs
        assert withd == pytest.approx(base)

        # Constructed so inclusion would move the result: DDD's correlations
        # are strongly negative, so had those pairs been admitted at any
        # meaningful weight the mean would fall.
        assert float(np.corrcoef(returns["AAA"], returns["DDD"])[0, 1]) < -0.7
        forced = cofire_pair_days(events)
        forced[("AAA", "DDD")] = 100
        moved, _, _, _ = empirical_rho_bar(returns, forced)
        assert moved < base - 0.3

    def test_thin_overlap_pairs_drop_out(self):
        # Two shared observations can return exactly 1.0 by coincidence.
        returns = _correlated_returns().copy()
        returns.loc[DATES[3:], "CCC"] = np.nan
        counts = cofire_pair_days(self._pair_counts())
        rho, values, _, pairs = empirical_rho_bar(returns, counts, min_overlap=30)
        ccc_positions = [i for i, p in enumerate(pairs) if "CCC" in p]
        assert all(np.isnan(values[i]) for i in ccc_positions)
        assert rho == pytest.approx(
            float(np.corrcoef(returns["AAA"], returns["BBB"])[0, 1]), abs=1e-12
        )

    def test_no_co_firing_yields_null(self):
        rho, values, weights, pairs = empirical_rho_bar(
            _correlated_returns(), cofire_pair_days(_events([("AAA", 0)]))
        )
        assert rho is None
        assert pairs == []


class TestFactorImplied:
    """Recovers the analytical value; understates it when residuals co-move."""

    BETAS = (0.6, 0.9, 1.2, 1.5)
    SIGMA_M = 0.16
    SIGMA_E = 0.20

    def _panel(self, resid_rho: float, seed: int = 20260810):
        bars, market_close = single_factor_bars(
            betas=self.BETAS,
            n_days=2000,
            sigma_market_annual=self.SIGMA_M,
            sigma_resid_annual=self.SIGMA_E,
            resid_rho=resid_rho,
            seed=seed,
        )
        returns = overlapping_horizon_returns(bars, 5)
        market = (market_close.shift(-5) / market_close - 1.0).reindex(returns.index)
        return returns, market

    def _all_pairs(self, returns: pd.DataFrame):
        names = list(returns.columns)
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1 :]]
        return pairs, np.ones(len(pairs))

    def test_reproduces_the_analytical_value_at_zero_residual_correlation(self):
        returns, market = self._panel(resid_rho=0.0)
        pairs, weights = self._all_pairs(returns)
        betas = factor_betas(returns, market)
        rho_factor, mean_beta = factor_implied_rho_bar(betas, pairs, weights)

        analytical = float(
            np.mean(
                [
                    analytical_pair_correlation(
                        self.BETAS[list(returns.columns).index(a)],
                        self.BETAS[list(returns.columns).index(b)],
                        self.SIGMA_M,
                        self.SIGMA_E,
                        resid_rho=0.0,
                    )
                    for a, b in pairs
                ]
            )
        )
        # Tolerance of 0.03 on a correlation estimated from ~2,000
        # overlapping 5-day windows. Overlap makes the effective sample far
        # smaller than the row count, so a tighter bound would be asserting
        # the seed rather than the estimator.
        assert rho_factor == pytest.approx(analytical, abs=0.03)
        assert mean_beta == pytest.approx(float(np.mean(self.BETAS)), abs=0.10)

    def test_recovers_the_known_betas(self):
        returns, market = self._panel(resid_rho=0.0)
        betas = factor_betas(returns, market)
        for ticker, expected in zip(returns.columns, self.BETAS):
            assert float(betas.at[ticker, "beta"]) == pytest.approx(expected, abs=0.10)

    def test_every_quantity_comes_from_one_sample(self):
        """A ticker observed on half the days must not borrow the other
        half's market variance.

        `rho_ij = beta_i beta_j sigma_m^2 / (sigma_i sigma_j)` is only a
        correlation if all four quantities describe one sample. Measuring
        `sigma_m` on the full index while `beta_i` and `sigma_i` come from a
        ticker's own observed days mixes three samples, and on real `bars` —
        where names list and delist mid-era — the three genuinely differ.
        Here SYN000 is observed only where the market is calm, so a
        full-index `sigma_m` overstates its systematic share and pushes its
        implied correlations up.
        """
        returns, market = self._panel(resid_rho=0.0)
        calm = market.abs() < market.abs().median()
        partial = returns.copy()
        partial.loc[~calm, partial.columns[0]] = np.nan

        betas = factor_betas(partial, market)
        # sigma_m is per ticker, and the truncated one genuinely differs.
        sigma_m = betas["sigma_m"]
        assert sigma_m.iloc[0] < sigma_m.iloc[1]
        assert sigma_m.iloc[1:].std() == pytest.approx(0.0, abs=1e-12)

        # And the implied correlation stays a correlation despite the mix.
        pairs, weights = self._all_pairs(partial)
        rho_factor, _ = factor_implied_rho_bar(betas, pairs, weights)
        assert rho_factor is not None
        assert -1.0 <= rho_factor <= 1.0

    def test_a_ticker_below_min_overlap_drops_out_rather_than_poisoning(self):
        returns, market = self._panel(resid_rho=0.0)
        partial = returns.copy()
        partial.iloc[10:, 0] = np.nan  # ten observations, well under the floor
        betas = factor_betas(partial, market)
        assert np.isnan(betas["beta"].iloc[0])
        assert not betas["beta"].iloc[1:].isna().any()

    def test_correlated_residuals_make_rho_gap_positive(self):
        # ADR 098's stated direction: the factor version assumes residual
        # independence, so sector co-movement lands entirely in the
        # empirical estimate and the gap opens upward.
        returns, market = self._panel(resid_rho=0.5)
        pairs, weights = self._all_pairs(returns)
        rho_emp, _, _, _ = empirical_rho_bar(returns, {p: 1 for p in pairs})
        rho_factor, _ = factor_implied_rho_bar(factor_betas(returns, market), pairs, weights)

        assert rho_emp is not None and rho_factor is not None
        assert rho_emp - rho_factor > 0.10

    def test_zero_residual_correlation_leaves_almost_no_gap(self):
        returns, market = self._panel(resid_rho=0.0)
        pairs, weights = self._all_pairs(returns)
        rho_emp, _, _, _ = empirical_rho_bar(returns, {p: 1 for p in pairs})
        rho_factor, _ = factor_implied_rho_bar(factor_betas(returns, market), pairs, weights)
        assert abs(rho_emp - rho_factor) < 0.05

    def test_missing_market_series_yields_null_diagnostic_only(self):
        # The diagnostic is optional; its absence must not block the value
        # `n_eff` actually consumes.
        returns = _correlated_returns()
        events = _events([(t, d) for d in range(0, 60) for t in ("AAA", "BBB")])
        estimate = rho_for_era("2015-2019", events, returns, None)
        assert estimate.rho_factor_implied is None
        assert estimate.rho_gap is None
        assert estimate.mean_beta is None
        assert estimate.rho_empirical is not None


class TestEffectiveSampleSize:
    """`n_eff <= n` always; equality exactly at `k_bar = 1` or `rho_bar = 0`."""

    def test_worked_values(self):
        # 1,000 events, mean co-fire 5, rho_bar 0.25:
        # n_eff = 1000 / (1 + 4 * 0.25) = 500. Half the sample gone, which
        # is the size of the effect a wrong rho_bar moves.
        assert effective_sample_size(1000, 5.0, 0.25) == pytest.approx(500.0)
        assert effective_sample_size(1000, 12.0, 1.0) == pytest.approx(1000 / 12)

    def test_equality_conditions(self):
        assert effective_sample_size(500, 1.0, 0.9) == pytest.approx(500.0)
        assert effective_sample_size(500, 20.0, 0.0) == pytest.approx(500.0)

    def test_rejects_inadmissible_inputs(self):
        with pytest.raises(ValueError):
            effective_sample_size(-1, 2.0, 0.2)
        with pytest.raises(ValueError):
            effective_sample_size(100, 0.5, 0.2)
        with pytest.raises(ValueError):
            effective_sample_size(100, 2.0, -0.01)
        with pytest.raises(ValueError):
            effective_sample_size(100, 2.0, 1.01)

    def test_negative_measurement_clamps_to_no_correction(self):
        # Estimation noise around zero, not evidence that clustering adds
        # information. Clamping to zero applies no correction, which is the
        # conservative reading; passing it through would return n_eff > n.
        assert rho_bar_for_correction(-0.02) == 0.0
        assert rho_bar_for_correction(0.31) == pytest.approx(0.31)
        with pytest.raises(ValueError):
            rho_bar_for_correction(1.5)


class TestEraAggregation:
    def _era_events(self):
        rows = []
        for day in range(0, 60):
            rows += [("AAA", day), ("BBB", day)]
        events = _events(rows)
        events["era"] = "2015-2019"
        second = _events(
            [("AAA", d) for d in range(100, 140)] + [("CCC", d) for d in range(100, 140)]
        )
        second["era"] = "2020-2023"
        return pd.concat([events, second], ignore_index=True)

    def test_one_estimate_per_era(self):
        estimates = compute_rho_eras(self._era_events(), _correlated_returns())
        assert [e.era for e in estimates] == ["2015-2019", "2020-2023"]
        assert all(e.rho_empirical is not None for e in estimates)

    def test_requires_the_era_column(self):
        with pytest.raises(ValueError):
            compute_rho_eras(_events([("AAA", 0)]), _correlated_returns())

    def test_two_runs_agree_ignoring_run_id_and_timestamp(self):
        events, returns = self._era_events(), _correlated_returns()
        stamp = datetime(2026, 8, 10, tzinfo=timezone.utc)
        first = rho_era_rows(compute_rho_eras(events, returns), "run-a", "hash1", "sha1", stamp)
        second = rho_era_rows(compute_rho_eras(events, returns), "run-b", "hash1", "sha2", stamp)
        measured = [
            "era",
            "rho_empirical",
            "rho_factor_implied",
            "rho_gap",
            "n_pairs",
            "n_cofire_days",
        ]
        pd.testing.assert_frame_equal(first[measured], second[measured])

    def test_a_second_config_adds_rows_rather_than_replacing(self):
        events, returns = self._era_events(), _correlated_returns()
        estimates = compute_rho_eras(events, returns)
        stamp = datetime(2026, 8, 10, tzinfo=timezone.utc)
        rows = pd.concat(
            [
                rho_era_rows(estimates, "run-a", "hash1", "sha", stamp),
                rho_era_rows(estimates, "run-a", "hash2", "sha", stamp),
            ],
            ignore_index=True,
        )
        # `(era, config_hash)` is the key: two configs, four rows, no
        # collision. One snapshot per era would have made the second write
        # overwrite the first.
        assert len(rows) == 4
        assert rows.duplicated(subset=["era", "config_hash"]).sum() == 0

    def test_row_shape_matches_the_table(self):
        rows = rho_era_rows(
            compute_rho_eras(self._era_events(), _correlated_returns()), "r", "h", "s"
        )
        assert list(rows.columns) == RHO_ERA_COLUMNS
        assert rows["run_id"].eq("r").all()
        assert rows["git_sha"].eq("s").all()

    def test_empty_estimates_keep_the_shape(self):
        assert list(rho_era_rows([], "r", "h", "s").columns) == RHO_ERA_COLUMNS

    def test_an_era_with_no_measurable_rho_writes_no_row(self):
        """`rho_era.rho_empirical` is NOT NULL, and this is what enforces it.

        An era whose pairs all fall under `min_overlap` measures `None`.
        Passing that through raised an IntegrityError at write time instead
        of the skip the migration documents, and a null in that column would
        anyway read as "computed and found nothing", which is a different
        claim from "not computed".
        """
        events = self._era_events()
        # Two co-firing days: real pairs, nowhere near `min_overlap`
        # observations, so the correlation is not estimable.
        thin = _events([("AAA", 0), ("BBB", 0), ("AAA", 1), ("BBB", 1)])
        thin["era"] = "2024+"
        estimates = compute_rho_eras(
            pd.concat([events, thin], ignore_index=True), _correlated_returns()
        )
        assert [e.era for e in estimates] == ["2015-2019", "2020-2023", "2024+"]
        assert estimates[-1].rho_empirical is None

        rows = rho_era_rows(estimates, "r", "h", "s")
        assert list(rows["era"]) == ["2015-2019", "2020-2023"]
        assert rows["rho_empirical"].notna().all()
