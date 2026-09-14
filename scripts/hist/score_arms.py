"""Does more bear-market history fix the 2022 coverage failure?

**One build, two slices, one variable.** Both arms read the same isolated
store, the same config, the same criteria, and the SAME validate split.
They differ only in where train begins.

    Arm A   train 2010-01-01..2021-12-31   (matches production's window)
    Arm B   train 2002-01-01..2021-12-31   (adds the dot-com tail and 2007-09)
    both    validate 2022-01-03..2023-12-29

**These numbers are NOT comparable to production's 17/20.** `crit_mcap` is
dropped in this store because `shares_outstanding` starts 2008-12-31 (the
SEC XBRL floor), so the population is broader than the product's. The
comparison that means something is A against B, not either against
production.

**The known bias, stated before the result.** The added bear years are
survivorship-biased: Lehman, Bear Stearns, Merrill and Wachovia are absent
from `tickers`, so 2008 here looks milder than 2008 was. That bias points
*against* the hypothesis -- it makes the extra history less useful than
real history would be. So a gain is understated and a null is not
conclusive.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = (
    "postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
)
os.environ["CAPSCAN_SPLITS"] = '{"ingest_start":"1999-01-01","event_start":"2002-01-01"}'
os.environ["CAPSCAN_UNIVERSE"] = (
    '{"required_criteria":["crit_above_sma200","crit_sma200_slope","crit_rel_return"]}'
)

from capitalscan.core import distributions as dist  # noqa: E402
from capitalscan.core import folds as cf  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.config import config_hash, resolve_config  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural, promotion, train  # noqa: E402
from sqlalchemy import text  # noqa: E402

CHASH = config_hash(resolve_config())
ARMS = {"A_train2010": "2010-01-01", "B_train2002": "2002-01-01"}


def main() -> None:
    engine = db_io.get_engine()
    with engine.connect() as conn:
        url = str(engine.url)
        assert "capitalscan_hist" in url, f"REFUSING: not the isolated store ({url})"
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())
    print(f"store       : capitalscan_hist\nconfig_hash : {CHASH}")

    tr_all, rep_tr = feat.build_training_frame(engine, CHASH, split="train")
    va, rep_va = feat.build_training_frame(engine, CHASH, split="validate")
    print(f"train (all) : {rep_tr}\nvalidate    : {rep_va}")

    tr_year = pd.to_datetime(tr_all["signal_date"]).dt.year
    print("\ntrain events per year:")
    print(tr_year.value_counts().sort_index().to_string())

    va_year = pd.to_datetime(va["signal_date"]).dt.year.to_numpy()
    w_va = np.asarray(cf.cluster_weights(list(va["cluster_id"])))
    rows: list[dict] = []

    for arm, start in ARMS.items():
        mask = pd.to_datetime(tr_all["signal_date"]) >= pd.Timestamp(start)
        tr = tr_all[mask].reset_index(drop=True)
        print(f"\n=== {arm}: train from {start}, {len(tr):,} events ===", flush=True)
        if len(tr) < 5000:
            print("  too few train rows, skipping")
            continue

        ens = neural.fit(tr, calendar, seeds=neural.DEFAULT_SEEDS)
        pmf = ens.predict_pmf(va)
        print(f"  steps: {[m.steps for m in ens.members]}", flush=True)

        for k, (family, horizon) in enumerate(neural.TASKS):
            fan = dist.quantiles_from_pmf(pmf[:, k, :], ens.grids[k], train.TAUS)
            for e in promotion.score_family(tr, va, family, horizon, fan):
                rows.append(
                    {
                        "arm": arm, "head": e.head, "tau": e.tau,
                        "improve": e.improvement * 100, "cov": e.coverage,
                        "cov_err": e.coverage_error, "cov_ok": e.coverage_ok,
                        "n_train": len(tr),
                    }
                )
            # Per-year coverage: the pooled number hid that every failure was 2022.
            y = pd.to_numeric(va[train.label_for(family, horizon)], errors="coerce").to_numpy(float)
            for tau in train.TAUS:
                q = fan[tau]
                for yy in (2022, 2023):
                    m = (va_year == yy) & ~np.isnan(y)
                    c = ((y[m] <= q[m]).astype(float) * w_va[m]).sum() / w_va[m].sum()
                    rows.append(
                        {
                            "arm": arm, "head": train.head_name(family, horizon, tau),
                            "tau": tau, "year": yy, "cov": c, "cov_err": c - tau,
                        }
                    )
        pd.DataFrame(rows).to_csv("arms_results.csv", index=False)

    d = pd.DataFrame(rows)
    pooled = d[d["year"].isna()] if "year" in d else d
    print("\n" + "=" * 70)
    print("POOLED on validate 2022-2023 (tolerance +/-0.05)")
    print("=" * 70)
    for arm in ARMS:
        s = pooled[pooled["arm"] == arm]
        if s.empty:
            continue
        print(
            f"  {arm:12} n_train={int(s['n_train'].iloc[0]):>7,}  "
            f"beats_const {int((s['improve'] > 0).sum())}/20  "
            f"cov_ok {int(s['cov_ok'].sum())}/20  "
            f"mean|cov_err| {s['cov_err'].abs().mean():.4f}  "
            f"mean_improve {s['improve'].mean():+.2f}"
        )

    if "year" in d:
        yd = d[d["year"].notna()]
        print("\nmean |coverage error| by arm and year -- the question this asks:")
        print(yd.assign(ae=yd["cov_err"].abs())
                .pivot_table(index="arm", columns="year", values="ae").round(4).to_string())
        print("\nthe three heads that failed in production, by year:")
        fails = ["terminal_h5_q25", "terminal_h10_q25", "terminal_h10_q50"]
        print(yd[yd["head"].isin(fails)]
              .pivot_table(index="head", columns=["arm", "year"], values="cov_err").round(3).to_string())
    print("\n=== ARMS DONE ===")


if __name__ == "__main__":
    main()
