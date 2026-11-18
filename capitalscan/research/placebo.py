"""Placebo-calibrated inflation of the cluster-robust variance (ADR 209).

The month x cluster variance in `core.variance` is right only if the month
block absorbs every dependence the fires share. This measures how far off it
is, on the real co-firing structure, with the true edge fixed at zero.

Each headline cell-target keeps its events' tickers, clusters and spacing,
and the whole cell moves by one random number of trading days, circular
inside `VarianceParams.placebo_window`. The outcome at the new date is
`1[dir * fwd_h >= target]` minus that ticker-year's own hit rate, so its
expectation is zero. Over `placebo_draws` shifts, the SD of the placebo edge
is the sampling error the estimator should reproduce, and the ratio
`SD / median(SE)` is the factor `cell_stats` multiplies its SE by.

Ported from `scripts/hist/neff-correction-test-2026-11-18/placebo_estimators.py`
(`build`, `work`, `_init`), restricted to the month x cluster estimator.

Spawn-safe: importing this module does nothing, `work` is top-level, and
workers receive arrays through `.npy` files memory-mapped read-only. No
worker opens a database connection.
"""

from __future__ import annotations

import logging
import math
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from capitalscan.core.cells import headline_grid
from capitalscan.core.config import StatsParams, VarianceParams
from capitalscan.core.variance import cluster_robust_variance, inflation_factor, month_block

logger = logging.getLogger(__name__)

POOLED = "pooled"

# Worker-process arrays, filled by `_init` from memory-mapped `.npy` files.
_D: dict[str, np.ndarray] = {}


def null_edge_matrix(
    panel: pd.DataFrame,
    target: float,
    direction: int,
    horizon: int,
    window: tuple[str, str],
) -> tuple[pd.DatetimeIndex, list[str], np.ndarray]:
    """Dates x tickers, float32: `1[dir * fwd_h >= target] - ticker_year_mean`.

    `fwd_h` is the `horizon`-day total-return forward return on the full
    panel, so the forward windows of the last days in `window` close on bars
    past it. The ticker-year mean is the empirical baseline over the same
    days, as `baselines.ticker_year_baselines` measures it. NaN where the
    forward return is undefined (invariant 4: never filled).
    """
    close = panel.pivot(index="ts", columns="ticker", values="adj_close").sort_index()
    idx = pd.DatetimeIndex(pd.to_datetime(close.index))
    close = close.set_axis(idx).astype("float64")
    fwd = close.shift(-horizon) / close - 1.0
    r = fwd * float(direction)
    hit = (r >= float(target)).astype("float64").where(r.notna())
    base = hit.groupby(idx.year).transform("mean")
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    in_window = (idx >= lo) & (idx <= hi)
    d = (hit - base).loc[in_window]
    return pd.DatetimeIndex(d.index), [str(t) for t in d.columns], d.to_numpy(np.float32)


# ---------------------------------------------------------------- workers


def _init(paths: dict[str, str]) -> None:
    for k, p in paths.items():
        _D[k] = np.load(p, mmap_mode="r")


