"""Re-measure the ADR 170 validate baseline under the seeding fix (BACKLOG).

`docs/model_spec_adr170.json` records "coverage 17/20, beats constant
18/20, mean improvement +8.015%" on validate, measured before 2026-09-04,
when `_run` built the module before seeding it: a re-run gave 14/20. Every
published figure predates the fix, so the spec implies a reproducibility
the measurement did not have.

This re-measures on the spec's own splits (train = `split_key` train,
validate = `split_key` validate, 2022-23), with today's code: seeded, six
heads (ADR 175), touch entry (ADR 177). Scored by the gate's own
`promotion.score_family`, so the numbers mean what the spec's numbers
meant. Run twice with the same seeds to show the fix holds.

Read-only against `wivie`.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = os.environ.get(
    "ADR170_EXP_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sqlalchemy import text  # noqa: E402

from capitalscan.core import distributions as dist  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural, promotion, train  # noqa: E402

CHASH = "f183b0f5209a4677"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results.csv")


def main() -> None:
    engine = db_io.get_engine()
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())
    tr, rep_tr = feat.build_training_frame(engine, CHASH, split="train")
    va, rep_va = feat.build_training_frame(engine, CHASH, split="validate")
    print(f"train {rep_tr}\nvalidate {rep_va}", flush=True)

    rows: list[dict] = []
    for run in (1, 2):
        ens = neural.fit(tr, calendar, seeds=neural.DEFAULT_SEEDS)
        steps = [m.steps for m in ens.members]
        print(f"\n=== run {run}: steps {steps} ===", flush=True)
        pmf = ens.predict_pmf(va)
        for k, (family, horizon) in enumerate(neural.TASKS):
            fan = dist.quantiles_from_pmf(pmf[:, k, :], ens.grids[k], train.TAUS)
            for e in promotion.score_family(tr, va, family, horizon, fan):
                rows.append(
                    {
                        "run": run,
                        "steps": str(steps),
                        "family": family,
                        "head": e.head,
                        "tau": e.tau,
                        "improve_pct": e.improvement * 100,
                        "beats": e.improvement > 0,
                        "cov": e.coverage,
                        "cov_err": e.coverage_error,
                        "cov_ok": e.coverage_ok,
                    }
                )
        pd.DataFrame(rows).to_csv(OUT, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 72)
    for run, g in d.groupby("run"):
        fam = g.groupby("family").agg(cov_ok=("cov_ok", "sum"), n=("cov_ok", "size"))
        print(
            f"run {run}: coverage {int(g.cov_ok.sum())}/{len(g)}  beats constant "
            f"{int(g.beats.sum())}/{len(g)}  mean improvement {g.improve_pct.mean():+.3f}%  "
            f"steps {g.steps.iloc[0]}"
        )
        print("  by family: " + ", ".join(f"{f} {r.cov_ok}/{r.n}" for f, r in fam.iterrows()))
    same = (
        d[d.run == 1]
        .reset_index(drop=True)["cov"]
        .equals(d[d.run == 2].reset_index(drop=True)["cov"])
    )
    print(f"\nrun 1 and run 2 identical coverage: {same}")
    print(f"results: {OUT}\n=== DONE ===")


if __name__ == "__main__":
    sys.exit(main())
