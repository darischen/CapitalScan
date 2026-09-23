"""Does splitting the reliability table by market regime improve the numbers a reader sees?

BACKLOG item 3c. The model's coverage error separates by a factor of three
within 2022 once the year is held fixed: mean |error| 0.0778 with SPX above
its 200-day SMA against 0.0236 below it. The failure is the *transition*,
not the bear market. Item 3c proposes sidestepping the model entirely:

    fit ADR 174's reliability table separately above and below the line.

This measures that, and nothing else. **One model fit, two calibration
schemes.** Everything downstream of the raw probabilities is the variable;
the raw probabilities are held fixed, so a difference cannot be a different
model.

    fit          train 2010-01-01 .. 2021-12-31   (production's start, ADR 193)
    window W1    2022-01-01 .. 2023-12-31         the transition and the bear
    window W2    2024-01-01 .. 2026-09-03         the calm that followed

Both windows are unseen by the fit, so **both directions are legitimate**
and both are run: calibrate on W1 and evaluate on W2, then the reverse.
One direction alone would confound the split with which period happened to
supply the calibration sample.

    arm POOLED   one table per field, all calibration rows
    arm SPLIT    two tables per field, applied by the event's own regime

Regime is SPX against its own 200-day SMA at the signal date, resolved
as-of: the last session on or before it, never a later one.

**Metrics, all Kish-weighted** (`core.folds.cluster_weights`), because
events cluster and an unweighted calibration error is too small by exactly
that factor:

    bias   weighted mean published p_hat - weighted mean realised.
           The "~5 points low" quantity CLAUDE.md names. Signed.
    ece    ten equal-mass bins on the published p_hat, weighted mean
           |bin mean p_hat - bin mean outcome|.
    brier  weighted mean (p_hat - y)^2. Guards against a split that
           improves calibration by destroying resolution.

Run against `capitalscan_hist` only, which is survivorship-biased and drops
`crit_mcap`. POOLED against SPLIT on the same rows is the only comparison
here that means anything; neither arm's absolute level transfers to
production.
"""

from __future__ import annotations

import os
from datetime import date

import numpy as np
import pandas as pd

os.environ["DATABASE_URL_RESEARCH"] = "postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
os.environ["CAPSCAN_SPLITS"] = '{"ingest_start":"1999-01-01","event_start":"2002-01-01"}'
os.environ["CAPSCAN_UNIVERSE"] = (
    '{"required_criteria":["crit_above_sma200","crit_sma200_slope","crit_rel_return"]}'
)
os.environ.pop("DATABASE_URL_SERVING", None)

from sqlalchemy import text  # noqa: E402

from capitalscan.core import calibration as calib  # noqa: E402
from capitalscan.core import folds as cf  # noqa: E402
from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.config import config_hash, resolve_config  # noqa: E402
from capitalscan.research import features as feat  # noqa: E402
from capitalscan.research import neural  # noqa: E402
from capitalscan.research.predict import TARGETS  # noqa: E402

CHASH = config_hash(resolve_config())
TRAIN = (date(2010, 1, 1), date(2021, 12, 31))
W1 = (date(2022, 1, 1), date(2023, 12, 31))
W2 = (date(2024, 1, 1), date(2026, 9, 3))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "regime_calibration.csv")


def regime_above(engine, signal_dates: pd.Series) -> np.ndarray:
    """SPX above its 200-day SMA as of each signal date, resolved as-of."""
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


def wmean(x: np.ndarray, w: np.ndarray) -> float:
    return float((x * w).sum() / w.sum()) if w.sum() > 0 else float("nan")


def ece(p: np.ndarray, y: np.ndarray, w: np.ndarray, bins: int = 10) -> float:
    """Weighted expected calibration error over equal-mass bins of `p`."""
    if len(p) < bins * 5:
        return float("nan")
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    total = 0.0
    for k in range(len(edges) - 1):
        m = idx == k
        if not m.any() or w[m].sum() == 0:
            continue
        total += (w[m].sum() / w.sum()) * abs(wmean(p[m], w[m]) - wmean(y[m], w[m]))
    return total


def published(table: calib.ReliabilityTable, raw: np.ndarray) -> np.ndarray:
    return np.array([table.band(float(v))[0] for v in raw])


