"""Arm D, re-derived on the six-head model and ADR 193's window (BACKLOG item 6).

The 2026-09-05 arms (RESULTS "Combined beats individual, but only on
calibration") had arm D -- sector-relative features plus `net_ret` / `mae`
training tasks -- winning Brier skill at every threshold with AUC flat, on
a four-head model and the fixed 2010-21 / 2022-23 split. Both have changed:
six heads (ADR 175) and the expanding window (ADR 193). The original script
is not in the repo, so this reconstructs the arm from its RESULTS entry.

    A_base      production features and tasks
    D_both      + rel_dd, sector_dd_med   (features)
                + ("net", 5) -> net_ret, ("mae", 5) -> mae   (training tasks)

**Measurement only, not an adoption.** `net_ret` and `mae` are exit-based,
so `ExitParams` is baked into them; ADR 175 chose `trough_ret_*` over `mae`
for exactly that reason. Adopting D's tasks would need that reasoning
revisited, which is the owner's call.

Population held constant: both arms read frames built with `net_ret` and
`mae` added to the required labels, so only features and tasks differ.

Sector features at t-1: the median `dd_52w` of the sector's tickers on the
last session STRICTLY before the signal date (from `indicators`), and the
ticker's own event `dd_52w` minus it (invariant 3).

Same anchor as the breadth run: T = 2025-09-26, production's window shape,
scored on the following twelve months. Read-only against `wivie`.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = os.environ.get(
    "ARMD_EXP_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sklearn.metrics import roc_auc_score  # noqa: E402
from sqlalchemy import text  # noqa: E402

from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural, train  # noqa: E402
from capitalscan.research import predict as rp  # noqa: E402

CHASH = "f183b0f5209a4677"
T = date(2025, 9, 26)
EVAL = (T, date(2026, 9, 25))
SECTOR_FEATS = ("rel_dd", "sector_dd_med")
EXTRA_LABELS = ("net_ret", "mae")
EXTRA_TASKS = (("net", 5), ("mae", 5))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results.csv")


def sector_medians(engine) -> pd.DataFrame:
    """Median dd_52w per (sector, session), across every ticker with a row."""
    with engine.connect() as conn:
        m = pd.read_sql(
            text(
                "SELECT i.ts::date AS d, t.sector, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY i.dd_52w) AS sector_dd_med "
                "FROM indicators i JOIN tickers t ON t.ticker = i.ticker "
                "WHERE i.interval = '1d' AND i.dd_52w IS NOT NULL AND t.sector IS NOT NULL "
                "AND i.ts >= '2009-01-01' GROUP BY 1, 2 ORDER BY 1"
            ),
            conn,
        )
    m["d"] = pd.to_datetime(m["d"])
    return m


def with_sector(frame: pd.DataFrame, med: pd.DataFrame) -> pd.DataFrame:
    sig = pd.DataFrame(
        {
            "d": pd.to_datetime(frame["signal_date"]).values,
            "sector": frame["sector"].astype(str).values,
            "_i": np.arange(len(frame)),
        }
    ).sort_values("d")
    merged = pd.merge_asof(
        sig, med.sort_values("d"), on="d", by="sector", allow_exact_matches=False
    ).sort_values("_i")
    out = frame.copy()
    out["sector_dd_med"] = merged["sector_dd_med"].to_numpy(float)
    out["rel_dd"] = pd.to_numeric(out["dd_52w"], errors="coerce").to_numpy(float) - out[
        "sector_dd_med"
    ].to_numpy(float)
    return out


def score(frame: pd.DataFrame, applied: pd.DataFrame, arm: str, rows: list[dict]) -> None:
    side = frame["side"].astype(str)
    for target in rp.TARGETS:
        labels = pd.to_numeric(frame[target.label_column], errors="coerce").to_numpy(float)
        keep = ~np.isnan(labels)
        y = target.realised(labels)
        p = applied[target.field].to_numpy(float)
        base = float(np.mean(y[keep]))
        for scope, mask in [("all", keep)] + [
            (f"side={s}", keep & (side == s).to_numpy()) for s in ("long", "short")
        ]:
            yy, pp = y[mask], p[mask]
            brier = float(np.mean((pp - yy) ** 2))
            clim = float(np.mean((base - yy) ** 2))
            rows.append(
                {
                    "arm": arm,
                    "field": target.field,
                    "scope": scope,
                    "n": int(mask.sum()),
                    "mean_pred": float(pp.mean()),
                    "realised": float(yy.mean()),
                    "gap": float(yy.mean() - pp.mean()),
                    "skill_vs_eval_base": 1 - brier / clim if clim > 0 else np.nan,
                    "auc": float(roc_auc_score(yy, pp)) if len(set(yy)) == 2 else np.nan,
                }
            )


def main() -> None:
    engine = db_io.get_engine()
    print(f"db: {engine.url.host}/{engine.url.database}  T={T}  eval={EVAL}", flush=True)
    med = sector_medians(engine)
    print(f"sector medians: {len(med):,} rows", flush=True)

    base_labels = feat.LABEL_COLS
    feat.LABEL_COLS = base_labels + EXTRA_LABELS  # same population for both arms
    real_build = feat.build_training_frame
    real_label_for = train.label_for
    base_cols, base_tasks = feat.FEATURE_COLS, neural.TASKS

    def label_for(family: str, horizon: int) -> str:
        return {"net": "net_ret", "mae": "mae"}.get(family) or real_label_for(family, horizon)

    ev, rep = real_build(engine, CHASH, window=EVAL)
    ev = with_sector(ev, med)
    print(f"eval frame: {rep}  sector NaN: {int(ev['sector_dd_med'].isna().sum())}", flush=True)

    rows: list[dict] = []
    for arm, feats, tasks in (
        ("A_base", (), ()),
        ("D_both", SECTOR_FEATS, EXTRA_TASKS),
    ):
        feat.FEATURE_COLS = base_cols + tuple(feats)
        neural.TASKS = base_tasks + tuple(tasks)
        train.label_for = label_for

        def build(engine_, chash_, split="train", require_labels=True, window=None):
            f, r = real_build(
                engine_, chash_, split=split, require_labels=require_labels, window=window
            )
            return with_sector(f, med), r

        feat.build_training_frame = build
        try:
            print(
                f"\n=== {arm}: {len(feat.FEATURE_COLS)} features, {len(neural.TASKS)} tasks ===",
                flush=True,
            )
            predictor = rp.fit_and_calibrate(engine, CHASH, "armdexp", today=T)
            print(
                f"  n_train={predictor.n_train:,} n_calibrate={predictor.n_calibrate:,} "
                f"steps={[m.steps for m in predictor.ensemble.members]}",
                flush=True,
            )
            score(ev, predictor.apply(ev), arm, rows)
        finally:
            feat.build_training_frame = real_build
            feat.FEATURE_COLS, neural.TASKS = base_cols, base_tasks
            train.label_for = real_label_for
        pd.DataFrame(rows).to_csv(OUT, index=False)

    feat.LABEL_COLS = base_labels
    d = pd.DataFrame(rows)
    print("\n" + "=" * 78 + f"\nOUT OF SAMPLE {EVAL[0]} .. {EVAL[1]}\n" + "=" * 78)
    print(
        d.pivot_table(
            index=["field", "scope"], columns="arm", values=["skill_vs_eval_base", "auc", "gap"]
        )
        .round(4)
        .to_string()
    )
    print(f"\nresults: {OUT}\n=== DONE ===")


if __name__ == "__main__":
    sys.exit(main())
