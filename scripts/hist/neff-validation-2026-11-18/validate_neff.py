"""ADR 166's validation: cluster-robust intervals on every fire, against the
shipped cluster-head intervals. Read-only.

    uv run python scripts/hist/neff-validation-2026-11-18/validate_neff.py

For each headline cell (ADR 102's grid) and each reach target, on train and
validate:

- **shipped**: `research.cell_stats` exactly as `cscan cell-stats` runs it --
  cluster heads only, `n_eff = n / (1 + rho_bar (k_bar - 1))` (ADR 098),
  Wilson interval on `p_hit` over `n_eff`.
- **robust**: every fire in the same population, `p_hit` with a two-way
  cluster-robust variance (Cameron, Gelbach & Miller 2011), clustered by
  `signal_date` (cross-sectional co-firing) and by `cluster_id` (serial
  overlap), minus the intersection. A Wald interval on that variance, and
  the `n_eff` it implies: `p (1 - p) / V`, the independent-sample size that
  gives the same width.

Writes a CSV per run next to this file and prints the comparison ADR 166
asks for. Nothing is written to any database.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from capitalscan.core.cells import headline_grid
from capitalscan.core.config import BaselineParams
from capitalscan.core.stats import wilson_ci
from capitalscan.jobs import db_io
from capitalscan.jobs.config import config_hash, resolve_config
from capitalscan.research import cell_stats as cs

OUT = Path(__file__).resolve().parent
TIME_BLOCK = sys.argv[1] if len(sys.argv) > 1 else "date"
Z = 1.959963984540054  # two-sided 95%, the level wilson_ci uses by default


def load(engine, chash: str, split: str) -> pd.DataFrame:
    cols = (
        "ticker, signal_date, signal_type, side, dd_bucket, era, split_key, entry_kind,"
        " is_cluster_head, cluster_id, fwd_window_days, cofire_count, touched_2pct,"
        " day_touched_2pct, touched_3pct, day_touched_3pct, touched_5pct, day_touched_5pct,"
        " touched_10pct, day_touched_10pct, mfe, mae, net_ret,"
        " fwd_ret_1d, fwd_ret_2d, fwd_ret_3d, fwd_ret_5d, fwd_ret_10d"
    )
    sql = (
        f"SELECT {cols} FROM events WHERE config_hash = :c AND split_key = :s"
        " AND in_trade AND entry_kind = :k"
    )
    with engine.connect() as conn:
        return pd.read_sql(
            text(sql), conn, params={"c": chash, "s": split, "k": cs.GRID_ENTRY_KIND}
        )


def two_way_variance(y: np.ndarray, g1: np.ndarray, g2: np.ndarray) -> tuple[float, float, float]:
    """Variance of mean(y) clustered on g1, g2 and their intersection.

    Returns (v_two_way, v_g1, v_g2). For a sample mean the cluster-robust
    sandwich reduces to sum over clusters of (sum of residuals)^2 / n^2.
    """
    n = len(y)
    r = y - y.mean()

    def v(groups: np.ndarray) -> float:
        s = pd.Series(r).groupby(groups).sum().to_numpy()
        return float((s**2).sum() / n**2)

    inter = pd.Series(list(zip(g1, g2))).astype(str).to_numpy()
    v1, v2, v12 = v(g1), v(g2), v(inter)
    return v1 + v2 - v12, v1, v2


def run(split: str, engine, cfg, chash: str, rho_era: pd.DataFrame) -> pd.DataFrame:
    sp = cfg.stats
    raw = load(engine, chash, split)
    events, _ = cs.select_grid_events(raw, sp, cs.min_fwd_window_for(cfg))
    heads = events.loc[events["is_cluster_head"].fillna(False).astype(bool)]
    horizon = BaselineParams().horizon_days

    rows = []
    for spec in headline_grid(sp):
        sel_all = (
            (events["side"] == spec.side)
            & (events["signal_type"] == spec.signal_type)
            & (events["dd_bucket"] == spec.dd_bucket)
        )
        sel_h = (
            (heads["side"] == spec.side)
            & (heads["signal_type"] == spec.signal_type)
            & (heads["dd_bucket"] == spec.dd_bucket)
        )
        cell_all, cell_h = events.loc[sel_all], heads.loc[sel_h]
        if cell_h.empty or cell_all.empty:
            continue
        _, _, neff_h = cs.cell_n_eff(cell_h, rho_era)
        for target in sp.reach_targets:
            fh = cs.hit_flags(cell_h, float(target), horizon).dropna()
            fa_full = cs.hit_flags(cell_all, float(target), horizon)
            keep = fa_full.notna()
            fa = fa_full[keep].astype(float).to_numpy()
            if len(fh) == 0 or len(fa) == 0:
                continue
            p_h = float(fh.mean())
            lo_h, hi_h = wilson_ci(p_h * neff_h, neff_h)

            sd = pd.to_datetime(cell_all.loc[keep, "signal_date"])
            dates = {
                "date": sd.dt.strftime("%Y-%m-%d"),
                "week": sd.dt.strftime("%G-%V"),
                "month": sd.dt.strftime("%Y-%m"),
                "quarter": sd.dt.year.astype(str) + "Q" + sd.dt.quarter.astype(str),
            }[TIME_BLOCK].to_numpy()
            clus = cell_all.loc[keep, "cluster_id"].astype(str).to_numpy()
            p_a = float(fa.mean())
            v2, v_date, v_clus = two_way_variance(fa, dates, clus)
            neg = v2 <= 0
            v_use = max(v_date, v_clus) if neg else v2
            se = math.sqrt(v_use)
            neff_r = p_a * (1 - p_a) / v_use if v_use > 0 else float("nan")
            rows.append(
                {
                    "split": split,
                    "side": spec.side,
                    "signal_type": spec.signal_type,
                    "dd_bucket": spec.dd_bucket,
                    "target": float(target),
                    "n_heads": len(fh),
                    "n_all": len(fa),
                    "p_heads": p_h,
                    "neff_shipped": neff_h,
                    "ci_lo_shipped": lo_h,
                    "ci_hi_shipped": hi_h,
                    "p_all": p_a,
                    "neff_robust": neff_r,
                    "ci_lo_robust": p_a - Z * se,
                    "ci_hi_robust": p_a + Z * se,
                    "v_two_way_negative": neg,
                    "neff_date_only": p_a * (1 - p_a) / v_date if v_date > 0 else float("nan"),
                    "neff_cluster_only": p_a * (1 - p_a) / v_clus if v_clus > 0 else float("nan"),
                    "min_n_eff": sp.min_n_eff,
                }
            )
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> None:
    df = df.assign(
        width_shipped=df.ci_hi_shipped - df.ci_lo_shipped,
        width_robust=df.ci_hi_robust - df.ci_lo_robust,
    )
    df["width_ratio"] = df.width_robust / df.width_shipped
    df["neff_ratio"] = df.neff_robust / df.neff_shipped
    df["p_shift"] = df.p_all - df.p_heads
    df["heads_p_in_robust_ci"] = (df.p_heads >= df.ci_lo_robust) & (df.p_heads <= df.ci_hi_robust)
    df["overlap"] = (df.ci_lo_robust <= df.ci_hi_shipped) & (df.ci_lo_shipped <= df.ci_hi_robust)
    df["suppressed_shipped"] = df.neff_shipped < df.min_n_eff
    df["suppressed_robust"] = df.neff_robust < df.min_n_eff
    for split, g in df.groupby("split"):
        q = g.width_ratio.quantile([0.1, 0.5, 0.9]).round(3).tolist()
        n_cells = g[["side", "signal_type", "dd_bucket"]].drop_duplicates().shape[0]
        print(f"\n== {split}: {len(g)} cell-targets, {n_cells} cells")
        print(f"  n: heads {int(g.n_heads.sum()):,}  all fires {int(g.n_all.sum()):,}")
        print(f"  width robust/shipped  p10 {q[0]}  median {q[1]}  p90 {q[2]}")
        print(f"  n_eff robust/shipped  median {g.neff_ratio.median():.3f}")
        ps = g.p_shift
        print(
            f"  p_all - p_heads       median {ps.median():+.4f}"
            f"  p10 {ps.quantile(0.1):+.4f}  p90 {ps.quantile(0.9):+.4f}"
        )
        print(f"  shipped p_hit inside robust CI: {int(g.heads_p_in_robust_ci.sum())}/{len(g)}")
        print(f"  intervals overlap: {int(g.overlap.sum())}/{len(g)}")
        n_neg = int(g.v_two_way_negative.sum())
        print(f"  two-way variance negative (fell back to max one-way): {n_neg}")
        sup_s, sup_r = int(g.suppressed_shipped.sum()), int(g.suppressed_robust.sum())
        print(f"  suppressed: shipped {sup_s}  robust {sup_r}")
        dom = (g.neff_date_only < g.neff_cluster_only).mean()
        print(f"  the time block is the binding axis in {dom:.0%} of cell-targets")


def main() -> int:
    engine = db_io.get_engine()
    if engine.url.host != "localhost":
        print("refusing: this runs against the workstation copy only", file=sys.stderr)
        return 2
    cfg = resolve_config()
    chash = config_hash(cfg)
    rho_era = cs.load_rho_era(engine, chash)
    frames = [run(s, engine, cfg, chash, rho_era) for s in ("train", "validate")]
    df = pd.concat(frames, ignore_index=True)
    out = OUT / f"neff_validation_{TIME_BLOCK}.csv"
    df.to_csv(out, index=False)
    print(f"config {chash}, time block {TIME_BLOCK}, wrote {out.name} ({len(df)} rows)")
    summarise(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
