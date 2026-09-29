"""Is the transition error learnable, or is it the market's next move? (BACKLOG 3c)

The model fails where the S&P is still above its 200-day while a decline
is underway (2022, above the line: mean |error| 0.0778 against 0.0236
below it). Every fix tried moved t-1 information into the model or its
calibration, and none worked. This asks the question underneath: is there
anything at t-1 to learn?

One fit on 2010-2021, production features (24, ADR 204), scored on

    W1  2022-01-01 .. 2023-12-31   the transition and the bear
    W2  2024-01-01 .. 2026-09-03   the calm that followed

Three logistic recalibration layers, each fitted on W2 and applied to W1,
so the transition cell is never in its own fit:

    BASE    logit(p)
    STATE   logit(p) + t-1 market state, raw and signed by side
    ORACLE  logit(p) + the S&P's move over the label's own horizon, signed
            by side. Not knowable at t-1: an upper bound, not a proposal

If ORACLE removes the transition bias and STATE does not, the error is the
market's forward move, and a per-ticker model built on t-1 data cannot
fix it. Part 2 checks the premise directly: out-of-sample R^2 of the
S&P's forward return on the same t-1 state, 2012-2026.

Read-only against `wivie`'s research database.
"""

from __future__ import annotations

import os
from datetime import date

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = os.environ.get(
    "TRANSITION_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sqlalchemy import text  # noqa: E402

from capitalscan.core import folds as cf  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural  # noqa: E402
from capitalscan.research.predict import TARGETS  # noqa: E402

CHASH = "f183b0f5209a4677"
TRAIN = (date(2010, 1, 1), date(2021, 12, 31))
W1 = (date(2022, 1, 1), date(2023, 12, 31))
W2 = (date(2024, 1, 1), date(2026, 9, 3))
STATE = ("above200", "dd252", "ret20", "vix", "breadth_ma_above", "breadth_chg_60d")
HERE = os.path.dirname(os.path.abspath(__file__))


def market(engine) -> pd.DataFrame:
    """Daily market state. Row t holds what was known at t's close."""
    with engine.connect() as conn:
        m = pd.read_sql(
            text(
                "SELECT ts::date AS d, spx_close, vix_close, breadth_ma_above, breadth_chg_60d "
                "FROM market_days WHERE spx_close IS NOT NULL ORDER BY ts"
            ),
            conn,
        )
    m["d"] = pd.to_datetime(m["d"])
    c = m["spx_close"].astype(float)
    m["above200"] = (c > c.rolling(200).mean()).astype(float)
    m["dd252"] = c / c.rolling(252).max() - 1
    m["ret20"] = c / c.shift(20) - 1
    m["vix"] = m["vix_close"].astype(float)
    return m.reset_index(drop=True)


def attach(frame: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """t-1 state and the forward S&P move for each horizon, per event."""
    d = frame.copy()
    sd = pd.to_datetime(d["signal_date"]).to_numpy()
    prev = m["d"].searchsorted(sd, side="left") - 1  # last session strictly before
    for col in STATE:
        d[col] = m[col].to_numpy(float)[prev]
    c = m["spx_close"].to_numpy(float)
    sign = np.where(d["side"] == "long", 1.0, -1.0)
    d["sign"] = sign
    for h in {t.horizon for t in TARGETS}:
        end = np.minimum(prev + h, len(c) - 1)  # close of the h-th session from t
        d[f"mkt_{h}"] = sign * (c[end] / c[prev] - 1)
        # The S&P's best and worst close inside the window, for the side.
        win = np.stack([c[np.minimum(prev + j, len(c) - 1)] for j in range(1, h + 1)])
        best = np.where(sign > 0, win.max(axis=0), win.min(axis=0))
        worst = np.where(sign > 0, win.min(axis=0), win.max(axis=0))
        d[f"mkt_best_{h}"] = sign * (best / c[prev] - 1)
        d[f"mkt_worst_{h}"] = sign * (worst / c[prev] - 1)
    d["transition"] = (pd.to_datetime(d["signal_date"]).dt.year == 2022) & (d["above200"] == 1)
    return d


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def design(d: pd.DataFrame, raw: np.ndarray, arm: str, h: int) -> np.ndarray:
    cols = [logit(raw)]
    if arm == "STATE":
        for s in STATE:
            v = d[s].to_numpy(float)
            cols += [v, v * d["sign"].to_numpy()]
    if arm in ("ORACLE", "ORACLE_W1"):
        cols.append(d[f"mkt_{h}"].to_numpy(float))
    if arm == "PATH":
        cols += [d[f"mkt_{c}_{h}"].to_numpy(float) for c in ("best", "worst")]
    return np.column_stack(cols)


def wmean(x: np.ndarray, w: np.ndarray) -> float:
    return float((x * w).sum() / w.sum())


def part1(engine, m: pd.DataFrame) -> pd.DataFrame:
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())
    tr, rep = feat.build_training_frame(engine, CHASH, window=TRAIN)
    f1, rep1 = feat.build_training_frame(engine, CHASH, window=W1)
    f2, rep2 = feat.build_training_frame(engine, CHASH, window=W2)
    print(f"train {rep}\nW1    {rep1}\nW2    {rep2}", flush=True)
    ens = neural.fit(tr, calendar, seeds=neural.DEFAULT_SEEDS)
    print(f"steps {[x.steps for x in ens.members]}", flush=True)

    sets = {}
    for name, f in (("W1", f1), ("W2", f2)):
        d = attach(f, m).reset_index(drop=True)
        ok = d[list(STATE)].notna().all(axis=1).to_numpy()
        sets[name] = (d[ok].reset_index(drop=True), ens.predict_pmf(f)[ok])
        print(f"{name}: {ok.sum():,} rows, transition {int(d['transition'][ok].sum()):,}")

    rows = []
    for t in TARGETS:
        k = neural.TASKS.index((t.family, t.horizon))
        prepared = {}
        for name, (d, pmf) in sets.items():
            lab = pd.to_numeric(d[t.label_column], errors="coerce").to_numpy(float)
            keep = ~np.isnan(lab)
            prepared[name] = (
                d[keep].reset_index(drop=True),
                t.probability(pmf[keep, k, :], ens.grids[k]),
                t.realised(lab[keep]),
                np.asarray(cf.cluster_weights(list(d.loc[keep, "cluster_id"]))),
            )
        d2, r2, y2, w2 = prepared["W2"]
        d1, r1, y1, w1 = prepared["W1"]
        tr_mask = d1["transition"].to_numpy(bool)
        long_mask = (d1["side"] == "long").to_numpy()
        cells = {
            "W1 transition": tr_mask,
            "W1 transition long": tr_mask & long_mask,
            "W1 transition short": tr_mask & ~long_mask,
            "W1 rest": ~tr_mask,
        }
        pd.DataFrame(
            {"event_id": d1["id"], "field": t.field, "raw": r1, "y": y1, "w": w1}
        ).to_parquet(os.path.join(HERE, f"scores_{t.field}.parquet"))
        for arm in ("RAW", "BASE", "STATE", "ORACLE", "PATH", "ORACLE_W1"):
            if arm == "RAW":
                p = r1
            elif arm == "ORACLE_W1":
                # Fitted on W1 outside the cell: a slope learned in a bear.
                lr = LogisticRegression(C=1e6, max_iter=2000)
                x1 = design(d1, r1, arm, t.horizon)
                lr.fit(x1[~tr_mask], y1[~tr_mask], sample_weight=w1[~tr_mask])
                p = lr.predict_proba(x1)[:, 1]
            else:
                lr = LogisticRegression(C=1e6, max_iter=2000)
                lr.fit(design(d2, r2, arm, t.horizon), y2, sample_weight=w2)
                p = lr.predict_proba(design(d1, r1, arm, t.horizon))[:, 1]
            for cell, mask in cells.items():
                pm, ym, wm = p[mask], y1[mask], w1[mask]
                rows.append(
                    {
                        "field": t.field,
                        "arm": arm,
                        "cell": cell,
                        "n": int(mask.sum()),
                        "base_rate": wmean(ym, wm),
                        "mean_p": wmean(pm, wm),
                        "bias": wmean(pm, wm) - wmean(ym, wm),
                        "mkt_fav": wmean(d1[f"mkt_{t.horizon}"].to_numpy(float)[mask], wm),
                        "brier": wmean((pm - ym) ** 2, wm),
                    }
                )
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(HERE, "part1.csv"), index=False)
    return out


