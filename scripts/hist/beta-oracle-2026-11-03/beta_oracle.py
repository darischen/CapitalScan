"""Does each stock's beta explain the rest of the transition bias? (BACKLOG 3c)

RESULTS 2026-09-29: a recalibration layer handed the S&P's actual forward
move removes about half of the long-side bias in the transition cell (2022,
S&P above its 200-day). This asks whether the other half is the same move
scaled by each stock's beta: a high-beta long falls further than the index
when the index falls.

Reads the W1 scores the 2026-09-29 run saved
(`../transition-2026-09-29/scores_*.parquet`: event id, the model's raw
probability, the realised label, the cluster weight), so no model is
refitted. Every arm is fitted on W1 outside the transition cell and applied
to the cell, the protocol of that run's ORACLE_W1 arm. The script first
reproduces ORACLE_W1 on all rows as a check against `part1.csv`.

    ORACLE_W1  logit(p) + m           m = the S&P's move over the label's
                                        horizon, signed by side
    BETA_W1    logit(p) + beta * m    beta = the stock's 252-session beta to
                                        the S&P, from returns through t-1
    BOTH_W1    logit(p) + m + beta * m
    IXIC_W1    logit(p) + n           n = the Nasdaq Composite's move over
                                        the same window, signed by side
    SPX_IXIC   logit(p) + m + n

Every arm but RAW uses the market's future move: upper bounds, not
proposals. IXIC asks whether the residual is a growth sell-off (January
2022) rather than the broad market.
Read-only against `wivie`'s research database.

    uv run python scripts/hist/beta-oracle-2026-11-03/beta_oracle.py
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sqlalchemy import create_engine, text

DB = os.environ.get(
    "BETA_ORACLE_DB", "postgresql+psycopg://capscan:capscan@192.168.1.12:5432/capitalscan"
)
HERE = os.path.dirname(os.path.abspath(__file__))
SCORES = os.path.join(HERE, "..", "transition-2026-09-29")
BETA_WINDOW, BETA_MIN = 252, 126
ARMS = ("RAW", "ORACLE_W1", "BETA_W1", "BOTH_W1", "IXIC_W1", "SPX_IXIC")
# Sessions each label looks forward, from `research.predict.TARGETS`. The
# number in a field's name is its threshold, not its horizon: `p_touch_3` is
# "+3% within 5 sessions". Copied rather than imported so this script does
# not load the model code.
HORIZON = {
    "p_touch_2": 5,
    "p_touch_3": 5,
    "p_touch_5": 5,
    "p_touch_10": 10,
    "p_adverse_3": 5,
    "p_adverse_5": 5,
}


def load_scores() -> pd.DataFrame:
    parts = [
        pd.read_parquet(p) for p in sorted(glob.glob(os.path.join(SCORES, "scores_*.parquet")))
    ]
    s = pd.concat(parts, ignore_index=True)
    s["horizon"] = s["field"].map(HORIZON).astype(int)
    return s


def load_market(engine) -> pd.DataFrame:
    with engine.connect() as conn:
        m = pd.read_sql(
            text(
                "SELECT ts::date AS d, spx_close FROM market_days "
                "WHERE spx_close IS NOT NULL ORDER BY ts"
            ),
            conn,
        )
    m["d"] = pd.to_datetime(m["d"])
    c = m["spx_close"].astype(float)
    m["spx_close"] = c
    m["above200"] = (c > c.rolling(200).mean()).astype(float)
    with engine.connect() as conn:
        ix = pd.read_sql(
            text(
                "SELECT (ts AT TIME ZONE 'UTC')::date AS d, close FROM bars "
                "WHERE ticker = '^IXIC' AND interval = '1d'"
            ),
            conn,
        )
    ix["d"] = pd.to_datetime(ix["d"])
    m["ixic_close"] = m["d"].map(ix.set_index("d")["close"].astype(float))
    return m.reset_index(drop=True)


def load_events(engine, ids: list[int]) -> pd.DataFrame:
    with engine.connect() as conn:
        e = pd.read_sql(
            text(
                "SELECT id AS event_id, ticker, signal_date, side FROM events WHERE id = ANY(:ids)"
            ),
            conn,
            params={"ids": ids},
        )
    e["signal_date"] = pd.to_datetime(e["signal_date"])
    return e


def load_betas(engine, tickers: list[str], m: pd.DataFrame) -> pd.DataFrame:
    """Rolling beta per ticker and date, from daily total-return closes.

    Both return series are taken on the ticker's own dates, so a missing
    bar spans the same days on both sides.
    """
    with engine.connect() as conn:
        b = pd.read_sql(
            text(
                "SELECT ticker, (ts AT TIME ZONE 'UTC')::date AS d, adj_close FROM bars "
                "WHERE interval = '1d' AND ticker = ANY(:t) "
                "AND ts >= '2020-06-01' AND ts < '2024-02-01'"
            ),
            conn,
            params={"t": tickers},
        )
    b["d"] = pd.to_datetime(b["d"])
    b = b.merge(m[["d", "spx_close"]], on="d", how="inner").sort_values(["ticker", "d"])
    out = []
    for t, g in b.groupby("ticker", sort=False):
        ri = g["adj_close"].astype(float).pct_change()
        rm = g["spx_close"].pct_change()
        cov = ri.rolling(BETA_WINDOW, min_periods=BETA_MIN).cov(rm)
        var = rm.rolling(BETA_WINDOW, min_periods=BETA_MIN).var()
        out.append(
            pd.DataFrame({"ticker": t, "d": g["d"].to_numpy(), "beta": (cov / var).to_numpy()})
        )
    return pd.concat(out, ignore_index=True).dropna()


def attach(s: pd.DataFrame, e: pd.DataFrame, m: pd.DataFrame, betas: pd.DataFrame) -> pd.DataFrame:
    d = s.merge(e, on="event_id", how="inner")
    sd = d["signal_date"].to_numpy()
    prev = m["d"].searchsorted(sd, side="left") - 1  # last session strictly before
    c = m["spx_close"].to_numpy(float)
    sign = np.where(d["side"] == "long", 1.0, -1.0)
    end = np.minimum(prev + d["horizon"].to_numpy(), len(c) - 1)
    d["mkt"] = sign * (c[end] / c[prev] - 1)
    n = m["ixic_close"].to_numpy(float)
    d["ixic"] = sign * (n[end] / n[prev] - 1)
    d["transition"] = (d["signal_date"].dt.year == 2022) & (m["above200"].to_numpy()[prev] == 1)
    # Beta as of the last session strictly before the signal: returns through t-1.
    d = d.sort_values("signal_date")
    d = pd.merge_asof(
        d,
        betas.sort_values("d"),
        left_on="signal_date",
        right_on="d",
        by="ticker",
        allow_exact_matches=False,
        direction="backward",
    )
    return d.drop(columns=["d"]).reset_index(drop=True)


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def design(d: pd.DataFrame, arm: str) -> np.ndarray:
    cols = [logit(d["raw"].to_numpy(float))]
    mkt = d["mkt"].to_numpy(float)
    if arm in ("ORACLE_W1", "BOTH_W1", "SPX_IXIC"):
        cols.append(mkt)
    if arm in ("IXIC_W1", "SPX_IXIC"):
        cols.append(d["ixic"].to_numpy(float))
    if arm in ("BETA_W1", "BOTH_W1"):
        cols.append(d["beta"].to_numpy(float) * mkt)
    return np.column_stack(cols)


def wmean(x: np.ndarray, w: np.ndarray) -> float:
    return float((x * w).sum() / w.sum())


def score(d: pd.DataFrame, arms: tuple[str, ...], label: str) -> pd.DataFrame:
    rows = []
    for field, g in d.groupby("field", sort=True):
        g = g.reset_index(drop=True)
        y, w = g["y"].to_numpy(float), g["w"].to_numpy(float)
        tr = g["transition"].to_numpy(bool)
        long_ = (g["side"] == "long").to_numpy()
        cells = {
            "transition long": tr & long_,
            "transition short": tr & ~long_,
            "rest": ~tr,
        }
        for arm in arms:
            if arm == "RAW":
                p = g["raw"].to_numpy(float)
            else:
                lr = LogisticRegression(C=1e6, max_iter=2000)
                x = design(g, arm)
                lr.fit(x[~tr], y[~tr], sample_weight=w[~tr])
                p = lr.predict_proba(x)[:, 1]
            for cell, mask in cells.items():
                pm, ym, wm = p[mask], y[mask], w[mask]
                rows.append(
                    {
                        "set": label,
                        "field": field,
                        "arm": arm,
                        "cell": cell,
                        "n": int(mask.sum()),
                        "realised": wmean(ym, wm),
                        "mean_p": wmean(pm, wm),
                        "bias": wmean(pm, wm) - wmean(ym, wm),
                        "brier": wmean((pm - ym) ** 2, wm),
                    }
                )
    return pd.DataFrame(rows)


def main() -> None:
    engine = create_engine(DB)
    s = load_scores()
    m = load_market(engine)
    e = load_events(engine, sorted(int(i) for i in s["event_id"].unique()))
    print(
        f"scores {len(s):,} rows, {s['event_id'].nunique():,} events; matched {len(e):,} on wivie"
    )
    betas = load_betas(engine, sorted(e["ticker"].unique()), m)
    d = attach(s, e, m, betas)
    print(f"beta missing on {d['beta'].isna().sum():,} of {len(d):,} rows (short history)")

    # Check: ORACLE_W1 on every row must reproduce the 2026-09-29 run.
    check = score(d, ("ORACLE_W1",), "all rows")
    ref = pd.read_csv(os.path.join(SCORES, "part1.csv"))
    ref = ref[
        (ref["arm"] == "ORACLE_W1")
        & ref["cell"].isin(["W1 transition long", "W1 transition short"])
    ]
    ref = ref.assign(cell=ref["cell"].str.replace("W1 ", "", regex=False))
    cmp = check.merge(ref[["field", "cell", "bias"]], on=["field", "cell"], suffixes=("", "_0929"))
    print("\nreproduction of ORACLE_W1 (bias now vs 2026-09-29)")
    print(cmp[["field", "cell", "n", "bias", "bias_0929"]].round(4).to_string(index=False))

    print(f"Nasdaq move missing on {d['ixic'].isna().sum():,} rows")
    with_beta = d[d["beta"].notna() & d["ixic"].notna()].reset_index(drop=True)
    out = score(with_beta, ARMS, "rows with a beta")
    out.to_csv(os.path.join(HERE, "results.csv"), index=False)

    print("\nbias (mean p - realised), fitted on W1 outside the cell")
    piv = out.pivot_table(index=["field", "cell"], columns="arm", values="bias")
    print(piv[list(ARMS)].round(4).to_string())
    summary = (
        out.assign(abs_bias=out["bias"].abs())
        .groupby(["cell", "arm"])[["abs_bias", "brier"]]
        .mean()
        .unstack("arm")
    )
    print("\nmean over six fields")
    print(summary.round(4).to_string())

    desc = with_beta.drop_duplicates("event_id")
    desc = desc.assign(cell=np.where(desc["transition"], "transition", "rest"))
    print("\nbeta by cell and side (one row per event)")
    print(
        desc.groupby(["cell", "side"])["beta"]
        .describe()[["count", "mean", "50%"]]
        .round(3)
        .to_string()
    )
    print("\n=== BETA ORACLE DONE ===")


if __name__ == "__main__":
    main()
