"""Which variance estimator is right for a cell's hit-rate edge? A placebo
test, read-only.

    uv run python scripts/hist/neff-correction-test-2026-11-18/placebo_estimators.py [R] [WORKERS]

For each headline cell-target (train and validate), every event keeps its
ticker, its cluster and its spacing to every other event in the cell, and
the whole cell moves by one random number of trading days (circular inside
2010-01-04 .. 2023-12-15). The outcome at the new date is the same hit
definition the baseline uses, `1[dir * close(t+5)/close(t) - 1 >= target]`,
minus that ticker-year's `baseline_empirical`, so the true edge is zero and
the co-firing and serial structure is the real one. Over R shifts the SD of
the placebo edge is the sampling error the estimators should reproduce.

Estimators, each a standard error of the cell's mean edge:

- `iid`: sd / sqrt(n), every fire treated as independent
- `shipped`: ADR 098, cluster heads only, sqrt(b (1 - b) / n_eff_shipped)
- `cgm_{date,month,quarter}`: two-way cluster-robust, time block x cluster_id
- `dk_L{5,10,21,63}`: Driscoll-Kraay, Bartlett-weighted HAC on the daily
  cross-sectional sum of residuals, lag L trading days

Scored per estimator: median SE / placebo SD (1 is right, below 1 is
overconfident), one-sided rejection rate at 5% (5% is right), and the
same on the real data: the count of cells surviving Benjamini-Hochberg.

Holdout events are never loaded. Bars run to 2024-01-12 only so the last
2023 forward windows close.
"""

from __future__ import annotations

import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sst
from sqlalchemy import text

OUT = Path(__file__).resolve().parent
LO, HI = "2010-01-04", "2023-12-15"
DK_LAGS = (5, 10, 21, 63)
BLOCKS = ("date", "month", "quarter")
SEED = 20261118
EST = ("iid", "shipped", *[f"cgm_{b}" for b in BLOCKS], *[f"dk_L{L}" for L in DK_LAGS])

_D: dict = {}


# ---------------------------------------------------------------- estimators


def _clustered(r: np.ndarray, codes: np.ndarray) -> float:
    _, inv = np.unique(codes, return_inverse=True)
    s = np.bincount(inv, weights=r)
    return float(s @ s)


def cgm_se(d: np.ndarray, block: np.ndarray, clus: np.ndarray) -> float:
    n = len(d)
    r = d - d.mean()
    inter = block.astype(np.int64) * (int(clus.max()) + 1) + clus
    v1, v2, v12 = _clustered(r, block), _clustered(r, clus), _clustered(r, inter)
    v = v1 + v2 - v12
    if v <= 0:
        v = max(v1, v2)
    return math.sqrt(v) / n


def dk_se(d: np.ndarray, t: np.ndarray, n_t: int, lag: int) -> float:
    n = len(d)
    s = np.bincount(t, weights=d - d.mean(), minlength=n_t)
    v = float(s @ s)
    for k in range(1, lag + 1):
        v += 2.0 * (1.0 - k / (lag + 1)) * float(s[k:] @ s[:-k])
    return math.sqrt(max(v, 0.0)) / n


def all_se(d, t, month, quarter, clus, n_t, heads_mask, neff_shipped, b_heads):
    out = {"iid": float(d.std(ddof=1) / math.sqrt(len(d)))}
    bh = min(max(b_heads, 1e-6), 1 - 1e-6)
    out["shipped"] = math.sqrt(bh * (1 - bh) / neff_shipped) if neff_shipped > 0 else np.nan
    for name, blk in (("date", t), ("month", month), ("quarter", quarter)):
        out[f"cgm_{name}"] = cgm_se(d, blk, clus)
    for L in DK_LAGS:
        out[f"dk_L{L}"] = dk_se(d, t, n_t, L)
    return out


# ---------------------------------------------------------------- workers


