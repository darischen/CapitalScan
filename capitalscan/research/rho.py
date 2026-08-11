"""`rho_bar` per era: how much independent information a clustered event
population actually carries (Session 11.3, ADR 098, DESIGN §6.3).

Two estimates, and only one of them is used.

**Empirical, the value in use.** The mean pairwise correlation of 5-day
returns among co-firing tickers, on **overlapping** windows, weighted by the
number of days each pair fired together. Pairs that never co-fired are
excluded entirely — their correlation says nothing about the dependence
structure of the event set.

Both of those choices are deliberate and both are documented in ADR 098.
Overlapping windows share days between adjacent observations and inflate the
measured correlation; that bias raises `rho_bar`, lowers `n_eff`, widens
every interval, and raises the bar for significance, so it runs in the safe
direction. Co-fire weighting exists because a pair co-firing 200 times
contributes 200 times the clustering of a pair co-firing once, and an
unweighted mean lets thin, noisy pair estimates outvote dense ones.

**Factor-implied, a stored diagnostic.** From a single-factor decomposition
against the S&P series in `market_days`:

```
r_i = alpha_i + beta_i * r_m + eps_i
rho_ij = (beta_i * beta_j * sigma_m^2) / (sigma_i * sigma_j)
```

This is **not** the value feeding `n_eff`. It assumes the residuals are
mutually uncorrelated, and two semiconductor names co-firing share a sector
move on top of the market move. It therefore understates `rho_bar`, inflates
`n_eff`, and narrows intervals — the opposite of the direction the
overlapping-window choice deliberately took. Its value is the gap:
`rho_gap` near zero says co-firing is largely a market effect, and a clearly
positive gap says the signal clusters within industries. Either reading is a
finding about the signal, and the check costs two columns.

Both estimates are computed over the **same pair set with the same weights**,
so `rho_gap` is a statement about the estimators rather than about which
pairs each happened to see.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.returns import forward_returns
from capitalscan.jobs import db_io

RHO_ERA_COLUMNS = [
    "era",
    "run_id",
    "config_hash",
    "rho_empirical",
    "rho_factor_implied",
    "rho_gap",
    "n_pairs",
    "n_cofire_days",
    "mean_beta",
    "computed_at",
    "git_sha",
]


@dataclass(frozen=True)
class RhoEstimate:
    """One era's measurement. Mirrors the `rho_era` row it becomes."""

    era: str
    rho_empirical: float | None
    rho_factor_implied: float | None
    rho_gap: float | None
    n_pairs: int
    n_cofire_days: int
    mean_beta: float | None


def overlapping_horizon_returns(
    bars: pd.DataFrame,
    horizon_days: int = 5,
) -> pd.DataFrame:
    """Wide frame of `horizon_days` forward returns, one row per trading day.

    ADR 098 part 1: measure the window starting on **every** trading day,
    yielding roughly 250 observations per ticker-year, rather than every
    fifth day yielding roughly 50. The overlap is the point, not an
    oversight.

    `bars` carries `ticker`, `ts`, `adj_close`. Total-return adjusted close,
    through `core.returns.forward_returns` — the same series the baselines
    read, so `rho_bar` and the baseline describe the same quantity.

    Returns a frame indexed by `ts` with one column per ticker. Missing days
    stay NaN; nothing is filled (invariant 4).
    """
    frames = {}
    for ticker, group in bars.groupby("ticker", sort=True):
        ordered = group.sort_values("ts")
        close = pd.Series(
            pd.to_numeric(ordered["adj_close"], errors="coerce").to_numpy(dtype="float64"),
            index=pd.to_datetime(ordered["ts"].to_numpy()),
        )
        frames[str(ticker)] = forward_returns(close, [horizon_days])[f"fwd_ret_{horizon_days}d"]
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).sort_index()


def cofire_pair_days(events: pd.DataFrame) -> Counter[tuple[str, str]]:
    """Days each unordered ticker pair fired the same signal type together.

    Co-firing is defined exactly as `events.cofire_count` defines it
    (`research.backtest.add_cofire_count`): distinct tickers sharing a
    `(signal_date, signal_type)`. Reusing that definition matters because
    `k_bar` in the `n_eff` formula is the mean of that same column — a
    `rho_bar` measured over a different notion of "together" would correct
    the wrong quantity.

    Multiple rows per ticker per day (one per entry kind) collapse to one,
    for the same reason `add_cofire_count` counts distinct tickers.
    """
    keys = ["signal_date", "signal_type"] if "signal_type" in events.columns else ["signal_date"]
    counts: Counter[tuple[str, str]] = Counter()
    for _, group in events.groupby(keys, sort=False):
        tickers = sorted({str(t) for t in group["ticker"]})
        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                counts[(tickers[i], tickers[j])] += 1
    return counts


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float | None:
    usable = ~np.isnan(values)
    if not usable.any() or weights[usable].sum() == 0:
        return None
    return float(np.average(values[usable], weights=weights[usable]))