def _draws(task: dict[str, Any], data: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """One cell-target's placebo shifts: `(edges, ses)`, one entry per kept draw."""
    month = data["month"]
    dmat = data[task["key"]]
    span = len(month)
    tic, tidx, clus = task["tic"], task["tidx"], task["clus"]
    lo, hi = task["min_shift"], span - task["min_shift"]
    rng = np.random.default_rng(task["seed"])
    edges: list[float] = []
    ses: list[float] = []
    if hi <= lo:
        return np.empty(0), np.empty(0)
    for k in rng.integers(lo, hi, size=task["draws"]):
        tt = (tidx + k) % span
        d = np.asarray(dmat[tt, tic], dtype=np.float64)
        ok = ~np.isnan(d)
        if int(ok.sum()) < task["min_outcomes"]:
            continue
        d, t2, c2 = d[ok], tt[ok], clus[ok]
        v, _, _ = cluster_robust_variance(d, month[t2], c2)
        edges.append(float(d.mean()))
        ses.append(math.sqrt(v))
    return np.asarray(edges), np.asarray(ses)


def work(task: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Pool entry point: `_draws` over the arrays `_init` mapped."""
    return _draws(task, _D)


# ---------------------------------------------------------------- driver


def inherit_pooled(factors: dict[str, float], tolerance: float) -> dict[str, float]:
    """Every non-pooled factor within `tolerance` (relative) of pooled takes pooled."""
    if POOLED not in factors:
        return dict(factors)
    p = factors[POOLED]
    return {
        k: (p if k != POOLED and abs(f - p) <= tolerance * p else f) for k, f in factors.items()
    }


def _tasks(
    events: pd.DataFrame,
    scopes: dict[str, pd.Series],
    sp: StatsParams,
    vp: VarianceParams,
    dpos: dict[pd.Timestamp, int],
    tpos: dict[str, int],
) -> list[tuple[str, dict[str, Any]]]:
    """`(scope, task)` for every headline cell-target with events in the window."""
    sd = pd.to_datetime(events["signal_date"]).dt.normalize()
    in_win = sd.isin(list(dpos)) & events["ticker"].isin(list(tpos))
    out: list[tuple[str, dict[str, Any]]] = []
    seed = vp.placebo_seed
    for scope, mask in scopes.items():
        ev = events.loc[mask.reindex(events.index, fill_value=False).astype(bool) & in_win]
        for spec in headline_grid(sp):
            c = ev.loc[
                (ev["side"] == spec.side)
                & (ev["signal_type"] == spec.signal_type)
                & (ev["dd_bucket"] == spec.dd_bucket)
            ]
            if c.empty:
                continue
            direction = -1 if spec.side == "short" else 1
            tic = c["ticker"].map(tpos).to_numpy(np.int64)
            tidx = sd.loc[c.index].map(dpos).to_numpy(np.int64)
            clus = pd.factorize(c["cluster_id"].astype(str))[0].astype(np.int64)
            for target in sp.reach_targets:
                out.append(
                    (
                        scope,
                        {
                            "key": f"d_{direction}_{float(target)}",
                            "tic": tic,
                            "tidx": tidx,
                            "clus": clus,
                            "seed": seed,
                            "draws": vp.placebo_draws,
                            "min_shift": vp.placebo_min_shift_days,
                            "min_outcomes": vp.placebo_min_outcomes,
                        },
                    )
                )
                seed += 1
    return out


def measure_inflation(
    panel: pd.DataFrame,
    events: pd.DataFrame,
    sp: StatsParams,
    vp: VarianceParams,
    horizon: int,
    scopes: dict[str, pd.Series],
) -> dict[str, float]:
    """The placebo inflation factor per scope.

    `scopes` maps a name (`"pooled"`, each era) to a boolean mask over
    `events`. A scope with no cell-target carrying at least two usable draws
    gets `vp.inflation_floor`, logged. An era factor within
    `vp.era_inherit_tolerance` of the pooled one is replaced by it.
    """
    lo, hi = vp.placebo_window
    with tempfile.TemporaryDirectory(prefix="capitalscan-placebo-") as tmp:
        paths: dict[str, str] = {}
        dates: pd.DatetimeIndex | None = None
        tickers: list[str] = []
        for direction in (1, -1):
            for target in sp.reach_targets:
                dates, tickers, d = null_edge_matrix(
                    panel, float(target), direction, horizon, (lo, hi)
                )
                key = f"d_{direction}_{float(target)}"
                paths[key] = str(Path(tmp) / f"{key}.npy")
                np.save(paths[key], d)
                del d
        assert dates is not None
        paths["month"] = str(Path(tmp) / "month.npy")
        np.save(paths["month"], month_block(pd.Series(dates)))

        dpos = {ts: i for i, ts in enumerate(dates)}
        tpos = {t: i for i, t in enumerate(tickers)}
        tasks = _tasks(events, scopes, sp, vp, dpos, tpos)

        if vp.placebo_workers == 1:
            data = {k: np.load(p, mmap_mode="r") for k, p in paths.items()}
            results = [_draws(t, data) for _, t in tasks]
            del data
        else:
            with ProcessPoolExecutor(
                max_workers=vp.placebo_workers, initializer=_init, initargs=(paths,)
            ) as ex:
                results = list(ex.map(work, [t for _, t in tasks], chunksize=1))

    factors: dict[str, float] = {}
    for scope in scopes:
        edges: list[np.ndarray] = []
        ses: list[np.ndarray] = []
        groups: list[np.ndarray] = []
        for g, ((s, _), (e, se)) in enumerate(zip(tasks, results, strict=True)):
            if s != scope or len(e) < 2:
                continue
            edges.append(e)
            ses.append(se)
            groups.append(np.full(len(e), g, dtype=np.int64))
        if not edges:
            logger.warning(
                "placebo scope %s: no cell-target with usable draws; factor falls back "
                "to the floor %.4f",
                scope,
                vp.inflation_floor,
            )
            factors[scope] = vp.inflation_floor
            continue
        factors[scope] = inflation_factor(
            np.concatenate(edges), np.concatenate(ses), np.concatenate(groups), vp.inflation_floor
        )
    return inherit_pooled(factors, vp.era_inherit_tolerance)
