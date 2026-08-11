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

from capitalscan.core.config import DEFAULT_BASELINE, DEFAULT_RHO
from capitalscan.core.returns import forward_returns
from capitalscan.jobs import db_io
from capitalscan.research.baselines import load_adj_close_panel

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
    horizon_days: int = DEFAULT_BASELINE.horizon_days,
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


def cofire_days(events: pd.DataFrame) -> int:
    """Distinct calendar days on which two or more tickers fired together.

    `rho_era.n_cofire_days` is this number, not the sum of every pair's
    co-fire count. The two differ by a large factor and in the direction
    that flatters the measurement: a day with `k` co-firing names produces
    `C(k, 2)` pairs, so at the ~10 names/day the synthetic null exercises,
    a pair-day total reads roughly 47x the number of days it claims to
    count. Anyone reading the stored column as "how many days did this
    measurement see" would be off by that factor.

    Same grain as `cofire_pair_days`: a ticker firing several entry kinds on
    one date counts once, and two tickers firing *different* signal types on
    the same date are not co-firing. A date qualifies when any one of its
    `(signal_date, signal_type)` groups holds at least two distinct tickers.
    """
    keys = ["signal_date", "signal_type"] if "signal_type" in events.columns else ["signal_date"]
    qualifying = set()
    for key, group in events.groupby(keys, sort=False):
        if group["ticker"].nunique() >= 2:
            qualifying.add(key[0] if isinstance(key, tuple) else key)
    return len(qualifying)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float | None:
    usable = ~np.isnan(values)
    if not usable.any() or weights[usable].sum() == 0:
        return None
    return float(np.average(values[usable], weights=weights[usable]))


def empirical_rho_bar(
    returns_wide: pd.DataFrame,
    pair_days: Counter[tuple[str, str]],
    min_overlap: int = DEFAULT_RHO.min_pair_overlap,
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
    min_overlap: int = DEFAULT_RHO.min_pair_overlap,
) -> pd.DataFrame:
    """`beta_i` and `sigma_i` per ticker against one market factor.

    `beta_i = cov(r_i, r_m) / var(r_m)`, the OLS slope of the single-factor
    decomposition. Computed on the same overlapping horizon returns as the
    empirical estimate, so the two are comparable.

    `sigma_m` rides along as a column rather than a second return value:
    every consumer needs it alongside the betas, and the alternative is a
    tuple whose second element is a scalar repeated for every row.

    **Every quantity comes from the sample it was measured in.** `beta_i`,
    `sigma_i`, and `sigma_m` all read ticker `i`'s own joint sample with the
    market, which is why `sigma_m` varies by row rather than being one
    scalar broadcast across the frame. The earlier version measured `beta_i`
    and `sigma_i` on that per-ticker subset while taking `sigma_m` from the
    full index, so `rho_ij = beta_i beta_j sigma_m^2 / (sigma_i sigma_j)`
    combined quantities from three different samples. On the synthetic
    panels those samples coincide and nothing showed; on real `bars`, where
    tickers list and delist mid-era, they do not, and the error lands in
    `rho_gap` — the one number ADR 099 says to read the "is one factor
    enough" decision off.

    Restricting instead to the days where *every* ticker is observed was the
    other candidate and is worse: one ticker listing mid-era would truncate
    the sample for all 500 others, and a single ticker with no overlap would
    empty it entirely.

    `beta_i * sigma_m_i` is a ticker's systematic volatility, and that
    product is what `factor_implied_rho_bar` multiplies pairwise. It is
    algebraically identical to ADR 098's formula whenever the two tickers
    share a sample, which is the case the ADR was written against.
    """
    aligned_market = market_returns.reindex(returns_wide.index)
    rows = []
    for ticker in returns_wide.columns:
        joint = pd.concat([returns_wide[ticker], aligned_market], axis=1).dropna()
        var_m = float(joint.iloc[:, 1].var(ddof=1)) if len(joint) >= min_overlap else 0.0
        if len(joint) < min_overlap or var_m == 0.0:
            rows.append({"ticker": ticker, "beta": np.nan, "sigma": np.nan, "sigma_m": np.nan})
            continue
        rows.append(
            {
                "ticker": ticker,
                "beta": float(joint.iloc[:, 0].cov(joint.iloc[:, 1])) / var_m,
                "sigma": float(joint.iloc[:, 0].std(ddof=1)),
                "sigma_m": float(np.sqrt(var_m)),
            }
        )
    return pd.DataFrame(rows, columns=["ticker", "beta", "sigma", "sigma_m"]).set_index("ticker")


