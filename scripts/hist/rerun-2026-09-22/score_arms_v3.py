"""Does the 2000s add anything ON TOP OF recency?

The 2026-09-22 arms both stopped training at 2021, so they answered "depth
against depth" while production's weekly refit trains up to `today - 6mo`
(ADR 193). Recency is the larger measured effect: the same fit scored 25/30
on 2022-23 and 14/30 on 2026, recovering to 25/30 when the window was
extended. So this run gives BOTH arms production's window shape and varies
only the start year.

    Arm A   train 2010-01-01 .. 2026-03-13   (production's shape)
    Arm B   train 2002-01-01 .. 2026-03-13   (same, plus the 2000s)
    both    validate 2026-03-13 .. 2026-09-03, the most recent unseen data

Same store, same config, same six heads and seeds, same label sample. Still
`capitalscan_hist`, so `crit_mcap` is dropped and the added years are
survivorship-biased; A against B is the only comparison that means anything.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = "postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
os.environ["CAPSCAN_SPLITS"] = '{"ingest_start":"1999-01-01","event_start":"2002-01-01"}'
os.environ["CAPSCAN_UNIVERSE"] = (
    '{"required_criteria":["crit_above_sma200","crit_sma200_slope","crit_rel_return"]}'
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sqlalchemy import text  # noqa: E402

from capitalscan.core import distributions as dist  # noqa: E402
from capitalscan.core import folds as cf  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.config import config_hash, resolve_config  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural, promotion, train  # noqa: E402

CHASH = config_hash(resolve_config())
ARMS = {"A_train2010": "2010-01-01", "B_train2002": "2002-01-01"}
TRAIN_END = "2026-03-13"  # today - 6mo, the shape ADR 193 gave the weekly refit
VALIDATE = ("2026-03-13", "2026-09-03")  # unseen, and the regime the site serves
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arms_results_v3.csv")


def regime_above(engine, signal_dates: pd.Series) -> np.ndarray:
    """SPX above its 200-day SMA as of each signal date: the last session on
    or before it, never a later one."""
    with engine.connect() as conn:
        mkt = pd.read_sql(
            text("SELECT ts, spx_close FROM market_days WHERE spx_close IS NOT NULL ORDER BY ts"),
            conn,
        )
    mkt["ts"] = pd.to_datetime(mkt["ts"])
    close = mkt["spx_close"].astype(float)
    mkt["above"] = close > close.rolling(200).mean()
    sig = pd.DataFrame(
        {"ts": pd.to_datetime(signal_dates).values, "_i": np.arange(len(signal_dates))}
    )
    merged = pd.merge_asof(sig.sort_values("ts"), mkt[["ts", "above"]], on="ts").sort_values("_i")
    return merged["above"].fillna(False).to_numpy(bool)


def main() -> None:
    engine = db_io.get_engine()
    assert "capitalscan_hist" in str(engine.url), f"REFUSING: not the isolated store ({engine.url})"
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())
    print(f"store       : capitalscan_hist\nconfig_hash : {CHASH}", flush=True)

    from datetime import date as _date

    tr_all, rep_tr = feat.build_training_frame(
        engine, CHASH, window=(_date(2002, 1, 1), _date.fromisoformat(TRAIN_END))
    )
    va, rep_va = feat.build_training_frame(
        engine, CHASH, window=(_date.fromisoformat(VALIDATE[0]), _date.fromisoformat(VALIDATE[1]))
    )
    print(f"train (all) : {rep_tr}\nvalidate    : {rep_va}", flush=True)
    print("\ntrain events per year:")
    print(pd.to_datetime(tr_all["signal_date"]).dt.year.value_counts().sort_index().to_string())

    va_year = pd.to_datetime(va["signal_date"]).dt.year.to_numpy()
    va_above = regime_above(engine, va["signal_date"])
    w_va = np.asarray(cf.cluster_weights(list(va["cluster_id"])))
    cells = {"all": np.ones(len(va), bool), "above": va_above, "below": ~va_above}
    rows: list[dict] = []

    for arm, start in ARMS.items():
        tr = tr_all[pd.to_datetime(tr_all["signal_date"]) >= pd.Timestamp(start)].reset_index(
            drop=True
        )
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
                        "arm": arm,
                        "family": family,
                        "head": e.head,
                        "tau": e.tau,
                        "improve": e.improvement * 100,
                        "cov": e.coverage,
                        "cov_err": e.coverage_error,
                        "cov_ok": e.coverage_ok,
                        "n_train": len(tr),
                    }
                )
            y = pd.to_numeric(va[train.label_for(family, horizon)], errors="coerce").to_numpy(float)
            for tau in train.TAUS:
                q = fan[tau]
                for yy in (2026,):
                    for regime, rmask in cells.items():
                        m = (va_year == yy) & ~np.isnan(y) & rmask
                        if m.sum() == 0:
                            continue
                        c = ((y[m] <= q[m]).astype(float) * w_va[m]).sum() / w_va[m].sum()
                        rows.append(
                            {
                                "arm": arm,
                                "family": family,
                                "head": train.head_name(family, horizon, tau),
                                "tau": tau,
                                "year": yy,
                                "regime": regime,
                                "n": int(m.sum()),
                                "cov": c,
                                "cov_err": c - tau,
                            }
                        )
        pd.DataFrame(rows).to_csv(OUT, index=False)

    d = pd.DataFrame(rows)
    pooled = d[d["year"].isna()]
    print("\n" + "=" * 72)
    print("POOLED on validate 2026-03..2026-09, by family (tolerance +/-0.05)")
    print("=" * 72)
    for arm in ARMS:
        for fam, g in pooled[pooled["arm"] == arm].groupby("family"):
            print(
                f"  {arm:12} {fam:9} n_train={int(g['n_train'].iloc[0]):>9,}  "
                f"cov_ok {int(g['cov_ok'].sum())}/{len(g)}  "
                f"mean|cov_err| {g['cov_err'].abs().mean():.4f}  "
                f"mean_improve {g['improve'].mean():+.2f}"
            )

    yd = d[d["year"].notna()].assign(ae=lambda x: x["cov_err"].abs())
    print("\nmean |coverage error| by family, year and regime  (THE question: 2022 / above)")
    print(
        yd.pivot_table(index=["family", "arm"], columns=["year", "regime"], values="ae")
        .round(4)
        .to_string()
    )
    print("\nvalidate events per cell:")
    print(yd[yd["arm"] == list(ARMS)[0]].groupby(["year", "regime"])["n"].max().to_string())
    print(f"\nresults: {OUT}")
    print("\n=== ARMS DONE ===")


if __name__ == "__main__":
    main()