def empirical_rho_bar(
    returns_wide: pd.DataFrame,
    pair_days: Counter[tuple[str, str]],
    min_overlap: int = 30,
) -> tuple[float | None, np.ndarray, np.ndarray, list[tuple[str, str]]]:
    """Co-fire-weighted mean pairwise correlation over the co-firing pairs.

    Returns `(rho_bar, correlations, weights, pairs)` so the caller can reuse
    the exact pair set and weights for the factor-implied estimate.

    `min_overlap` is the number of shared observations a pair needs before
    its correlation is counted. Below it the estimate carries almost no
    information, and a pair with three overlapping days can return exactly
    1.0 by coincidence. Such pairs drop out with NaN rather than being
    counted as perfectly correlated.
    """
    pairs = sorted(pair_days)
    if not pairs:
        return None, np.array([]), np.array([]), []

    columns = sorted({t for pair in pairs for t in pair})
    present = [c for c in columns if c in returns_wide.columns]
    corr = returns_wide[present].corr(min_periods=min_overlap)

    values = np.full(len(pairs), np.nan)
    weights = np.zeros(len(pairs))
    for idx, (a, b) in enumerate(pairs):
        weights[idx] = float(pair_days[(a, b)])
        if a in corr.index and b in corr.columns:
            values[idx] = corr.at[a, b]

    return _weighted_mean(values, weights), values, weights, pairs


def factor_betas(
    returns_wide: pd.DataFrame,
    market_returns: pd.Series,
    min_overlap: int = 30,
) -> pd.DataFrame:
    """`beta_i` and `sigma_i` per ticker against one market factor.

    `beta_i = cov(r_i, r_m) / var(r_m)`, the OLS slope of the single-factor
    decomposition. Computed on the same overlapping horizon returns as the
    empirical estimate, so the two are comparable.

    `sigma_m` rides along as a column rather than a second return value:
    every consumer needs it alongside the betas, and the alternative is a
    tuple whose second element is a scalar repeated for every row.
    """
    aligned_market = market_returns.reindex(returns_wide.index)
    var_m = float(aligned_market.var(ddof=1))
    rows = []
    for ticker in returns_wide.columns:
        series = returns_wide[ticker]
        joint = pd.concat([series, aligned_market], axis=1).dropna()
        if len(joint) < min_overlap or var_m == 0.0:
            rows.append({"ticker": ticker, "beta": np.nan, "sigma": np.nan})
            continue
        cov = float(joint.iloc[:, 0].cov(joint.iloc[:, 1]))
        rows.append(
            {
                "ticker": ticker,
                "beta": cov / float(joint.iloc[:, 1].var(ddof=1)),
                "sigma": float(joint.iloc[:, 0].std(ddof=1)),
            }
        )
    frame = pd.DataFrame(rows).set_index("ticker")
    frame["sigma_m"] = float(np.sqrt(var_m)) if var_m > 0 else np.nan
    return frame


def factor_implied_rho_bar(
    betas: pd.DataFrame,
    pairs: list[tuple[str, str]],
    weights: np.ndarray,
) -> tuple[float | None, float | None]:
    """`rho_ij = beta_i beta_j sigma_m^2 / (sigma_i sigma_j)`, same weights.

    Returns `(rho_factor_implied, mean_beta)`. The pair set and weights are
    the empirical estimate's, passed in rather than recomputed, so `rho_gap`
    measures the difference between two estimators over one population.
    """
    if not pairs or betas.empty:
        return None, None

    sigma_m_values = betas["sigma_m"].dropna()
    if sigma_m_values.empty:
        return None, None
    sigma_m = float(sigma_m_values.iloc[0])

    values = np.full(len(pairs), np.nan)
    for idx, (a, b) in enumerate(pairs):
        if a not in betas.index or b not in betas.index:
            continue
        beta_a, sigma_a = float(betas.at[a, "beta"]), float(betas.at[a, "sigma"])
        beta_b, sigma_b = float(betas.at[b, "beta"]), float(betas.at[b, "sigma"])
        if np.isnan(beta_a) or np.isnan(beta_b) or sigma_a == 0.0 or sigma_b == 0.0:
            continue
        values[idx] = (beta_a * beta_b * sigma_m**2) / (sigma_a * sigma_b)

    used_tickers = {t for pair in pairs for t in pair} & set(betas.index)
    mean_beta_value = betas.loc[sorted(used_tickers), "beta"].mean()
    mean_beta = None if pd.isna(mean_beta_value) else float(mean_beta_value)
    return _weighted_mean(values, weights), mean_beta


