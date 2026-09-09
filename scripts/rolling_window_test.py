"""Does a rolling training window close the coverage gate?

**ADR 179's cheapest test, and the cheapest test of the label-shift story.**

The gate fails on 2022: mean absolute coverage error 0.0778 where the index
is above its 200-day average, against 0.0236 below it. Five hypotheses were
tested and four refuted (market regime as a pooled contrast -- itself a
Simpson's paradox, since the 2x2 shows regime *does* separate -- CRPS grid
truncation, volatility scale, multi-task interference). What survived is
that train contains no sustained decline: its worst year is 2011 at 0.603
of sessions above the 200-day SMA, against 2022's 0.151.

A five-year window from 2026 pulls 2022 *into* training. If the diagnosis is
right, the arms below should show coverage improving as the window moves
forward -- with no architecture change at all.

**Three arms, same architecture, same seeds:**

    fixed       train 2010-2021, validate 2022-2023   (today's model)
    roll5       train 2021-2025, validate 2026        (five-year window)
    roll5_purge same, with the 10-day embargo made explicit

**Why the third arm exists.** A rolling boundary needs the same purge the
walk-forward ladder uses: a 10-day forward window means the last 10 days of
train overlap the first days of validate, and without the embargo the model
reads its own validation labels. `roll5` and `roll5_purge` differing is
itself the finding -- it would mean the boundary leaks.

**The comparison is not like-for-like and must not be read as one.** The
arms validate on different years, so a lower error could mean "2026 is an
easier year" rather than "the model improved". What the test can settle is
narrower and still worth knowing: whether a model trained through 2022
covers *its own* validation period better than one trained before it.

**No prediction recorded.** Four of five written earlier in this session
were wrong.

---

**Ran 2026-09-08 with five-year windows and the result was void.** A
five-year window builds **zero** inner walk-forward folds -- `walk_forward_folds`
needs `DEFAULT_MIN_TRAIN_YEARS` (5) of training before its first validation
year -- so both rolling arms fell through to `neural.DEFAULT_STEPS = 300`
while the fixed arm selected [776, 909, 591] from seven folds. The
comparison measured a 2.5x difference in training length, not the window.
`neural.fit` now raises on an empty ladder; the windows below are seven
years, which builds two folds. Two against seven is still thin, and any
write-up must say so. Full retraction in `RESULTS.md`.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
from sqlalchemy import text

from capitalscan.core import folds as core_folds
from capitalscan.jobs import db_io
from capitalscan.research import features as feat
from capitalscan.research import neural, train

#: Per-arm coverage tables land here so a question the printout did not
#: anticipate costs a file read rather than a 22-minute refit.
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "reports" / "rolling"

CHASH = "0523841076f47293"
TOL = 0.05

#: `(name, train_start, train_end, validate_end)`. Validate begins the day
#: after `train_end`; the purge, where applied, removes the overlap.
#: Seven years, not five. Five builds no ladder at all -- see the note at
#: the end of the module docstring.
ARMS = (
    ("fixed", "2010-03-31", "2021-12-31", "2023-12-29"),
    ("roll7", "2019-01-01", "2025-12-31", "2026-09-04"),
    ("roll7_purge", "2019-01-01", "2025-12-31", "2026-09-04"),
)
PURGE_DAYS = 10


def frames(engine, start: str, end: str, vend: str, purge: bool):
    """Train and validate frames cut at arbitrary dates.

    `build_training_frame` filters on `split_key`, which ADR 019 fixed at
    event creation and invariant 5 forbids recomputing. So this reads the
    whole labelled population once and slices by `signal_date` -- the
    rolling window is a training-time filter, never a rewrite of the column.
    """
    original = feat._SQL
    try:
        feat._SQL = original.replace("AND e.split_key = :split", "")
        cols = feat._select_columns()
        params = {"chash": CHASH, "entry_kind": feat.TRAINING_ENTRY_KIND}
        with engine.connect() as conn:
            raw = pd.read_sql(
                text(feat._SQL.format(cols=", ".join(cols), row_filter="")), conn, params=params
            )
    finally:
        feat._SQL = original

    trainable, _etf, _missing = feat.partition_for_training(
        list(zip(raw["ticker"], raw["sector"], strict=True))
    )
    kept = raw.iloc[trainable].reset_index(drop=True)
    kept = kept.dropna(subset=list(feat.LABEL_COLS)).reset_index(drop=True)
    kept = feat._coerce_boolean_features(kept)
    kept = feat._add_derived(kept)

    d = pd.to_datetime(kept["signal_date"])
    train_end = pd.Timestamp(end)
    # The embargo: drop the last PURGE_DAYS of train, whose 10-day forward
    # windows reach into validate.
    cut = train_end - pd.Timedelta(days=PURGE_DAYS) if purge else train_end
    tr = kept[(d >= pd.Timestamp(start)) & (d <= cut)].reset_index(drop=True)
    va = kept[(d > train_end) & (d <= pd.Timestamp(vend))].reset_index(drop=True)
    return tr, va


def coverage(ens, va, w) -> pd.DataFrame:
    rows = []
    for family, horizon in neural.TASKS:
        fan = ens.fan(va, family, horizon)
        label = pd.to_numeric(va[train.label_for(family, horizon)], errors="coerce").to_numpy(float)
        ok = ~np.isnan(label)
        for tau, pred in fan.items():
            below = (label <= pred).astype(float)
            cov = float((w[ok] * below[ok]).sum() / w[ok].sum())
            rows.append(
                {
                    "head": f"{family}_h{horizon}",
                    "tau": tau,
                    "err": cov - tau,
                    "ok": abs(cov - tau) <= TOL,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = db_io.get_engine()
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())

    results = {}
    for name, start, end, vend in ARMS:
        purge = name.endswith("purge")
        tr, va = frames(engine, start, end, vend, purge)
        print(
            f"\n=== {name}: train {len(tr):,} ({start}..{end}"
            f"{', purged ' + str(PURGE_DAYS) + 'd' if purge else ''})"
            f"  validate {len(va):,} (..{vend}) ===",
            flush=True,
        )
        if len(tr) < 5000 or len(va) < 2000:
            print("  too few rows, skipping")
            continue
        ens = neural.fit(tr, calendar, seeds=neural.DEFAULT_SEEDS)
        steps = [m.steps for m in ens.members]
        print(f"  steps {steps}", flush=True)
        # A selected count that happens to equal the fallback is possible
        # and a set of three identical ones is not. `fit` raises on an
        # empty ladder now, so this covers the remaining degenerate case.
        if len(set(steps)) == 1 and steps[0] == neural.DEFAULT_STEPS:
            print(
                "  !! every seed reports DEFAULT_STEPS -- selection did not "
                "happen, treat this arm as void",
                flush=True,
            )
        w = np.asarray(core_folds.cluster_weights(list(va["cluster_id"])))
        d = coverage(ens, va, w)
        results[name] = d
        out = OUT_DIR / f"coverage_{name}.csv"
        d.assign(arm=name, steps=str(steps), n_train=len(tr), n_validate=len(va)).to_csv(
            out, index=False
        )
        print(f"  wrote {out}", flush=True)
        print(
            f"  {d['ok'].sum()}/{len(d)} heads within {TOL:.0%}"
            f"   mean |err| {d['err'].abs().mean():.4f}",
            flush=True,
        )

    print("\n" + "=" * 78)
    print("COVERAGE BY ARM  (different validation years -- see the docstring)")
    print("=" * 78)
    print(f"{'arm':14} {'heads pass':>12} {'mean |err|':>12}")
    for name, d in results.items():
        print(
            f"{name:14} {str(d['ok'].sum()) + '/' + str(len(d)):>12} {d['err'].abs().mean():>12.4f}"
        )

    if "roll7" in results and "roll7_purge" in results:
        a, b = results["roll7"]["err"].abs().mean(), results["roll7_purge"]["err"].abs().mean()
        print(
            f"\npurge effect: {a:.4f} -> {b:.4f}  "
            f"({'boundary leaks' if b > a * 1.15 else 'no leak detected'})"
        )

    print("\nthe five heads the fixed arm fails:")
    if "fixed" in results:
        failing = results["fixed"][~results["fixed"]["ok"]][["head", "tau"]]
        for _, r in failing.iterrows():
            line = f"  {r['head']} q{r['tau']}: "
            for name, d in results.items():
                m = (d["head"] == r["head"]) & (d["tau"] == r["tau"])
                if m.any():
                    line += f"{name} {float(d.loc[m, 'err'].iloc[0]):+.4f}  "
            print(line)

    print("\n=== ROLLING DONE ===")


if __name__ == "__main__":
    main()