def main() -> None:
    engine = db_io.get_engine()
    assert "capitalscan_hist" in str(engine.url), f"REFUSING: not the isolated store ({engine.url})"
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())
    print(f"store       : capitalscan_hist\nconfig_hash : {CHASH}", flush=True)

    tr, rep_tr = feat.build_training_frame(engine, CHASH, window=TRAIN)
    f1, rep1 = feat.build_training_frame(engine, CHASH, window=W1)
    f2, rep2 = feat.build_training_frame(engine, CHASH, window=W2)
    print(f"train : {rep_tr}\nW1    : {rep1}\nW2    : {rep2}", flush=True)

    print("\nfitting once, six heads...", flush=True)
    ens = neural.fit(tr, calendar, seeds=neural.DEFAULT_SEEDS)
    print(f"  steps: {[m.steps for m in ens.members]}", flush=True)

    frames = {}
    for name, frame in (("W1", f1), ("W2", f2)):
        pmf = ens.predict_pmf(frame)
        frames[name] = {
            "frame": frame,
            "pmf": pmf,
            "above": regime_above(engine, frame["signal_date"]),
            "w": np.asarray(cf.cluster_weights(list(frame["cluster_id"]))),
        }
        n_above = int(frames[name]["above"].sum())
        print(f"{name}: {len(frame):,} events, {n_above:,} above / {len(frame) - n_above:,} below")

    rows: list[dict] = []
    for fit_on, eval_on in (("W1", "W2"), ("W2", "W1")):
        cal, ev = frames[fit_on], frames[eval_on]
        for target in TARGETS:
            k = neural.TASKS.index((target.family, target.horizon))
            col = target.label_column

            def prep(d: dict, target=target, k=k, col=col):
                lab = pd.to_numeric(d["frame"][col], errors="coerce").to_numpy(float)
                raw = target.probability(d["pmf"][:, k, :], ens.grids[k])
                keep = ~np.isnan(lab) & ~np.isnan(raw)
                return raw[keep], target.realised(lab[keep]), d["w"][keep], d["above"][keep]

            a_raw, a_y, a_w, a_above = prep(cal)
            b_raw, b_y, b_w, b_above = prep(ev)

            tables: dict[str, calib.ReliabilityTable | None] = {}
            try:
                tables["POOLED"] = calib.build_reliability(target.field, a_raw, a_y, weights=a_w)
            except ValueError as exc:
                print(f"  {target.field} POOLED fit on {fit_on} refused: {exc}")
                tables["POOLED"] = None
            for label, mask in (("above", a_above), ("below", ~a_above)):
                try:
                    tables[label] = calib.build_reliability(
                        target.field, a_raw[mask], a_y[mask], weights=a_w[mask]
                    )
                except ValueError as exc:
                    print(f"  {target.field} {label} fit on {fit_on} refused: {exc}")
                    tables[label] = None

            pooled_table = tables["POOLED"]
            if pooled_table is None:
                continue
            pooled_p = published(pooled_table, b_raw)
            split_p = pooled_p.copy()
            for label, mask in (("above", b_above), ("below", ~b_above)):
                t = tables[label]
                if t is not None and mask.any():
                    split_p[mask] = published(t, b_raw[mask])

            cells = {"all": np.ones(len(b_y), bool), "above": b_above, "below": ~b_above}
            for arm, p_hat in (("POOLED", pooled_p), ("SPLIT", split_p)):
                for cell, m in cells.items():
                    if m.sum() < 50:
                        continue
                    rows.append(
                        {
                            "fit_on": fit_on,
                            "eval_on": eval_on,
                            "field": target.field,
                            "arm": arm,
                            "cell": cell,
                            "n": int(m.sum()),
                            "n_eff": calib.kish_n_eff(b_w[m]),
                            "base_rate": wmean(b_y[m], b_w[m]),
                            "mean_p": wmean(p_hat[m], b_w[m]),
                            "bias": wmean(p_hat[m], b_w[m]) - wmean(b_y[m], b_w[m]),
                            "ece": ece(p_hat[m], b_y[m], b_w[m]),
                            "brier": wmean((p_hat[m] - b_y[m]) ** 2, b_w[m]),
                        }
                    )
            pd.DataFrame(rows).to_csv(OUT, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 78)
    print("|bias| and ECE by arm and cell, lower is better")
    print("=" * 78)
    for direction, g in d.groupby(["fit_on", "eval_on"]):
        print(f"\n--- calibrate on {direction[0]}, evaluate on {direction[1]} ---")
        piv = g.assign(abs_bias=g["bias"].abs()).pivot_table(
            index=["field", "cell"], columns="arm", values=["abs_bias", "ece", "brier"]
        )
        print(piv.round(4).to_string())
        for cell in ("all", "above", "below"):
            c = g[g["cell"] == cell]
            if c.empty:
                continue
            po, sp = c[c["arm"] == "POOLED"], c[c["arm"] == "SPLIT"]
            print(
                f"  {cell:5} mean |bias| POOLED {po['bias'].abs().mean():.4f} -> "
                f"SPLIT {sp['bias'].abs().mean():.4f} | "
                f"ECE {po['ece'].mean():.4f} -> {sp['ece'].mean():.4f} | "
                f"Brier {po['brier'].mean():.4f} -> {sp['brier'].mean():.4f}"
            )
    print(f"\nresults: {OUT}")
    print("\n=== REGIME CALIBRATION DONE ===")


if __name__ == "__main__":
    main()