def factor_implied_rho_bar(
    betas: pd.DataFrame,
    pairs: list[tuple[str, str]],
    weights: np.ndarray,
) -> tuple[float | None, float | None]:
    """`rho_ij = beta_i beta_j sigma_m^2 / (sigma_i sigma_j)`, same weights.

    Returns `(rho_factor_implied, mean_beta)`. The pair set and weights are
    the empirical estimate's, passed in rather than recomputed, so `rho_gap`
    measures the difference between two estimators over one population.

    Written as `(beta_i sigma_m_i)(beta_j sigma_m_j) / (sigma_i sigma_j)`,
    the product of the two tickers' systematic volatilities. That is ADR
    098's formula exactly whenever both tickers were measured on the same
    days, and it stays coherent when they were not — see `factor_betas` on
    why `sigma_m` is per-ticker rather than one scalar.
    """
    if not pairs or betas.empty:
        return None, None
    if betas["sigma_m"].dropna().empty:
        return None, None

    beta_of = {str(k): float(v) for k, v in betas["beta"].items()}
    sigma_of = {str(k): float(v) for k, v in betas["sigma"].items()}
    sigma_m_of = {str(k): float(v) for k, v in betas["sigma_m"].items()}

    values = np.full(len(pairs), np.nan)
    for idx, (a, b) in enumerate(pairs):
        if a not in beta_of or b not in beta_of:
            continue
        beta_a, sigma_a, sigma_m_a = beta_of[a], sigma_of[a], sigma_m_of[a]
        beta_b, sigma_b, sigma_m_b = beta_of[b], sigma_of[b], sigma_m_of[b]
        if np.isnan(beta_a) or np.isnan(beta_b) or sigma_a == 0.0 or sigma_b == 0.0:
            continue
        values[idx] = ((beta_a * sigma_m_a) * (beta_b * sigma_m_b)) / (sigma_a * sigma_b)

    used_tickers = {t for pair in pairs for t in pair} & set(betas.index)
    mean_beta_value = betas.loc[sorted(used_tickers), "beta"].mean()
    mean_beta = None if pd.isna(mean_beta_value) else float(mean_beta_value)
    return _weighted_mean(values, weights), mean_beta


def rho_for_era(
    era: str,
    events: pd.DataFrame,
    returns_wide: pd.DataFrame,
    market_returns: pd.Series | None = None,
    min_overlap: int = DEFAULT_RHO.min_pair_overlap,
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
        n_cofire_days=cofire_days(events),
        mean_beta=mean_beta,
    )