def part2(m: pd.DataFrame) -> pd.DataFrame:
    """Out-of-sample R^2 of the S&P's forward h-day return on t-1 state."""
    c = m["spx_close"].to_numpy(float)
    rows = []
    for h in (3, 5, 10):
        fwd = np.full(len(c), np.nan)
        fwd[:-h] = c[h:] / c[:-h] - 1  # from close t to close t+h
        df = m[["d", *STATE]].assign(fwd=fwd).dropna().reset_index(drop=True)
        years = df["d"].dt.year
        pred, bench, real = [], [], []
        for yr in range(2012, 2027):
            # Train on rows whose forward window closed before the test year.
            train = df[(years < yr)].iloc[:-h]
            test = df[years == yr]
            if test.empty or len(train) < 500:
                continue
            x = np.column_stack([np.ones(len(train)), train[list(STATE)].to_numpy(float)])
            beta, *_ = np.linalg.lstsq(x, train["fwd"].to_numpy(), rcond=None)
            xt = np.column_stack([np.ones(len(test)), test[list(STATE)].to_numpy(float)])
            pred.append(xt @ beta)
            bench.append(np.full(len(test), train["fwd"].mean()))
            real.append(test["fwd"].to_numpy())
        p, b, r = map(np.concatenate, (pred, bench, real))
        r2 = 1 - ((r - p) ** 2).sum() / ((r - b) ** 2).sum()
        hit = float((np.sign(p - b) == np.sign(r - b)).mean())
        rows.append({"h": h, "n": len(r), "oos_r2": r2, "direction_hit": hit})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(HERE, "part2.csv"), index=False)
    return out


def main() -> None:
    engine = db_io.get_engine()
    m = market(engine)
    print("part 2: forward S&P on t-1 state, out of sample 2012-2026")
    print(part2(m).round(4).to_string(index=False), flush=True)
    out = part1(engine, m)
    piv = out.assign(abs_bias=out["bias"].abs()).pivot_table(
        index=["field", "cell"], columns="arm", values="bias"
    )
    print("\nbias (mean p - realised), fitted on W2, applied to W1")
    print(piv[["RAW", "BASE", "STATE", "ORACLE", "PATH", "ORACLE_W1"]].round(4).to_string())
    cols = ["n", "base_rate", "mean_p", "bias", "mkt_fav"]
    print("\nRAW arm by cell")
    print(out[out["arm"] == "RAW"].set_index(["field", "cell"])[cols].round(4).to_string())
    summary = (
        out.assign(abs_bias=out["bias"].abs())
        .groupby(["cell", "arm"])[["abs_bias", "brier"]]
        .mean()
        .unstack("arm")
    )
    print("\nmean over six fields")
    print(summary.round(4).to_string())
    print("\n=== TRANSITION DONE ===")


if __name__ == "__main__":
    main()
