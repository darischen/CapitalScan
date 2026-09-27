"""Does market breadth improve Brier skill, or the published level? (BACKLOG item 3b)

The 2026-09-07 breadth arms scored quantile COVERAGE only. Item 3b was
retired because coverage was the wrong yardstick, with one open question
left: breadth measured against Brier skill and the level bias. This is that
experiment, and nothing else.

    both arms   production's refit, `research.predict.fit_and_calibrate`,
                anchored at T = 2025-09-26 (ADR 193's window shape):
                train 2010 .. T-6mo-10d, calibrate T-6mo .. T-5d
    evaluate    T .. T+12mo, never seen by either arm
    arm A       production features
    arm B       + breadth_ma_above and breadth_chg_60d at t-1

Breadth is attached as of the last session STRICTLY before the signal date
(`merge_asof`, `allow_exact_matches=False`): a touch fires intraday, before
that day's breadth exists. Same frames, seeds and calibration for both
arms; only `features.FEATURE_COLS` differs.

Read-only against `wivie`'s research database; nothing is written anywhere
but this folder's CSV.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = os.environ.get(
    "BREADTH_EXP_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sklearn.metrics import roc_auc_score  # noqa: E402
from sqlalchemy import text  # noqa: E402

from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import predict as rp  # noqa: E402

CHASH = "f183b0f5209a4677"
T = date(2025, 9, 26)
EVAL = (T, date(2026, 9, 25))
BREADTH = ("breadth_ma_above", "breadth_chg_60d")
HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS = tuple(int(x) for x in os.environ.get("BREADTH_SEEDS", "").split(",") if x)
OUT = os.path.join(HERE, f"results{'_seeds_' + '_'.join(map(str, SEEDS)) if SEEDS else ''}.csv")
FIELDS = ("p_touch_2", "p_touch_3", "p_touch_5", "p_touch_10", "p_adverse_3", "p_adverse_5")


def load_breadth(engine) -> pd.DataFrame:
    with engine.connect() as conn:
        m = pd.read_sql(
            text(
                "SELECT ts::date AS d, breadth_ma_above, breadth_chg_60d FROM market_days "
                "WHERE breadth_ma_above IS NOT NULL ORDER BY ts"
            ),
            conn,
        )
    m["d"] = pd.to_datetime(m["d"])
    return m


def with_breadth(frame: pd.DataFrame, breadth: pd.DataFrame) -> pd.DataFrame:
    sig = pd.DataFrame(
        {"d": pd.to_datetime(frame["signal_date"]).values, "_i": np.arange(len(frame))}
    )
    merged = pd.merge_asof(
        sig.sort_values("d"), breadth, on="d", allow_exact_matches=False
    ).sort_values("_i")
    out = frame.copy()
    for c in BREADTH:
        out[c] = merged[c].to_numpy(float)
    return out


def score(frame: pd.DataFrame, applied: pd.DataFrame, arm: str, rows: list[dict]) -> None:
    month = pd.to_datetime(frame["signal_date"]).dt.to_period("M").astype(str)
    side = frame["side"].astype(str)
    for target in rp.TARGETS:
        labels = pd.to_numeric(frame[target.label_column], errors="coerce").to_numpy(float)
        keep = ~np.isnan(labels)
        y = target.realised(labels)
        p = applied[target.field].to_numpy(float)
        base = float(np.mean(y[keep]))
        for scope, mask in (
            [("all", keep)]
            + [(f"side={s}", keep & (side == s).to_numpy()) for s in ("long", "short")]
            + [(f"month={m}", keep & (month == m).to_numpy()) for m in sorted(month.unique())]
        ):
            if mask.sum() < 30:
                continue
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
                    "brier": brier,
                    "skill_vs_eval_base": 1 - brier / clim if clim > 0 else np.nan,
                    "auc": float(roc_auc_score(yy, pp)) if len(set(yy)) == 2 else np.nan,
                }
            )


def main() -> None:
    engine = db_io.get_engine()
    print(f"db: {engine.url.host}/{engine.url.database}  T={T}  eval={EVAL}", flush=True)
    breadth = load_breadth(engine)
    print(f"breadth rows: {len(breadth)}  latest {breadth['d'].max().date()}", flush=True)

    real_build = feat.build_training_frame
    ev, rep = real_build(engine, CHASH, window=EVAL)
    ev = with_breadth(ev, breadth)
    print(f"eval frame: {rep}  breadth NaN: {int(ev['breadth_ma_above'].isna().sum())}", flush=True)

    rows: list[dict] = []
    base_cols = feat.FEATURE_COLS
    for arm, extra in (("A_base", ()), ("B_breadth", BREADTH)):
        feat.FEATURE_COLS = base_cols + tuple(extra)

        def build(engine_, chash_, split="train", require_labels=True, window=None, _x=extra):
            f, r = real_build(
                engine_, chash_, split=split, require_labels=require_labels, window=window
            )
            return (with_breadth(f, breadth) if _x else f), r

        feat.build_training_frame = build
        try:
            print(f"\n=== {arm}: features {len(feat.FEATURE_COLS)} ===", flush=True)
            kw = {"seeds": SEEDS} if SEEDS else {}
            predictor = rp.fit_and_calibrate(engine, CHASH, "breadthexp", today=T, **kw)
            print(
                f"  n_train={predictor.n_train:,} n_calibrate={predictor.n_calibrate:,} "
                f"steps={[m.steps for m in predictor.ensemble.members]}",
                flush=True,
            )
            applied = predictor.apply(ev)
            score(ev, applied, arm, rows)
        finally:
            feat.build_training_frame = real_build
            feat.FEATURE_COLS = base_cols
        pd.DataFrame(rows).to_csv(OUT, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 78)
    print(f"OUT OF SAMPLE {EVAL[0]} .. {EVAL[1]}")
    print("=" * 78)
    a = d[d.scope.isin(["all", "side=long", "side=short"])]
    print(
        a.pivot_table(
            index=["field", "scope"], columns="arm", values=["gap", "skill_vs_eval_base", "auc"]
        )
        .round(4)
        .to_string()
    )
    m = d[d.scope.str.startswith("month=") & (d.field == "p_touch_3")]
    m = m.assign(abs_gap=m.gap.abs())
    print("\np_touch_3 by month: |realised - predicted|")
    print(m.pivot_table(index="scope", columns="arm", values="abs_gap").round(4).to_string())
    print("\nmean monthly |gap| p_touch_3:", m.groupby("arm").abs_gap.mean().round(4).to_dict())
    print(f"\nresults: {OUT}\n=== DONE ===")


if __name__ == "__main__":
    sys.exit(main())