def _init(paths: dict) -> None:
    for k, p in paths.items():
        _D[k] = np.load(p, mmap_mode="r")


def work(task: dict) -> tuple[dict, list[dict]]:
    """One cell-target: the real-data SEs, then R placebo shifts."""
    month, quarter = _D["month"], _D["quarter"]
    n_t = len(month)
    key = task["key"]
    dmat = _D[f"d_{key}"]
    tic, tidx, clus, head = task["tic"], task["tidx"], task["clus"], task["head"]

    # real data: the events' own outcomes, as `cell_stats` measures them
    rd, rt, rc, rh = task["real_d"], task["real_t"], task["real_clus"], task["real_head"]
    real = all_se(
        rd, rt, month[rt], quarter[rt], rc, n_t, rh, task["neff_shipped"], task["b_heads"]
    )
    real_edge = float(rd.mean())
    real_edge_heads = float(rd[rh].mean()) if rh.any() else np.nan

    rng = np.random.default_rng(SEED + task["seed"])
    draws = []
    span = n_t
    for k in rng.integers(60, span - 60, size=task["R"]):
        tt = (tidx + k) % span
        d = np.asarray(dmat[tt, tic], dtype=np.float64)
        ok = ~np.isnan(d)
        if ok.sum() < 30:
            continue
        d, t2, c2, h2 = d[ok], tt[ok], clus[ok], head[ok]
        se = all_se(
            d,
            t2,
            month[t2],
            quarter[t2],
            c2,
            n_t,
            h2,
            task["neff_shipped"],
            task["b_heads"],
        )
        draws.append({"edge": float(d.mean()), "edge_heads": float(d[h2].mean()), **se})

    row = {
        **task["meta"],
        "n_all": len(rd),
        "n_heads": int(rh.sum()),
        "edge": real_edge,
        "edge_heads": real_edge_heads,
    }
    for e in EST:
        row[f"se_{e}"] = real[e]
        est_edge = real_edge_heads if e == "shipped" else real_edge
        row[f"p_{e}"] = float(sst.norm.sf(est_edge / real[e])) if real[e] > 0 else np.nan
    return row, [{**task["meta"], **x} for x in draws]


# ---------------------------------------------------------------- setup


