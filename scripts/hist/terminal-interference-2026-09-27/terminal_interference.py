"""Is the `terminal` family miscalibrated because it shares a trunk? (BACKLOG)

The coverage gate splits 10/10 `peak`, 10/10 `trough`, 6/10 `terminal`
(2026-09-08), and the four failures are all `terminal` heads. Multi-task
interference was tested and refuted earlier (5/30 failing both ways), but
that test pooled all families -- the pooling this project has since learned
hides exactly this kind of signal. This re-runs it per family.

    multi       production's six tasks
    terminal    ("terminal", 5), ("terminal", 10) only

Same frames, seeds and window for both: production's shape anchored at
T = 2025-09-26 (ADR 193). Coverage per head, cluster-weighted, scored by
the project's own `promotion.score_family` on the calibration window, and
directly on the following twelve months. If `terminal`-only covers
materially better, interference is real for this family; if not, the cause
is elsewhere.

Read-only against `wivie`.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = os.environ.get(
    "TERM_EXP_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sqlalchemy import text  # noqa: E402

from capitalscan.core import distributions as dist  # noqa: E402
from capitalscan.core import folds as cf  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.config import resolve_config  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural, promotion, train  # noqa: E402

CHASH = "f183b0f5209a4677"
T = date(2025, 9, 26)
EVAL = (T, date(2026, 9, 25))
TOL = 0.05
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results.csv")


def coverage(frame: pd.DataFrame, fan: dict, family: str, horizon: int) -> dict[float, float]:
    y = pd.to_numeric(frame[train.label_for(family, horizon)], errors="coerce").to_numpy(float)
    w = np.asarray(cf.cluster_weights(list(frame["cluster_id"])))
    ok = ~np.isnan(y)
    return {
        tau: float(((y[ok] <= fan[tau][ok]).astype(float) * w[ok]).sum() / w[ok].sum())
        for tau in train.TAUS
    }


def main() -> None:
    engine = db_io.get_engine()
    print(f"db: {engine.url.host}/{engine.url.database}  T={T}", flush=True)
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())
    win = cf.training_window(T, date.fromisoformat(resolve_config().splits.event_start))
    tr, _ = feat.build_training_frame(engine, CHASH, window=(win.train_start, win.train_end))
    va, _ = feat.build_training_frame(engine, CHASH, window=(win.validate_start, win.validate_end))
    ev, _ = feat.build_training_frame(engine, CHASH, window=EVAL)
    print(f"train {len(tr):,}  validate {len(va):,}  eval {len(ev):,}", flush=True)

    base_tasks = neural.TASKS
    rows: list[dict] = []
    for arm, tasks in (
        ("multi", base_tasks),
        ("terminal_only", (("terminal", 5), ("terminal", 10))),
    ):
        neural.TASKS = tasks
        try:
            print(f"\n=== {arm}: {len(tasks)} tasks ===", flush=True)
            ens = neural.fit(tr, calendar, seeds=neural.DEFAULT_SEEDS)
            print(f"  steps {[m.steps for m in ens.members]}", flush=True)
            for window_name, frame in (("validate", va), ("eval", ev)):
                pmf = ens.predict_pmf(frame)
                for horizon in (5, 10):
                    k = tasks.index(("terminal", horizon))
                    fan = dist.quantiles_from_pmf(pmf[:, k, :], ens.grids[k], train.TAUS)
                    if window_name == "validate":
                        for e in promotion.score_family(tr, va, "terminal", horizon, fan):
                            rows.append(
                                {
                                    "arm": arm,
                                    "window": "validate_gate",
                                    "head": e.head,
                                    "tau": e.tau,
                                    "cov": e.coverage,
                                    "cov_err": e.coverage_error,
                                    "cov_ok": e.coverage_ok,
                                }
                            )
                    for tau, c in coverage(frame, fan, "terminal", horizon).items():
                        rows.append(
                            {
                                "arm": arm,
                                "window": window_name,
                                "head": train.head_name("terminal", horizon, tau),
                                "tau": tau,
                                "cov": c,
                                "cov_err": c - tau,
                                "cov_ok": abs(c - tau) <= TOL,
                            }
                        )
        finally:
            neural.TASKS = base_tasks
        pd.DataFrame(rows).to_csv(OUT, index=False)

    d = pd.DataFrame(rows).assign(ae=lambda x: x["cov_err"].abs())
    print("\n" + "=" * 72)
    print(
        d.pivot_table(index=["window", "head"], columns="arm", values="cov_err")
        .round(4)
        .to_string()
    )
    print("\nheads within tolerance / mean |error|:")
    print(
        d.groupby(["window", "arm"])
        .agg(ok=("cov_ok", "sum"), n=("cov_ok", "size"), mean_abs=("ae", "mean"))
        .round(4)
        .to_string()
    )
    print(f"\nresults: {OUT}\n=== DONE ===")


if __name__ == "__main__":
    sys.exit(main())
