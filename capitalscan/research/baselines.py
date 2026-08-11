"""Per-ticker-year baselines over a real or synthetic price panel
(Session 11.2, DESIGN §6.2, ADR 062).

`core/baselines.py` owns the formulas. This module owns the windowing: which
days belong to which ticker-year, which of them carry a complete forward
window, and which carry a complete strictly-prior trailing window. That
split is deliberate — the arithmetic is textbook and the windowing is where
lookahead enters.

**Price series.** Every number here is measured on total-return `adj_close`,
through `core.returns.forward_returns`. The label source contract in
`BUILD.md` is explicit that Phase 4 reads `events.fwd_ret_*d` rather than
`path.terminal` for exactly this reason: `path.terminal` is split-adjusted
close anchored to an entry price, a different quantity by design. A baseline
measured on one series and an event outcome measured on the other would
differ by the dividend stream and nothing would flag it.

**One observation set per ticker-year.** The empirical baseline needs a
complete forward window; the parametric one needs a complete trailing
window. Both are computed over the *same* day set — the year's days with a
complete forward window — and the parametric baseline is null unless every
one of those days also carries a full trailing window. Aligning them is what
makes the gap between them a statement about the return distribution rather
than about which days each happened to see.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.baselines import (
    baseline_gap,
    disagreement_flag,
    empirical_baseline,
    event_weighted_baseline,
    parametric_baseline_series,
    trailing_drift_vol,
)
from capitalscan.core.config import BaselineParams
from capitalscan.core.returns import forward_returns

TICKER_YEAR_COLUMNS = [
    "ticker",
    "year",
    "target_pct",
    "n_obs",
    "baseline_empirical",
    "baseline_parametric",
    "mu_annual",
    "sigma_annual",
    "baseline_gap",
    "disagreement",
]


def _one_ticker(
    bars: pd.DataFrame,
    targets: tuple[float, ...],
    bp: BaselineParams,
) -> list[dict]:
    """Every (year, target) row for a single ticker's sorted daily panel."""
    frame = bars.sort_values("ts")
    close = pd.Series(
        pd.to_numeric(frame["adj_close"], errors="coerce").to_numpy(dtype="float64"),
        index=pd.RangeIndex(len(frame)),
    )
    ts = pd.to_datetime(frame["ts"].to_numpy())
    ticker = str(frame["ticker"].iloc[0])

    fwd = forward_returns(close, [bp.horizon_days])[f"fwd_ret_{bp.horizon_days}d"]
    drift_vol = trailing_drift_vol(close.pct_change(), bp)

    years = pd.Series(ts.year, index=close.index)
    rows: list[dict] = []
    for year, idx in years.groupby(years).groups.items():
        # The year's observation set: days whose forward window closed
        # inside the series. Everything else is measured over these days.
        day_index = fwd.loc[idx].dropna().index
        mu = drift_vol["mu_annual"].loc[day_index]
        sigma = drift_vol["sigma_annual"].loc[day_index]
        # Any day without a full strictly-prior window makes the whole
        # ticker-year's parametric baseline null. A shortened window is
        # forbidden (invariant 4), and averaging over the subset that does
        # have one would report a partial year as if it were whole.
        has_history = bool(len(day_index) > 0 and not mu.isna().any())

        for target in targets:
            emp, n_obs = empirical_baseline(fwd.loc[day_index], target)
            if has_history:
                per_day = parametric_baseline_series(target, mu.to_numpy(), sigma.to_numpy(), bp)
                par: float | None = float(per_day.mean())
                mu_out: float | None = float(mu.mean())
                sigma_out: float | None = float(sigma.mean())
            else:
                par = mu_out = sigma_out = None
            gap = baseline_gap(emp, par)
            rows.append(
                {
                    "ticker": ticker,
                    "year": int(str(year)),
                    "target_pct": float(target),
                    "n_obs": n_obs,
                    "baseline_empirical": emp,
                    "baseline_parametric": par,
                    "mu_annual": mu_out,
                    "sigma_annual": sigma_out,
                    "baseline_gap": gap,
                    "disagreement": disagreement_flag(gap, bp),
                }
            )
    return rows


