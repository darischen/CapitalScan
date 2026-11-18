"""Option C of the ADR 166 decision: does any cell survive FDR correction
when its interval comes from the observed dependence rather than from
ADR 098's `k_bar`? Read-only.

    uv run python scripts/hist/neff-correction-test-2026-11-18/correction_test.py

Train and validate only. The holdout split is never loaded.

For each headline cell (ADR 102's grid) and reach target, four tests of
`p_hit > baseline_empirical`, one-sided, each then Benjamini-Hochberg
corrected across the family:

- **shipped**: `research.cell_stats.compute_grid` on cluster heads, exactly
  as `cscan cell-stats` computes it (ADR 098 `n_eff`, family = unsuppressed
  pooled cells).
- **level**: every fire, the shipped z-test (`one_sided_p_value`) with the
  `n_eff` a two-way cluster-robust variance implies (date x `cluster_id`).
- **diff_{block}**: every fire, `d_i = hit_i - baseline_i` against each
  event's own ticker-year baseline, mean of `d` with a two-way robust
  variance clustered by a time block (date, month, quarter) and by
  `cluster_id`. Tests the edge itself, so baseline heterogeneity across
  tickers enters the variance.
- **boot_L{n}**: the same `d`, a circular moving-block bootstrap over
  trading dates with block length n (10, 21, 63). One date resample is
  shared by every cell in a split, so cross-cell dependence is kept.

The robust family is every cell-target whose robust `n_eff` clears
`min_n_eff`. A second family, all 64 cell-targets regardless of
suppression, is reported beside it.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sst
from sqlalchemy import text

from capitalscan.core.cells import headline_grid
from capitalscan.core.config import BaselineParams
from capitalscan.core.stats import benjamini_hochberg, one_sided_p_value
from capitalscan.jobs import db_io
from capitalscan.jobs.config import config_hash, resolve_config
from capitalscan.research import cell_stats as cs
from capitalscan.research.baselines import attach_event_baselines

OUT = Path(__file__).resolve().parent
BLOCKS = ("date", "month", "quarter")
BOOT_LENGTHS = (10, 21, 63)
B = 4000
SEED = 20261118
COLS = (
    "ticker, signal_date, signal_type, side, dd_bucket, era, split_key, entry_kind,"
    " is_cluster_head, cluster_id, fwd_window_days, cofire_count,"
    " fwd_ret_1d, fwd_ret_2d, fwd_ret_3d, fwd_ret_5d, fwd_ret_10d,"
    " net_ret, gross_ret, mfe, mae, time_to_mfe, capture_ratio, exit_reason,"
    " earnings_in_window, touched_2pct, day_touched_2pct, touched_3pct,"
    " day_touched_3pct, touched_5pct, day_touched_5pct, touched_10pct, day_touched_10pct"
)


def load(engine, chash: str, split: str) -> pd.DataFrame:
    if split not in ("train", "validate"):
        raise ValueError("train and validate only; the holdout is evaluated once, at the end")
    sql = (
        f"SELECT {COLS} FROM events WHERE config_hash = :c AND split_key = :s"
        " AND in_trade AND entry_kind = :k"
    )
    with engine.connect() as conn:
        return pd.read_sql(
            text(sql), conn, params={"c": chash, "s": split, "k": cs.GRID_ENTRY_KIND}
        )


def block_labels(dates: pd.Series, block: str) -> np.ndarray:
    sd = pd.to_datetime(dates)
    return {
        "date": sd.dt.strftime("%Y-%m-%d"),
        "month": sd.dt.strftime("%Y-%m"),
        "quarter": sd.dt.year.astype(str) + "Q" + sd.dt.quarter.astype(str),
    }[block].to_numpy()


def two_way_var(y: np.ndarray, g1: np.ndarray, g2: np.ndarray) -> float:
    """CGM two-way variance of mean(y); falls back to the larger one-way term
    when the two-way sum comes out non-positive."""
    n = len(y)
    r = y - y.mean()

    def v(groups) -> float:
        s = pd.Series(r).groupby(groups).sum().to_numpy()
        return float((s**2).sum() / n**2)

    inter = np.char.add(np.char.add(g1.astype(str), "|"), g2.astype(str))
    v1, v2, v12 = v(g1), v(g2), v(inter)
    vt = v1 + v2 - v12
    return vt if vt > 0 else max(v1, v2)


def boot_weights(n_dates: int, length: int, rng: np.random.Generator) -> np.ndarray:
    """B x n_dates counts: how often each date lands in a circular
    moving-block resample of the same length."""
    n_blocks = math.ceil(n_dates / length)
    starts = rng.integers(0, n_dates, size=(B, n_blocks))
    idx = (starts[:, :, None] + np.arange(length)[None, None, :]) % n_dates
    idx = idx.reshape(B, -1)[:, :n_dates]
    w = np.zeros((B, n_dates), dtype=np.float64)
    rows = np.repeat(np.arange(B), n_dates)
    np.add.at(w, (rows, idx.ravel()), 1.0)
    return w


def run(split: str, engine, cfg, chash: str, rho_era: pd.DataFrame) -> pd.DataFrame:
    sp = cfg.stats
    bp = BaselineParams()
    horizon = bp.horizon_days
    raw = load(engine, chash, split)
    events, _ = cs.select_grid_events(raw, sp, cs.min_fwd_window_for(cfg))
    heads = events.loc[events["is_cluster_head"].fillna(False).astype(bool)]
    baselines = cs.build_baselines(engine, events, sp, bp)

    shipped = cs.compute_grid(
        heads.drop(columns=["cluster_id"]), rho_era, cfg, baselines=baselines, bp=bp
    )
    shipped = shipped.loc[shipped["era"].isna()]

    all_dates = np.sort(pd.to_datetime(events["signal_date"]).dt.normalize().unique())
    date_pos = {d: i for i, d in enumerate(all_dates)}
    rng = np.random.default_rng(SEED + (0 if split == "train" else 1))
    weights = {L: boot_weights(len(all_dates), L, rng) for L in BOOT_LENGTHS}

    rows = []
    for spec in headline_grid(sp):
        cell = events.loc[
            (events["side"] == spec.side)
            & (events["signal_type"] == spec.signal_type)
            & (events["dd_bucket"] == spec.dd_bucket)
        ]
        if cell.empty:
            continue
        direction = -1 if spec.side == "short" else 1
        for target in sp.reach_targets:
            target = float(target)
            ship = shipped.loc[
                (shipped["side"] == spec.side)
                & (shipped["signal_type"] == spec.signal_type)
                & (shipped["dd_bucket"] == spec.dd_bucket)
                & np.isclose(shipped["target_pct"].astype(float), target)
            ].iloc[0]

            flags = cs.hit_flags(cell, target, horizon)
            c = cell.loc[flags.notna()].copy()
            c["hit"] = flags[flags.notna()].astype(float).to_numpy()
            c = attach_event_baselines(c, baselines[direction], target)
            c = c.loc[c["baseline_empirical"].notna()]
            y = c["hit"].to_numpy()
            b = c["baseline_empirical"].astype(float).to_numpy()
            d = y - b
            clus = c["cluster_id"].astype(str).to_numpy()
            p_all, b_all, edge = float(y.mean()), float(b.mean()), float(d.mean())

            row = {
                "split": split,
                "side": spec.side,
                "signal_type": spec.signal_type,
                "dd_bucket": spec.dd_bucket,
                "target": target,
                "n_heads": int(ship["n_events"]),
                "n_all": len(c),
                "p_heads": ship["p_hit"],
                "baseline_heads": ship["baseline_empirical"],
                "neff_shipped": float(ship["n_eff"]),
                "suppressed_shipped": bool(ship["suppressed"]),
                "p_shipped": ship["p_value_parametric"],
                "q_shipped": ship["q_value"],
                "p_all": p_all,
                "baseline_all": b_all,
                "edge": edge,
            }

            v_hit = two_way_var(y, block_labels(c["signal_date"], "date"), clus)
            # a cell with no variance (every fire missed, or every fire hit)
            # has no interval to test; it is suppressed, not significant
            neff_r = p_all * (1 - p_all) / v_hit if v_hit > 0 else float("nan")
            row["neff_robust"] = neff_r
            row["suppressed_robust"] = not (neff_r >= sp.min_n_eff)
            row["p_level"] = one_sided_p_value(p_all, b_all, neff_r) if neff_r > 0 else None

            for block in BLOCKS:
                se = math.sqrt(two_way_var(d, block_labels(c["signal_date"], block), clus))
                row[f"se_diff_{block}"] = se
                row[f"p_diff_{block}"] = float(sst.norm.sf(edge / se)) if se > 0 else None

            pos = np.array([date_pos[x] for x in pd.to_datetime(c["signal_date"]).dt.normalize()])
            s_d = np.bincount(pos, weights=d, minlength=len(all_dates))
            s_n = np.bincount(pos, minlength=len(all_dates)).astype(float)
            for L, w in weights.items():
                den = w @ s_n
                ok = den > 0
                e_star = (w[ok] @ s_d) / den[ok]
                # one-sided, centred: how often the resampled deviation is as
                # large as the observed edge under a zero-edge null
                row[f"p_boot_L{L}"] = float(((e_star - edge) >= edge).mean())
                row[f"se_boot_L{L}"] = float(e_star.std(ddof=1))
                row[f"ci_lo_boot_L{L}"] = float(np.quantile(e_star, 0.025))
                row[f"ci_hi_boot_L{L}"] = float(np.quantile(e_star, 0.975))
            rows.append(row)
    return pd.DataFrame(rows)


def bh(df: pd.DataFrame, pcol: str, mask: pd.Series, alpha: float) -> pd.Series:
    q = pd.Series(np.nan, index=df.index)
    m = mask & df[pcol].notna()
    if m.any():
        _, qs = benjamini_hochberg(df.loc[m, pcol].to_numpy(dtype="float64"), alpha=alpha)
        q.loc[m] = qs
    return q


def summarise(df: pd.DataFrame, alpha: float) -> pd.DataFrame:
    tests = ["level", *[f"diff_{b}" for b in BLOCKS], *[f"boot_L{L}" for L in BOOT_LENGTHS]]
    out = []
    for split, g in df.groupby("split", sort=False):
        g = g.copy()
        print(f"\n== {split}: {len(g)} cell-targets")
        fam_s = ~g["suppressed_shipped"] & g["p_shipped"].notna()
        print(
            f"  shipped     family {int(fam_s.sum()):>2}  min q "
            f"{g.loc[fam_s, 'q_shipped'].min() if fam_s.any() else float('nan'):.4f}"
            f"  survive {int((g['q_shipped'] < alpha).sum())}"
        )
        fam_r = ~g["suppressed_robust"]
        everyone = pd.Series(True, index=g.index)
        for t in tests:
            g[f"q_{t}"] = bh(g, f"p_{t}", fam_r, alpha)
            g[f"q_{t}_all64"] = bh(g, f"p_{t}", everyone, alpha)
            s, s64 = int((g[f"q_{t}"] < alpha).sum()), int((g[f"q_{t}_all64"] < alpha).sum())
            print(
                f"  {t:<11} family {int(fam_r.sum()):>2}  min p {g[f'p_{t}'].min():.2e}"
                f"  min q {g[f'q_{t}'].min():.4f}  survive {s}  (all-64 family: {s64})"
            )
        print(
            f"  edge > 0 in {int((g.edge > 0).sum())}/{len(g)}  median edge {g.edge.median():+.4f}"
        )
        out.append(g)
    return pd.concat(out)


def main() -> int:
    engine = db_io.get_engine()
    if engine.url.host != "localhost":
        print("refusing: this runs against the workstation copy only", file=sys.stderr)
        return 2
    cfg = resolve_config()
    chash = config_hash(cfg)
    rho_era = cs.load_rho_era(engine, chash)
    df = pd.concat([run(s, engine, cfg, chash, rho_era) for s in ("train", "validate")])
    df = summarise(df.reset_index(drop=True), cfg.stats.fdr_alpha)
    path = OUT / "correction_test.csv"
    df.to_csv(path, index=False)
    print(f"\nconfig {chash}, B={B}, wrote {path.name} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
