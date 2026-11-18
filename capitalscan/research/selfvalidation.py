"""Session 11.4, the session gate: does the statistics layer report nothing
when there is nothing to report? (DESIGN §6.13, ADR 087.)

Two tests, one entry point (`cscan stats self-validate`).

**Null test.** Driftless synthetic prices, synthetic events placed
independently of every future return, the real 11.1-11.3 pipeline run over
them end to end. The fraction of cells reaching `q < 0.05` must not exceed
5%. A random walk contains no edge; if the layer finds one, the layer has a
bug and every real result Phase 4 later produces is suspect.

**Recovery test.** Known drift in, and the computed parametric baseline must
match the analytical value within 1 percentage point. The null test alone
would pass on a layer that reports nothing ever, which is why the pair is
the gate rather than the null test on its own.

Three rules this module follows, all from the session spec:

- **The real code path, not a parallel one.** `cell_statistics` below calls
  `research.baselines`, `research.backtest.add_cofire_count`,
  `jobs.compute._tag_clusters` for `cluster_id`, and the functions
  `research.cell_stats` ships with since ADR 209: `core.variance`'s
  `cluster_robust_variance` (calendar month x `cluster_id`),
  `robust_n_eff`, `wald_interval` and `one_sided_p_from_edge`, then
  `core.stats.benjamini_hochberg`. A test exercising a reimplementation
  proves nothing about the implementation that ships. Until ADR 209 this
  module tested ADR 098's `k_bar` correction and a Wilson interval, which
  no longer build any stored row.
- **Seeded and reproducible.** Two runs produce identical output. Every
  generator here takes its seed from the caller.
- **Report the rate, not pass or fail.** A run at 4.9% and a run at 0.2% are
  different states of the world, and the first deserves investigation even
  though it passes.

**On `broken_se_on_raw_n`.** A guard nobody has seen fail is not known to
work, so the null test carries a switch that reintroduces a specific, real
bug: standard errors on `n` rather than `n_eff`. On the ADR 209 path that
means the iid variance `var(y) / n` in place of the month x cluster one,
which ignores every co-firing name sharing the market factor. `confirm_broken_variant_fails`
runs it and asserts the null test catches it. The switch exists only for
that confirmation and is never reachable from the passing path.

**Inflation is 1.0 here.** ADR 209 widens each shipped scope by a factor a
placebo measures on real bars. The synthetic null has no placebo, and the
gate asks whether the estimator itself is calibrated, so nothing inflates.

**On the synthetic panel.** DESIGN §6.13 says driftless GBM. This module
uses `research.synthetic.single_factor_bars` with `mu_annual = 0`, which is
still driftless GBM per ticker — each log return is `N(0, sigma^2)` — but
with a shared market factor, so co-firing tickers genuinely share
information. That matters: on **independent** tickers the `n_eff` correction
has nothing to correct, the broken variant behaves identically to the
correct one, and the null test cannot fail no matter what is wrong with it.
A null that no bug can break is not a guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from capitalscan.core.baselines import event_weighted_baseline
from capitalscan.core.config import (
    DEFAULT_BASELINE,
    BaselineParams,
    ExitParams,
    StatsParams,
    VarianceParams,
)
from capitalscan.core.stats import benjamini_hochberg
from capitalscan.core.variance import (
    cluster_robust_variance,
    inflate,
    month_block,
    one_sided_p_from_edge,
    robust_n_eff,
    wald_interval,
)
from capitalscan.jobs.compute import _tag_clusters
from capitalscan.research.backtest import add_cofire_count
from capitalscan.research.baselines import attach_event_baselines, ticker_year_baselines
from capitalscan.research.cell_stats import ci_z
from capitalscan.research.rho import (
    cofire_pair_days,
    empirical_rho_bar,
    overlapping_horizon_returns,
)
from capitalscan.research.synthetic import single_factor_bars

# The null panel. Fifty tickers over 2,500 days is DESIGN §6.13's
# specification; the betas spread around 1.0 so the factor structure is
# heterogeneous rather than every name moving identically.
#
# Market volatility of 0.30 against residual 0.18 puts the pairwise
# correlation of 5-day returns near 0.7. Index constituents run 0.3-0.5 in
# calm periods and higher under stress, and this signal fires on drawdowns,
# so this is the stressed end of the real range.
#
# Stated plainly because it looks like a tuning knob: the **correct**
# pipeline passes at every setting tried. What the level buys is a null
# test that can *fail*. Under ADR 098's two-sided z-test, 0.22/0.22 gave
# the broken variant 11.7% and was the setting. ADR 209's test is
# one-sided (`one_sided_p_from_edge`), which counts one tail of the same
# overconfidence, and at 0.22/0.22 the broken variant fell to 3.8% (seed
# 20260811) and 2.6% (seed 777) at ten replications: under the 5%
# threshold, so the guard no longer caught the bug. At 0.30/0.18 it reads
# 10.7% and 9.6% on those seeds, while the correct path reads 0.24% and
# 0.0% (measured 2026-11-18). The parameter was chosen for the
# sensitivity of the guard, never to move the result the guard reports.
NULL_N_TICKERS = 50
NULL_N_DAYS = 2500
NULL_SIGMA_MARKET = 0.30
NULL_SIGMA_RESID = 0.18
NULL_BETA_LOW = 0.6
NULL_BETA_HIGH = 1.4

# Event generation. `firing_rate` is the fraction of days on which the
# signal fires at all; `breadth_*` bound how many names fire together on
# such a day, which is what produces the co-firing structure `k_bar`
# measures. Both are shaped to resemble a real firing population rather
# than to make the test easy: a 4% firing rate with 3-15 names co-firing is
# the regime ADR 099 describes.
NULL_FIRING_RATE = 0.04
NULL_BREADTH_LOW = 3
NULL_BREADTH_HIGH = 15
NULL_N_CELLS = 12  # ADR 011's twelve-cell headline grid

# The recovery panel. DESIGN §6.2's worked example parameters, so the
# analytical answer is one already written down.
RECOVERY_MU_ANNUAL = 0.30
RECOVERY_SIGMA_ANNUAL = 0.40
RECOVERY_TARGET = 0.02

_SIGNAL_TYPE = "SYNTHETIC_NULL"
_ERA = "synthetic"
_SIDE = "long"

# ADR 209's inflation factor. The synthetic null has no placebo (see the
# module docstring), so the estimator runs uninflated.
NULL_INFLATION = 1.0

# Event generation draws from `seed + _EVENT_SEED_OFFSET`. The offset is
# large because `single_factor_bars` derives ticker `i` from `seed + 1 + i`:
# at the old offset of 1, the event generator and ticker SYN000's
# idiosyncratic returns opened the *same* generator state. Nothing read a
# price either way — `test_event_generation_never_reads_a_price` pins that by
# scrambling the panel — but a null test whose entire claim is independence
# by construction should not have its event placement and one ticker's price
# path derived from one bit stream. 500,000 clears the 50-ticker span with
# room and stays clear of the 1,000-per-replication spacing in
# `run_null_test`.
_EVENT_SEED_OFFSET = 500_000


@dataclass(frozen=True)
class NullTestResult:
    """The null test's full state, not a boolean.

    `rate` against `threshold` is the gate; everything else is what a human
    needs to tell "passed with room" from "passed by a hair".
    """

    n_tests: int
    n_significant: int
    rate: float
    threshold: float
    alpha: float
    replications: int
    rho_bar: float
    mean_k_bar: float
    mean_n: float
    mean_n_eff: float
    z_sd: float
    upper_tail_rate: float
    min_p_value: float
    seed: int
    broken_se_on_raw_n: bool
    cells: pd.DataFrame = field(repr=False)

    @property
    def passed(self) -> bool:
        return self.rate <= self.threshold

    @property
    def rate_by_replication(self) -> list[float]:
        """Per-replication rates, so a pooled 2% built from one bad
        replication and nine clean ones is visible as such."""
        if self.cells.empty:
            return []
        grouped = self.cells.groupby("replication")["q_value"]
        return [float((q < self.alpha).mean()) for _, q in grouped]


@dataclass(frozen=True)
class RecoveryTestResult:
    analytical: float
    measured: float
    gap_pct_points: float
    tolerance_pct_points: float
    n_ticker_years: int
    seed: int

    @property
    def passed(self) -> bool:
        return self.gap_pct_points <= self.tolerance_pct_points


def null_panel(seed: int = 20260811) -> pd.DataFrame:
    """Driftless correlated GBM: 50 tickers, 2,500 days.

    Betas are spread deterministically across `[0.6, 1.4]` rather than
    drawn, so the panel depends on `seed` through the price process alone
    and a reader can see exactly what went in.
    """
    betas = tuple(np.linspace(NULL_BETA_LOW, NULL_BETA_HIGH, NULL_N_TICKERS))
    bars, _ = single_factor_bars(
        betas=betas,
        n_days=NULL_N_DAYS,
        sigma_market_annual=NULL_SIGMA_MARKET,
        sigma_resid_annual=NULL_SIGMA_RESID,
        resid_rho=0.0,
        mu_annual=0.0,
        seed=seed,
    )
    return bars


def synthetic_events(
    bars: pd.DataFrame,
    seed: int = 20260811,
    n_cells: int = NULL_N_CELLS,
) -> pd.DataFrame:
    """Events placed independently of every future return.

    This independence is the whole null. Firing days are drawn from the
    calendar and the names firing on each are drawn from the universe, both
    without reading a single price. No conditioning variable a signal could
    key on has any relationship to what happens next, so a layer reporting
    an edge here is reporting a bug.

    **The cell is assigned per firing day, not per event**, and that choice
    is load-bearing. The headline grid's dimensions are drawdown bucket and
    signal strength, both driven largely by market-wide moves, so names
    co-firing on one day mostly land in the same cell. Assigning cells
    independently per event instead scatters each day's co-firing group
    across twelve cells, leaving almost no clustering *within* a cell — and
    then `n_eff` corrects for dependence that is not there, the correct
    pipeline runs conservative, and the broken variant looks calibrated.
    Measured: per-event assignment gives the broken variant a z-score
    standard deviation of 1.01 and zero rejections, so the null test cannot
    catch it. Per-day assignment gives 1.70 and eight rejections out of 48.
    The realistic construction is also the one with teeth.
    """
    rng = np.random.default_rng(seed + _EVENT_SEED_OFFSET)
    calendar = np.array(sorted(pd.to_datetime(bars["ts"].unique())))
    tickers = np.array(sorted(bars["ticker"].unique()))

    # Firing days must leave room for the forward window to close, so the
    # tail of the calendar is excluded — the same "filter on window
    # completeness" rule BUILD.md requires of every Phase 4 consumer.
    usable = calendar[: -DEFAULT_BASELINE.horizon_days]
    n_firing = int(len(usable) * NULL_FIRING_RATE)
    firing_days = rng.choice(usable, size=n_firing, replace=False)

    rows = []
    for day in np.sort(firing_days):
        breadth = int(rng.integers(NULL_BREADTH_LOW, NULL_BREADTH_HIGH + 1))
        cell = int(rng.integers(0, n_cells))
        for ticker in rng.choice(tickers, size=breadth, replace=False):
            rows.append({"ticker": str(ticker), "signal_date": pd.Timestamp(day), "cell": cell})

    events = pd.DataFrame(rows)
    events["signal_type"] = _SIGNAL_TYPE
    events["era"] = _ERA
    events["side"] = _SIDE
    return add_cofire_count(_with_clusters(events))


def _with_clusters(events: pd.DataFrame) -> pd.DataFrame:
    """`cluster_id` exactly as `run_events` assigns it: one (ticker, side)
    run of fires whose gaps stay within `ExitParams.max_hold_days`.

    The real `jobs.compute._tag_clusters` does the tagging, so the ADR 209
    variance clusters synthetic fires by the rule it clusters real ones by.
    It sorts and mutates its input records, so it gets copies, and the
    original row order is restored afterwards.
    """
    records = [{**rec, "_row": i} for i, rec in enumerate(events.to_dict("records"))]
    tagged = _tag_clusters(records, ExitParams().max_hold_days)
    order = sorted(tagged, key=lambda rec: rec["_row"])
    out = events.copy()
    out["cluster_id"] = [rec["cluster_id"] for rec in order]
    return out


def attach_outcomes(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    bp: BaselineParams = DEFAULT_BASELINE,
) -> pd.DataFrame:
    """Each event's realized forward return, from the same series the
    baseline is measured on (`core.returns.forward_returns` on `adj_close`).

    Measuring the outcome on one series and the baseline on another is the
    single most effective way to manufacture a fake edge, which is why
    `overlapping_horizon_returns` is reused here rather than a second
    forward-return computation written locally.
    """
    wide = overlapping_horizon_returns(bars, bp.horizon_days)
    long = wide.reset_index(names="signal_date").melt(
        id_vars="signal_date", var_name="ticker", value_name="fwd_ret"
    )
    return events.merge(long, on=["ticker", "signal_date"], how="left")


def cell_statistics(
    events: pd.DataFrame,
    baselines: pd.DataFrame,
    targets: tuple[float, ...],
    sp: StatsParams,
    broken_se_on_raw_n: bool = False,
    vp: VarianceParams | None = None,
) -> pd.DataFrame:
    """One row per (cell, target): the shipped ADR 209 arithmetic.

    Test family, per DESIGN §6.8: every cell crossed with every ladder
    target, for one config. Benjamini-Hochberg runs once across that whole
    family, not per target. Correcting inside each target separately would
    under-correct by the number of targets.

    Per cell, as `research.cell_stats._cell_row` and `_edge_p_value` do it:
    the variance of `p_hit` is the month x `cluster_id` cluster-robust
    variance of the hit flags, `n_eff = p (1 - p) / V`, the interval is
    Wald at `vp.ci_level`, and the p-value is one-sided on each fire's
    edge `d = hit - baseline_empirical`, at the same variance of `mean(d)`.
    `NULL_INFLATION` (1.0) multiplies both standard errors.

    `broken_se_on_raw_n` swaps both variances for the iid `var / n`, which
    is the variance on the raw count. Nothing but
    `confirm_broken_variant_fails` passes True.
    """
    z_crit = ci_z(vp or VarianceParams())
    rows = []
    for target in targets:
        joined = attach_event_baselines(events, baselines, target)
        joined["hit"] = (joined["fwd_ret"] >= target).astype("float64")
        for cell, group in joined.groupby("cell", sort=True):
            usable = group.dropna(subset=["fwd_ret"])
            n = int(len(usable))
            if n == 0:
                continue
            baseline, n_used, n_null = event_weighted_baseline(usable["baseline_empirical"])
            if baseline is None:
                continue

            y = usable["hit"].to_numpy(dtype="float64")
            p_hit = float(y.mean())
            block = month_block(usable["signal_date"])
            cluster = pd.factorize(usable["cluster_id"].astype(str))[0].astype(np.int64)
            v = inflate(_variance(y, block, cluster, broken_se_on_raw_n), NULL_INFLATION)
            n_eff = robust_n_eff(p_hit, v)

            # The edge test reads only fires with a baseline (invariant 4),
            # as `_edge_p_value` does.
            has_base = usable["baseline_empirical"].notna().to_numpy()
            d = y[has_base] - usable["baseline_empirical"].to_numpy(dtype="float64")[has_base]
            v_d = inflate(
                _variance(d, block[has_base], cluster[has_base], broken_se_on_raw_n),
                NULL_INFLATION,
            )
            p_value = one_sided_p_from_edge(float(d.mean()), v_d)

            # A degenerate variance (every fire hit, or every fire missed)
            # carries no test. The shipped writer suppresses such a cell;
            # here it is skipped and counted by absence.
            if n_eff is None or p_value is None:
                continue
            se = float(np.sqrt(v_d))
            lower, upper = wald_interval(p_hit, v, z_crit)

            rows.append(
                {
                    "cell": int(str(cell)),
                    "target_pct": float(target),
                    "n": n,
                    "n_eff": float(n_eff),
                    "k_bar": float(usable["cofire_count"].mean()),
                    "n_time_blocks": int(np.unique(block).size),
                    "n_clusters": int(np.unique(cluster).size),
                    "p_hit": p_hit,
                    "baseline": baseline,
                    "edge": p_hit - baseline,
                    "se": se,
                    "z": float(d.mean()) / se,
                    "p_value": p_value,
                    "ci_lower": lower,
                    "ci_upper": upper,
                    "n_baseline_used": n_used,
                    "n_null_baseline": n_null,
                }
            )

    frame = pd.DataFrame(rows)
    if frame.empty:
        frame["q_value"] = []
        return frame
    _, q_values = benjamini_hochberg(frame["p_value"].to_numpy(), sp.fdr_alpha)
    frame["q_value"] = q_values
    return frame


def _variance(y: np.ndarray, block: np.ndarray, cluster: np.ndarray, broken: bool) -> float:
    """The month x cluster variance of `mean(y)`, or, broken, the iid one."""
    if broken:
        return float(np.var(y)) / len(y)
    return cluster_robust_variance(y, block, cluster)[0]


def _one_replication(
    seed: int,
    sp: StatsParams,
    bp: BaselineParams,
    broken_se_on_raw_n: bool,
) -> tuple[pd.DataFrame, float]:
    """One synthetic world: panel, events, baselines, rho_bar, cells, BH."""
    bars = null_panel(seed)
    events = attach_outcomes(synthetic_events(bars, seed), bars, bp)
    baselines = ticker_year_baselines(bars, tuple(sp.reach_targets), bp)

    returns_wide = overlapping_horizon_returns(bars, bp.horizon_days)
    measured_rho, _, _, _ = empirical_rho_bar(returns_wide, cofire_pair_days(events))
    rho_bar = 0.0 if measured_rho is None else float(measured_rho)

    cells = cell_statistics(events, baselines, tuple(sp.reach_targets), sp, broken_se_on_raw_n)
    return cells, rho_bar


def run_null_test(
    seed: int = 20260811,
    sp: StatsParams | None = None,
    bp: BaselineParams = DEFAULT_BASELINE,
    broken_se_on_raw_n: bool = False,
    replications: int = 10,
) -> NullTestResult:
    """The gate. Full pipeline over data that provably contains nothing.

    The threshold is `StatsParams.fdr_alpha`, not a literal 5% written here:
    the rate being bounded by alpha is what Benjamini-Hochberg claims, so
    the two must move together (invariant 9).

    **Why replications.** One synthetic world yields 12 cells x 4 ladder
    targets = 48 tests, so the finest rate it can express is 1/48 = 2.1%.
    Against a 5% threshold that is two significant figures of resolution,
    and "0 of 48" cannot distinguish a layer that is correct from one that
    is broken in a way costing 1% of cells. Ten independent worlds give 480
    tests and 0.2% resolution. Benjamini-Hochberg still runs **per
    replication**, because the test family DESIGN §6.8 defines is one
    config's cells and pooling ten worlds into one family would change the
    procedure under test rather than measure it.

    Seeds are `seed + 1000 * r`, spaced widely enough that no two
    replications share a ticker's price path (`single_factor_bars` derives
    ticker `i` from `seed + 1 + i` over at most 50 tickers) and that no
    replication's event draw (`seed + _EVENT_SEED_OFFSET`) lands on another
    replication's price stream.
    """
    sp = sp or StatsParams()
    if replications < 1:
        raise ValueError(f"replications must be at least 1, got {replications}")

    frames = []
    rhos = []
    for r in range(replications):
        cells, rho_bar = _one_replication(seed + 1000 * r, sp, bp, broken_se_on_raw_n)
        cells = cells.assign(replication=r, rho_bar=rho_bar)
        frames.append(cells)
        rhos.append(rho_bar)

    cells = pd.concat(frames, ignore_index=True)
    n_tests = int(len(cells))
    n_significant = int((cells["q_value"] < sp.fdr_alpha).sum()) if n_tests else 0

    return NullTestResult(
        n_tests=n_tests,
        n_significant=n_significant,
        rate=float(n_significant / n_tests) if n_tests else 0.0,
        threshold=sp.fdr_alpha,
        alpha=sp.fdr_alpha,
        replications=replications,
        rho_bar=float(np.mean(rhos)),
        mean_k_bar=float(cells["k_bar"].mean()) if n_tests else 0.0,
        mean_n=float(cells["n"].mean()) if n_tests else 0.0,
        mean_n_eff=float(cells["n_eff"].mean()) if n_tests else 0.0,
        # Under a correct variance the z-scores are near standard normal, so
        # a z_sd near 1 says the layer is calibrated and above 1 says
        # intervals are too narrow. Measured on the interquartile range
        # (IQR over a normal's IQR), not the plain SD: a cell with three to five month
        # blocks gives a heavy-tailed z, and a handful of such cells
        # dominate a plain SD in both directions (ADR 209 path, 2026-11-18:
        # plain SD about 2 for the correct and the broken variant alike).
        z_sd=_iqr_sd(cells["z"]) if n_tests > 1 else 0.0,
        # The share of cells whose raw one-sided p clears alpha before any
        # FDR correction. A calibrated one-sided test holds it near alpha.
        upper_tail_rate=float((cells["p_value"] < sp.fdr_alpha).mean()) if n_tests else 0.0,
        min_p_value=float(cells["p_value"].min()) if n_tests else 1.0,
        seed=seed,
        broken_se_on_raw_n=broken_se_on_raw_n,
        cells=cells,
    )


def _iqr_sd(values: pd.Series) -> float:
    """The normal-equivalent SD from the interquartile range."""
    # A standard normal's IQR is 2 * Phi^-1(0.75), about 1.349.
    return float(
        (values.quantile(0.75) - values.quantile(0.25)) / (2.0 * scipy_stats.norm.ppf(0.75))
    )


def analytical_recovery_baseline(bp: BaselineParams = DEFAULT_BASELINE) -> float:
    """The value the recovery test must reproduce.

    Two corrections separate this from DESIGN §6.2's headline 40.1%, and
    both are properties of the generator rather than of the estimator:

    1. `research.synthetic` draws drift in **log** space, so a log drift of
       `mu` produces an arithmetic drift of `mu + sigma^2 / 2` (Ito). The
       baseline estimator fits simple returns, so the drift it should
       recover is the arithmetic one.
    2. The estimator's volatility is the standard deviation of simple daily
       returns, which for small daily moves is `sigma` to well inside the
       tolerance here.

    Stating this explicitly is the point. Asserting against the uncorrected
    40.1% would pass by roughly half a point of luck, and a test that passes
    for a reason nobody wrote down is not evidence.
    """
    from capitalscan.core.baselines import parametric_baseline

    arithmetic_mu = RECOVERY_MU_ANNUAL + RECOVERY_SIGMA_ANNUAL**2 / 2.0
    return parametric_baseline(RECOVERY_TARGET, arithmetic_mu, RECOVERY_SIGMA_ANNUAL, bp)


def run_recovery_test(
    seed: int = 20260812,
    bp: BaselineParams = DEFAULT_BASELINE,
    n_tickers: int = 30,
    n_days: int = 2000,
    tolerance_pct_points: float = 1.0,
) -> RecoveryTestResult:
    """Known drift in; the computed parametric baseline must come back out.

    Beta is zero for every ticker, so this panel is pure independent GBM
    with the stated drift and volatility — the null test needs the factor
    structure, this one needs an unambiguous set of true parameters.
    """
    bars, _ = single_factor_bars(
        betas=tuple([0.0] * n_tickers),
        n_days=n_days,
        sigma_market_annual=RECOVERY_SIGMA_ANNUAL,  # unused at beta 0, must stay positive
        sigma_resid_annual=RECOVERY_SIGMA_ANNUAL,
        resid_rho=0.0,
        mu_annual=RECOVERY_MU_ANNUAL,
        seed=seed,
    )
    baselines = ticker_year_baselines(bars, (RECOVERY_TARGET,), bp)
    scored = baselines["baseline_parametric"].dropna()

    analytical = analytical_recovery_baseline(bp)
    measured = float(scored.mean())
    return RecoveryTestResult(
        analytical=analytical,
        measured=measured,
        gap_pct_points=abs(measured - analytical) * 100.0,
        tolerance_pct_points=tolerance_pct_points,
        n_ticker_years=int(scored.size),
        seed=seed,
    )


def confirm_broken_variant_fails(
    seed: int = 20260811,
    sp: StatsParams | None = None,
    replications: int = 10,
) -> tuple[NullTestResult, NullTestResult]:
    """Run the null test correct and broken, and return both.

    Session gate item 3. The broken variant computes standard errors on the
    raw event count, the iid `var / n`, so it ignores the clustering
    entirely: co-firing names on a shared market factor deliver far less
    independent information than their count suggests, the standard error
    comes out too small, and z-scores inflate by the same factor. The layer
    then finds significant cells in a random walk, which is exactly the
    failure the null test exists to catch.

    Returns `(correct, broken)` rather than asserting, so the caller decides
    what to do and the observed rates both get reported.
    """
    return (
        run_null_test(seed=seed, sp=sp, broken_se_on_raw_n=False, replications=replications),
        run_null_test(seed=seed, sp=sp, broken_se_on_raw_n=True, replications=replications),
    )