def ticker_year_baselines(
    bars: pd.DataFrame,
    targets: tuple[float, ...],
    bp: BaselineParams,
) -> pd.DataFrame:
    """Both baselines for every (ticker, year, target) in `bars`.

    `bars` carries `ticker`, `ts`, `adj_close` — the `bars` column names
    unchanged, so a database read drops in with no translation layer, and so
    does a synthetic panel from `research.synthetic`.

    One row per (ticker, year, target). `baseline_empirical` is the reported
    number (ADR 062); `baseline_parametric` and `disagreement` are the
    diagnostic. Null propagates in both: a ticker-year without a full
    trailing window carries a null parametric baseline and a null
    disagreement flag, never a shortened window and never a False.
    """
    missing = {"ticker", "ts", "adj_close"} - set(bars.columns)
    if missing:
        raise ValueError(f"bars is missing required columns: {sorted(missing)}")
    if not targets:
        raise ValueError("targets must not be empty")

    rows: list[dict] = []
    for _, group in bars.groupby("ticker", sort=True):
        rows.extend(_one_ticker(group, targets, bp))

    if not rows:
        return pd.DataFrame(columns=TICKER_YEAR_COLUMNS)
    return pd.DataFrame(rows, columns=TICKER_YEAR_COLUMNS)


def attach_event_baselines(
    events: pd.DataFrame,
    baselines: pd.DataFrame,
    target: float,
) -> pd.DataFrame:
    """Join each event to its own ticker-year baseline for one target.

    `events` carries `ticker` and `signal_date`. The join key is
    `(ticker, year(signal_date))` — the event's own ticker-year, which is
    what "the event-weighted mean of its constituent events' per-ticker-year
    baselines" (DESIGN §6.2) means. A left join, so an event whose
    ticker-year is absent carries a null baseline and is counted rather than
    dropped.
    """
    out = events.copy()
    out["year"] = pd.to_datetime(out["signal_date"]).dt.year.astype("int64")
    slice_ = baselines.loc[
        np.isclose(baselines["target_pct"].to_numpy(dtype="float64"), target),
        ["ticker", "year", "baseline_empirical", "baseline_parametric"],
    ]
    return out.merge(slice_, on=["ticker", "year"], how="left")


def cell_baselines(
    events_with_baselines: pd.DataFrame,
    group_cols: list[str],
    column: str = "baseline_empirical",
) -> pd.DataFrame:
    """Event-weighted cell baselines, one row per cell.

    Reuses `core.baselines.event_weighted_baseline` per group rather than
    calling `groupby().mean()`, so the null accounting is the same one the
    pure function defines: nulls are excluded from the mean and reported in
    `n_null_baseline`, never silently dropped and never counted as zero.
    """
    rows: list[dict] = []
    for key, group in events_with_baselines.groupby(group_cols, sort=True, dropna=False):
        value, n_used, n_null = event_weighted_baseline(group[column])
        key_tuple = key if isinstance(key, tuple) else (key,)
        row = dict(zip(group_cols, key_tuple))
        row.update(
            {
                "n_events": int(len(group)),
                "baseline": value,
                "n_baseline_used": n_used,
                "n_null_baseline": n_null,
            }
        )
        rows.append(row)
    columns = group_cols + ["n_events", "baseline", "n_baseline_used", "n_null_baseline"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def load_adj_close_panel(
    engine: Engine,
    tickers: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Daily total-return closes from `bars`, shaped for the functions above.

    `interval = '1d'` is pinned here rather than left to the caller: the
    hourly rows share the table and a baseline computed over a mixed
    interval would silently measure something else entirely.
    """
    sql = [
        "SELECT ticker, ts::date AS ts, adj_close",
        "FROM bars WHERE interval = '1d'",
    ]
    params: dict[str, Any] = {}
    if tickers:
        sql.append("AND ticker = ANY(:tickers)")
        params["tickers"] = tickers
    if start:
        sql.append("AND ts >= :start")
        params["start"] = start
    if end:
        sql.append("AND ts <= :end")
        params["end"] = end
    sql.append("ORDER BY ticker, ts")

    with engine.connect() as conn:
        frame = pd.read_sql(text(" ".join(sql)), conn, params=params)
    frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
    return frame