def rho_for_era(
    era: str,
    events: pd.DataFrame,
    returns_wide: pd.DataFrame,
    market_returns: pd.Series | None = None,
    min_overlap: int = 30,
) -> RhoEstimate:
    """Both estimates for one era's event population.

    `market_returns` may be `None` — the factor diagnostic is optional and
    its absence yields nulls in `rho_factor_implied`, `rho_gap`, and
    `mean_beta` rather than blocking the empirical value that `n_eff`
    actually needs.
    """
    pair_days = cofire_pair_days(events)
    rho_emp, _, weights, pairs = empirical_rho_bar(returns_wide, pair_days, min_overlap)

    rho_factor: float | None = None
    mean_beta: float | None = None
    if market_returns is not None and pairs:
        betas = factor_betas(returns_wide, market_returns, min_overlap)
        rho_factor, mean_beta = factor_implied_rho_bar(betas, pairs, weights)

    gap = None if (rho_emp is None or rho_factor is None) else float(rho_emp - rho_factor)
    return RhoEstimate(
        era=era,
        rho_empirical=rho_emp,
        rho_factor_implied=rho_factor,
        rho_gap=gap,
        n_pairs=len(pairs),
        n_cofire_days=int(sum(pair_days.values())),
        mean_beta=mean_beta,
    )


def compute_rho_eras(
    events: pd.DataFrame,
    returns_wide: pd.DataFrame,
    market_returns: pd.Series | None = None,
    min_overlap: int = 30,
) -> list[RhoEstimate]:
    """One `RhoEstimate` per era present in `events`.

    Era comes from `events.era`, which the backtest already assigned from
    `StatsParams.era_bounds` (`research.enrich._era`). Reading the stored
    column rather than recomputing the boundaries here is what keeps a
    single definition of era in the system: a second derivation from
    `era_bounds` would be a second place for it to drift.

    Each era's correlations are measured over that era's own returns, since
    a per-era constant computed over the whole history would describe no era
    in particular.
    """
    if "era" not in events.columns:
        raise ValueError("events must carry an 'era' column (research.enrich assigns it)")

    out = []
    for era, group in events.groupby("era", sort=True):
        dates = pd.to_datetime(group["signal_date"])
        era_returns = returns_wide.loc[
            (returns_wide.index >= dates.min()) & (returns_wide.index <= dates.max())
        ]
        era_market = None
        if market_returns is not None:
            era_market = market_returns.reindex(era_returns.index)
        out.append(rho_for_era(str(era), group, era_returns, era_market, min_overlap))
    return out


def rho_era_rows(
    estimates: list[RhoEstimate],
    run_id: str,
    config_hash: str,
    git_sha: str,
    computed_at: datetime | None = None,
) -> pd.DataFrame:
    """`rho_era`-shaped frame, provenance attached (invariant 6, ADR 034).

    `computed_at` is an argument rather than a `now()` call inside the row
    builder so two runs over identical data can be compared column by column
    (11.3 acceptance: "Two runs against identical data write identical
    values ignoring `run_id` and `computed_at`").
    """
    stamp = computed_at or datetime.now(timezone.utc)
    rows = [
        {
            "era": e.era,
            "run_id": run_id,
            "config_hash": config_hash,
            "rho_empirical": e.rho_empirical,
            "rho_factor_implied": e.rho_factor_implied,
            "rho_gap": e.rho_gap,
            "n_pairs": e.n_pairs,
            "n_cofire_days": e.n_cofire_days,
            "mean_beta": e.mean_beta,
            "computed_at": stamp,
            "git_sha": git_sha,
        }
        for e in estimates
    ]
    if not rows:
        return pd.DataFrame(columns=RHO_ERA_COLUMNS)
    return pd.DataFrame(rows, columns=RHO_ERA_COLUMNS)


def write_rho_era(engine: Engine, rows: pd.DataFrame) -> int:
    """Upsert on `(era, config_hash)` — the ADR 098 primary key.

    `config_hash` is in the key for the reason ADR 096 put it in
    `cell_stats`: an 18-config sweep produces 18 event populations with
    different co-firing structures, and one snapshot per era would make each
    Phase 4 run overwrite the last. Running against a second config adds
    rows; it does not replace the first config's.
    """
    if rows.empty:
        return 0
    return db_io.upsert(engine, "rho_era", rows, ["era", "config_hash"])


def load_market_horizon_returns(
    engine: Engine,
    horizon_days: int = 5,
) -> pd.Series:
    """S&P `horizon_days` forward returns from `market_days.spx_close`.

    The S&P series alone, per ADR 099: QQQ is ingested as an ordinary ticker
    and is not in `market_days` as an index series, so the single-factor
    diagnostic has one factor available and adding a second is a deferred
    decision the `rho_gap` measurement is meant to inform.
    """
    with engine.connect() as conn:
        frame = pd.read_sql(
            text("SELECT ts, spx_close FROM market_days WHERE spx_close IS NOT NULL ORDER BY ts"),
            conn,
        )
    close = pd.Series(
        pd.to_numeric(frame["spx_close"], errors="coerce").to_numpy(dtype="float64"),
        index=pd.to_datetime(frame["ts"].to_numpy()),
    )
    return forward_returns(close, [horizon_days])[f"fwd_ret_{horizon_days}d"]