def build(engine, cfg, chash):
    from capitalscan.core.cells import headline_grid
    from capitalscan.core.config import BaselineParams
    from capitalscan.research import cell_stats as cs
    from capitalscan.research.baselines import attach_event_baselines

    sp, bp = cfg.stats, BaselineParams()
    h = bp.horizon_days
    rho_era = cs.load_rho_era(engine, chash)

    cols = (
        "ticker, signal_date, signal_type, side, dd_bucket, era, split_key, entry_kind,"
        " is_cluster_head, cluster_id, fwd_window_days, cofire_count,"
        " fwd_ret_1d, fwd_ret_2d, fwd_ret_3d, fwd_ret_5d, fwd_ret_10d"
    )
    frames = {}
    for split in ("train", "validate"):
        sql = (
            f"SELECT {cols} FROM events WHERE config_hash = :c AND split_key = :s"
            " AND in_trade AND entry_kind = :k"
        )
        with engine.connect() as conn:
            raw = pd.read_sql(
                text(sql), conn, params={"c": chash, "s": split, "k": cs.GRID_ENTRY_KIND}
            )
        frames[split], _ = cs.select_grid_events(raw, sp, cs.min_fwd_window_for(cfg))

    with engine.connect() as conn:
        panel = pd.read_sql(
            text(
                "SELECT ticker, ts::date AS ts, adj_close FROM bars WHERE interval = '1d'"
                " AND ts >= '2008-01-01' AND ts <= '2024-01-12'"
            ),
            conn,
        )
    panel["adj_close"] = pd.to_numeric(panel["adj_close"], errors="coerce")
    close = panel.pivot(index="ts", columns="ticker", values="adj_close").sort_index()
    close.index = pd.to_datetime(close.index)
    fwd = close.shift(-h) / close - 1.0

    window = close.index[(close.index >= LO) & (close.index <= HI)]
    tick = list(close.columns)
    tpos = {t: i for i, t in enumerate(tick)}
    dpos = {d: i for i, d in enumerate(window)}
    month = (window.year * 12 + window.month - 1).to_numpy().astype(np.int64)
    quarter = (window.year * 4 + (window.month - 1) // 3).to_numpy().astype(np.int64)

    tmp = Path(sys.argv[3]) if len(sys.argv) > 3 else OUT / "_placebo_tmp"
    tmp.mkdir(exist_ok=True)
    paths = {"month": tmp / "month.npy", "quarter": tmp / "quarter.npy"}
    np.save(paths["month"], month)
    np.save(paths["quarter"], quarter)

    years = pd.Series(close.index.year, index=close.index)
    for direction in (1, -1):
        for target in sp.reach_targets:
            target = float(target)
            r = fwd * direction
            hit = (r >= target).where(r.notna()).astype("float64")
            # ticker-year empirical baseline over the same days, as
            # `ticker_year_baselines` computes it
            base = hit.groupby(years).transform("mean")
            d = (hit - base).loc[window].to_numpy(dtype=np.float32)
            key = f"{direction}_{target}"
            paths[f"d_{key}"] = tmp / f"d_{key}.npy"
            np.save(paths[f"d_{key}"], d)

    baselines = cs.build_baselines(engine, pd.concat(frames.values(), ignore_index=True), sp, bp)

    tasks = []
    seed = 0
    for split, ev in frames.items():
        heads = ev.loc[ev["is_cluster_head"].fillna(False).astype(bool)]
        for spec in headline_grid(sp):
            sel = (
                (ev["side"] == spec.side)
                & (ev["signal_type"] == spec.signal_type)
                & (ev["dd_bucket"] == spec.dd_bucket)
            )
            cell = ev.loc[sel]
            hcell = heads.loc[
                (heads["side"] == spec.side)
                & (heads["signal_type"] == spec.signal_type)
                & (heads["dd_bucket"] == spec.dd_bucket)
            ]
            if cell.empty or hcell.empty:
                continue
            _, _, neff_h = cs.cell_n_eff(hcell, rho_era)
            direction = -1 if spec.side == "short" else 1
            sd = pd.to_datetime(cell["signal_date"]).dt.normalize()
            in_win = sd.isin(dpos.keys()) & cell["ticker"].isin(tpos.keys())
            c = cell.loc[in_win]
            tic = c["ticker"].map(tpos).to_numpy(np.int64)
            tidx = pd.to_datetime(c["signal_date"]).dt.normalize().map(dpos).to_numpy(np.int64)
            clus = pd.factorize(c["cluster_id"].astype(str))[0].astype(np.int64)
            head = c["is_cluster_head"].fillna(False).astype(bool).to_numpy()
            for target in sp.reach_targets:
                target = float(target)
                flags = cs.hit_flags(c, target, h)
                keep = flags.notna().to_numpy()
                j = attach_event_baselines(c.loc[keep], baselines[direction], target)
                b = j["baseline_empirical"].astype(float).to_numpy()
                y = flags[keep].astype(float).to_numpy()
                okb = ~np.isnan(b)
                real_d = (y - b)[okb]
                real_head = head[keep][okb]
                b_heads = float(b[okb][real_head].mean()) if real_head.any() else 0.5
                tasks.append(
                    {
                        "meta": {
                            "split": split,
                            "side": spec.side,
                            "signal_type": spec.signal_type,
                            "dd_bucket": spec.dd_bucket,
                            "target": target,
                        },
                        "key": f"{direction}_{target}",
                        "tic": tic,
                        "tidx": tidx,
                        "clus": clus,
                        "head": head,
                        "real_d": real_d,
                        "real_t": tidx[keep][okb],
                        "real_clus": clus[keep][okb],
                        "real_head": real_head,
                        "neff_shipped": float(neff_h),
                        "b_heads": b_heads,
                        "seed": seed,
                    }
                )
                seed += 1
    return tasks, {k: str(v) for k, v in paths.items()}


def score(real: pd.DataFrame, draws: pd.DataFrame, alpha: float) -> None:
    from capitalscan.core.stats import benjamini_hochberg

    keys = ["split", "side", "signal_type", "dd_bucket", "target"]
    g = draws.groupby(keys)
    sd_all = g["edge"].std(ddof=1).rename("sd_all")
    sd_heads = g["edge_heads"].std(ddof=1).rename("sd_heads")
    m = real.set_index(keys).join(sd_all).join(sd_heads)

    print(
        f"{'estimator':<12}{'split':<10}{'SE/SD p10':>10}{'median':>9}{'p90':>8}"
        f"{'reject@5%':>11}{'cover95':>9}{'survive':>9}"
    )
    rows = []
    for split in ("train", "validate"):
        ms = m.xs(split, level="split")
        ds = draws.loc[draws.split == split]
        for e in EST:
            ref = ms["sd_heads"] if e == "shipped" else ms["sd_all"]
            ratio = ds.groupby(keys[1:])[e].median() / ref
            edge = ds["edge_heads"] if e == "shipped" else ds["edge"]
            z = edge / ds[e]
            reject = float((z > sst.norm.ppf(0.95)).mean())
            cover = float((z.abs() <= sst.norm.ppf(0.975)).mean())
            p = ms[f"p_{e}"]
            fam = p.notna()
            q = np.full(len(p), np.nan)
            if fam.any():
                _, qs = benjamini_hochberg(p[fam].to_numpy(float), alpha=alpha)
                q[fam.to_numpy()] = qs
            surv = int((q < alpha).sum())
            qv = ratio.quantile([0.1, 0.5, 0.9]).to_numpy()
            print(
                f"{e:<12}{split:<10}{qv[0]:>10.2f}{qv[1]:>9.2f}{qv[2]:>8.2f}"
                f"{reject:>11.3f}{cover:>9.3f}{surv:>9}"
            )
            rows.append(
                {
                    "estimator": e,
                    "split": split,
                    "ratio_p10": qv[0],
                    "ratio_median": qv[1],
                    "ratio_p90": qv[2],
                    "reject_5pct": reject,
                    "cover_95": cover,
                    "survive_bh": surv,
                }
            )
            m.loc[split, f"q_{e}"] = q
        print()
    pd.DataFrame(rows).to_csv(OUT / "placebo_scores.csv", index=False)
    m.reset_index().to_csv(OUT / "placebo_real.csv", index=False)


def main() -> int:
    from capitalscan.jobs import db_io
    from capitalscan.jobs.config import config_hash, resolve_config

    R = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    engine = db_io.get_engine()
    if engine.url.host != "localhost":
        print("refusing: this runs against the workstation copy only", file=sys.stderr)
        return 2
    cfg = resolve_config()
    chash = config_hash(cfg)
    t0 = time.time()
    tasks, paths = build(engine, cfg, chash)
    for t in tasks:
        t["R"] = R
    print(f"built {len(tasks)} cell-targets in {time.time() - t0:.0f}s; R={R}, workers={workers}")

    t1 = time.time()
    real, draws = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(paths,)) as ex:
        for row, dr in ex.map(work, tasks, chunksize=1):
            real.append(row)
            draws.extend(dr)
    print(f"placebo done in {time.time() - t1:.0f}s, {len(draws):,} draws\n")
    real_df, draws_df = pd.DataFrame(real), pd.DataFrame(draws)
    draws_df.to_parquet(OUT / "placebo_draws.parquet", index=False)
    score(real_df, draws_df, cfg.stats.fdr_alpha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
