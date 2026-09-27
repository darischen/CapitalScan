"""Can P(stop) be read off the trough head? (BACKLOG item 5)

`research.predict.expected_net_return` needs P(stop) and nothing supplies
it. Measured first on 161,642 exited trades (RESULTS 2026-09-27):

- 92.3% of trades whose 5-day trough crossed the stop level exited on the
  stop; target-first was 2.3%. So the ordering the backlog worried about
  costs little, and P(stop) ~ P(trough <= -stop distance).
- `p_adverse_3` is the wrong proxy: the stop is 2 x ATR, so across
  stop-distance quintiles the realised 3% adverse rate rises 0.20 -> 0.57
  while the stop rate falls 0.27 -> 0.18. On the forward log its AUC for
  the stop is 0.487.

This tests the estimator that follows: the trough head's own CDF read at
each event's stop distance, `shortfall(pmf, grid, -2 x atr_14 / entry)`,
then calibrated on the validate window exactly as production calibrates
`p_adverse_*` (`calib.build_reliability`, cluster weights). One production
fit anchored at T = 2025-09-26; scored on the following twelve months.

Read-only against `wivie`'s research database.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = os.environ.get(
    "PSTOP_EXP_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sklearn.metrics import roc_auc_score  # noqa: E402
from sqlalchemy import text  # noqa: E402

from capitalscan.core import calibration as calib  # noqa: E402
from capitalscan.core import distributions as dist  # noqa: E402
from capitalscan.core import folds as cf  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.config import resolve_config  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural  # noqa: E402
from capitalscan.research import predict as rp  # noqa: E402

CHASH = "f183b0f5209a4677"
T = date(2025, 9, 26)
EVAL = (T, date(2026, 9, 25))
HERE = os.path.dirname(os.path.abspath(__file__))


def exits(engine, ids: list[int]) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT id, exit_reason, atr_14::float AS atr_14, "
                "entry_price::float AS entry_price FROM events WHERE id = ANY(:ids)"
            ),
            conn,
            params={"ids": ids},  # type: ignore[arg-type]
        )


def attach(engine, frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.merge(exits(engine, [int(i) for i in frame["id"]]), on="id", how="left")
    x["stop_d"] = resolve_config().exits.stop_atr_k * x["atr_14"] / x["entry_price"]
    x["stopped"] = (x["exit_reason"] == "stop").astype(float)
    ok = x["exit_reason"].notna() & (x["exit_reason"] != "unfinished") & x["stop_d"].notna()
    return x.loc[ok].reset_index(drop=True)


def reach_prob(ens, frame: pd.DataFrame) -> np.ndarray:
    """P(trough_5d <= -stop_d) per row, each at its own threshold."""
    pmf = ens.predict_pmf(frame)
    k = neural.TASKS.index(("trough", 5))
    grid = ens.grids[k]
    thr = -frame["stop_d"].to_numpy(float)
    out = np.empty(len(frame))
    for i in range(len(frame)):
        out[i] = dist.shortfall(pmf[i : i + 1, k, :], grid, float(thr[i]))[0]
    return out


def report(name: str, p: np.ndarray, y: np.ndarray, frame: pd.DataFrame) -> dict:
    brier = float(np.mean((p - y) ** 2))
    clim = float(np.mean((y.mean() - y) ** 2))
    row = {
        "estimator": name,
        "n": len(y),
        "mean_pred": float(p.mean()),
        "realised": float(y.mean()),
        "auc": float(roc_auc_score(y, p)),
        "brier_skill": 1 - brier / clim,
    }
    print(
        f"  {name:34} mean {row['mean_pred']:.4f} vs {row['realised']:.4f}  "
        f"AUC {row['auc']:.4f}  Brier skill {row['brier_skill']:+.4f}",
        flush=True,
    )
    q = pd.qcut(frame["stop_d"], 5)
    t = (
        pd.DataFrame({"q": q, "p": p, "y": y})
        .groupby("q", observed=True)
        .agg(n=("y", "size"), pred=("p", "mean"), real=("y", "mean"))
    )
    print("    by stop-distance quintile:\n" + t.round(4).to_string().replace("\n", "\n    "))
    return row


def main() -> None:
    engine = db_io.get_engine()
    print(f"db: {engine.url.host}/{engine.url.database}  T={T}  eval={EVAL}", flush=True)
    win = cf.training_window(T, date.fromisoformat(resolve_config().splits.event_start))
    tr, _ = feat.build_training_frame(engine, CHASH, window=(win.train_start, win.train_end))
    va, _ = feat.build_training_frame(engine, CHASH, window=(win.validate_start, win.validate_end))
    ev, _ = feat.build_training_frame(engine, CHASH, window=EVAL)
    va, ev = attach(engine, va), attach(engine, ev)
    print(f"train {len(tr):,}  validate {len(va):,}  eval {len(ev):,}", flush=True)

    ens = neural.fit(tr, list(_calendar(engine)), seeds=neural.DEFAULT_SEEDS)
    print(f"steps {[m.steps for m in ens.members]}", flush=True)

    raw_va, raw_ev = reach_prob(ens, va), reach_prob(ens, ev)
    w = np.asarray(cf.cluster_weights(list(va["cluster_id"])))
    table = calib.build_reliability("p_stop", raw_va, va["stopped"].to_numpy(float), weights=w)
    cal_ev = np.array([table.calibrate(float(v)) for v in raw_ev])

    # The published p_adverse_3 for the same rows, calibrated the same way,
    # for the head-to-head the backlog asked about.
    tgt = next(t for t in rp.TARGETS if t.field == "p_adverse_3")
    k3 = neural.TASKS.index((tgt.family, tgt.horizon))
    adv_va = tgt.probability(ens.predict_pmf(va)[:, k3, :], ens.grids[k3])
    lab = pd.to_numeric(va[tgt.label_column], errors="coerce").to_numpy(float)
    keep = ~np.isnan(lab)
    adv_tab = calib.build_reliability(
        "p_adverse_3", adv_va[keep], tgt.realised(lab[keep]), weights=w[keep]
    )
    adv_ev = np.array(
        [
            adv_tab.calibrate(float(v))
            for v in tgt.probability(ens.predict_pmf(ev)[:, k3, :], ens.grids[k3])
        ]
    )

    y = ev["stopped"].to_numpy(float)
    print(f"\nOUT OF SAMPLE {EVAL[0]} .. {EVAL[1]}: stop rate {y.mean():.4f} on {len(y):,} exits")
    rows = [
        report("p_adverse_3 (calibrated) as P(stop)", adv_ev, y, ev),
        report("trough CDF at own stop, raw", raw_ev, y, ev),
        report("trough CDF at own stop, calibrated", cal_ev, y, ev),
    ]
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "results.csv"), index=False)
    print("\n=== DONE ===")


def _calendar(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars().all()


if __name__ == "__main__":
    sys.exit(main())