def compute_rho_eras(
    events: pd.DataFrame,
    returns_wide: pd.DataFrame,
    market_returns: pd.Series | None = None,
    min_overlap: int = DEFAULT_RHO.min_pair_overlap,
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

    **An era with no measurable `rho_empirical` writes no row.** That is the
    rule `rho_era`'s migration states, and this is where it is enforced:
    the column is `NOT NULL` precisely because a null there would read as
    "computed and found nothing", which is a different claim from "not
    computed". `rho_for_era` returns `None` when an era has no co-firing
    pairs at all, or when every pair falls under `min_overlap`, and passing
    that through produced an `IntegrityError` at write time rather than the
    documented skip. Dropped estimates are still visible: the caller sees
    fewer rows than it passed estimates.
    """
    stamp = computed_at or datetime.now(timezone.utc)
    estimates = [e for e in estimates if e.rho_empirical is not None]
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
    horizon_days: int = DEFAULT_BASELINE.horizon_days,
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


def load_config_events(engine: Engine, config_hash: str) -> pd.DataFrame:
    """The co-firing skeleton of one config's event population.

    Four columns, distinct: `ticker`, `signal_date`, `signal_type`, `era`.
    Nothing else is needed to measure `rho_bar`, and `DISTINCT` collapses the
    per-`entry_kind` fan-out for the same reason `add_cofire_count` counts
    distinct tickers — one ticker firing four entry kinds on a date is one
    co-firing name, not four.

    **Live events are excluded** by `era IS NOT NULL`. An event whose
    `signal_date` sits past the last era boundary carries a null era
    (`research.enrich._era`), and there is no era row for it to inform.
    Including them would silently pool the live tail into whichever era
    `groupby` happened to place it in.
    """
    sql = """
        SELECT DISTINCT ticker, signal_date, signal_type, era
        FROM events
        WHERE config_hash = :config_hash AND era IS NOT NULL
        ORDER BY era, signal_date, ticker
    """
    with engine.connect() as conn:
        frame = pd.read_sql(text(sql), conn, params={"config_hash": config_hash})
    frame["signal_date"] = pd.to_datetime(frame["signal_date"])
    return frame


@dataclass(frozen=True)
class RhoRunReport:
    """What one `cscan stats rho` run measured and wrote."""

    config_hash: str
    run_id: str
    estimates: list[RhoEstimate]
    rows_written: int
    n_events: int
    n_tickers: int

    @property
    def skipped_eras(self) -> list[str]:
        """Eras measured but not written: no `rho_empirical` to store.

        Visible rather than silent — a missing era row means every
        `cell_stats` row for that era is uninterpretable (ADR 098's
        consequences), so the caller has to be able to say which.
        """
        return [e.era for e in self.estimates if e.rho_empirical is None]


def run_rho_eras(
    engine: Engine,
    config_hash: str,
    run_id: str,
    git_sha: str,
    horizon_days: int = DEFAULT_BASELINE.horizon_days,
    min_overlap: int = DEFAULT_RHO.min_pair_overlap,
    computed_at: datetime | None = None,
) -> RhoRunReport:
    """Measure `rho_bar` per era for one config and write `rho_era`.

    The whole ADR 098 path in one call: read the config's events, read the
    total-return panel for exactly the tickers those events name, build
    overlapping `horizon_days` forward returns, measure both estimates per
    era, and upsert on `(era, config_hash)`.

    The bars read is bounded by the tickers and dates the events actually
    span. Pulling the full `bars` table instead would be several million
    rows for a measurement that only ever looks at co-firing names.

    Session 12 reads the rows this writes. A `cell_stats` row is only
    interpretable next to the `rho_era` row sharing its `config_hash`, which
    is why `run_id` and `git_sha` are required arguments rather than
    defaulted: a row without provenance cannot explain why two runs against
    one config disagree (ADR 034, ADR 098).
    """
    events = load_config_events(engine, config_hash)
    if events.empty:
        return RhoRunReport(config_hash, run_id, [], 0, 0, 0)

    tickers = sorted(events["ticker"].astype(str).unique())
    bars = load_adj_close_panel(
        engine,
        tickers=tickers,
        start=str(events["signal_date"].min().date()),
        # The forward window opens on the last signal day and closes
        # `horizon_days` sessions later, so the panel has to run past the
        # last event or every late event's return is NaN. Calendar days,
        # generously converted, since `bars` is filtered by date not by
        # session count.
        end=str((events["signal_date"].max() + pd.Timedelta(days=2 * horizon_days + 7)).date()),
    )
    returns_wide = overlapping_horizon_returns(bars, horizon_days)
    market = load_market_horizon_returns(engine, horizon_days)

    estimates = compute_rho_eras(events, returns_wide, market, min_overlap)
    rows = rho_era_rows(estimates, run_id, config_hash, git_sha, computed_at)
    written = write_rho_era(engine, rows)
    return RhoRunReport(
        config_hash=config_hash,
        run_id=run_id,
        estimates=estimates,
        rows_written=written,
        n_events=int(len(events)),
        n_tickers=len(tickers),
    )
