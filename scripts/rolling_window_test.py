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

---

# 2026-09-10: the expanding window, and the control the first test lacked

**`roll7` changed two things at once and only one was named.**

    fixed   train 2010-2021 -> 12 years, validate 2022-2023
    roll7   train 2019-2025 ->  7 years, validate 2026

It moved the window forward **and cut it from twelve years to seven**, then
validated on a different period. The degradation on `peak` and `trough` was
read as recency hurting. A shorter fit is the other available explanation,
and it is the one that already produced a false result here once -- the
five-year run fell through to `DEFAULT_STEPS` and looked like a clean win.

**The expanding window separates them.** Keep the 2010 start and move only
the end:

    expand  train 2010-2025 -> 16 years, validate 2026

More recent data *and* more of it, where `roll7` traded one for the other.
If recency is what hurt `peak`, `expand` degrades too. If the seven-year
span was the cause, `expand` should hold or improve.

**`fixed_v26` is the control, and its absence is why the first test could
not settle this.** Every arm before it validated on a different period from
`fixed`, so "2026 is an easier year" and "the model improved" were
indistinguishable. `fixed_v26` is today's exact training window scored on
2026, so:

    expand  vs  fixed_v26   -> the training window, same validation data
    fixed   vs  fixed_v26   -> the validation year, same training data

Those two contrasts are each like-for-like. Neither was available before.

**What this does NOT settle: the ~5pp `p_touch_3` bias.** Coverage here is
the quantile fan -- `terminal`, `peak`, `trough` heads against their taus.
The shipped bias is a different quantity: the isotonic table is anchored to
its validation period's base rate, and the trailing twelve months run
~6pp above the validate split's 43.2%. This test moves that anchor as a
side effect, so `base_rate` is reported per arm below. That number is
evidence about the bias; the coverage columns are not.

**No prediction recorded.** Four of five written in the first session of
this test were wrong.

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
#: `(name, train_start, train_end, validate_start, validate_end)`.
#:
#: **`validate_start` is explicit, and the control is worthless without
#: it.** The original signature derived validate as "everything after
#: `train_end`", which for `fixed_v26` meant 2022-2026 -- 64,651 rows
#: against `expand`'s 11,690 for 2026 alone. Caught by a smoke test on
#: 2026-09-10 before the arms ran. Two arms that validate on different
#: populations cannot isolate anything, which is the same defect that made
#: the first `roll7` result unreadable.
ARMS = (
    ("fixed", "2010-03-31", "2021-12-31", "2022-01-01", "2023-12-29"),
    ("fixed_v26", "2010-03-31", "2021-12-31", "2026-01-01", "2026-09-04"),
    ("expand", "2010-03-31", "2025-12-31", "2026-01-01", "2026-09-04"),
    ("expand_purge", "2010-03-31", "2025-12-31", "2026-01-01", "2026-09-04"),
)
PURGE_DAYS = 10


def frames(engine, start: str, end: str, vstart: str, vend: str, purge: bool):
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
                text(
                    feat._SQL.format(
                        cols=", ".join(cols),
                        # **`TRADE_ONLY`, matching what the shipped model is
                        # fitted on.** ADR 183 widened *serving* to include
                        # `in_watch`; training never moved, and an arm that
                        # trained on a wider population than the live model
                        # would not be comparable to it. This placeholder did
                        # not exist when the harness was written -- the
                        # `KeyError` on 2026-09-10 is what surfaced that.
                        universe_filter=feat.TRADE_ONLY,
                        row_filter="",
                    )
                ),
                conn,
                params=params,
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
    # `vstart`, not `train_end + 1`: two arms with different training ends
    # must still score the same rows or the contrast measures the period
    # rather than the window.
    v0 = pd.Timestamp(vstart)
    if v0 <= train_end:
        raise ValueError(f"validate_start {vstart} is inside train (ends {end}) -- that leaks")
    va = kept[(d >= v0) & (d <= pd.Timestamp(vend))].reset_index(drop=True)
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


def touch_base_rate(va: pd.DataFrame, w: np.ndarray, pct: float = 0.03, horizon: int = 5) -> float:
    """Weighted fraction of the validate frame that touched `pct` within `horizon`.

    **This is the number the isotonic table anchors to**, and therefore the
    number behind the shipped ~5pp bias. `core/calibration.py` publishes the
    realised rate of each bucket *in its calibration sample*; if that sample
    sits 6pp below the period being scored, every published probability
    inherits the gap however well the model ranks.

    Reported per arm because each calibrates on a different period and would
    therefore ship a different anchor. It is the one column here that speaks
    to the bias rather than to the fan.
    """
    col = train.label_for("peak", horizon)
    if col not in va.columns:
        return float("nan")
    peak = pd.to_numeric(va[col], errors="coerce").to_numpy(float)
    ok = ~np.isnan(peak)
    if not ok.any():
        return float("nan")
    hit = (peak >= pct).astype(float)
    return float((w[ok] * hit[ok]).sum() / w[ok].sum())


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = db_io.get_engine()
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())

    results = {}
    base_rates: dict[str, float] = {}
    for name, start, end, vstart, vend in ARMS:
        purge = name.endswith("purge")
        tr, va = frames(engine, start, end, vstart, vend, purge)
        print(
            f"\n=== {name}: train {len(tr):,} ({start}..{end}"
            f"{', purged ' + str(PURGE_DAYS) + 'd' if purge else ''})"
            f"  validate {len(va):,} ({vstart}..{vend}) ===",
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
        base = touch_base_rate(va, w)
        base_rates[name] = base
        print(f"  validate 3%/5d base rate {base:.4f} (the isotonic anchor)", flush=True)
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

    if "expand" in results and "expand_purge" in results:
        a, b = results["expand"]["err"].abs().mean(), results["expand_purge"]["err"].abs().mean()
        print(
            f"\npurge effect: {a:.4f} -> {b:.4f}  "
            f"({'boundary leaks' if b > a * 1.15 else 'no leak detected'})"
        )

    # **The two like-for-like contrasts.** Everything else in this printout
    # compares arms differing in more than one way.
    print("\n" + "=" * 78)
    print("THE CONTROLLED CONTRASTS")
    print("=" * 78)
    if "expand" in results and "fixed_v26" in results:
        e = results["expand"]["err"].abs().mean()
        c = results["fixed_v26"]["err"].abs().mean()
        verdict = "expanding HELPS" if e < c else "expanding HURTS" if e > c else "no difference"
        print("  training window, same validation data (2026):")
        print(f"    fixed_v26 {c:.4f}  ->  expand {e:.4f}   {verdict}")
    if "fixed" in results and "fixed_v26" in results:
        f0 = results["fixed"]["err"].abs().mean()
        f1 = results["fixed_v26"]["err"].abs().mean()
        print("  validation year, same training data:")
        easier = "2026 is the easier year" if f1 < f0 else "2026 is the harder year"
        print(f"    2022-23 {f0:.4f}  ->  2026 {f1:.4f}   ({easier})")

    # Per family, because the aggregate hid an inversion last time.
    print("\n" + "=" * 78)
    print("BY TASK FAMILY  (peak and trough ship; terminal displays nowhere)")
    print("=" * 78)
    print(f"{'arm':14} {'peak':>12} {'trough':>12} {'terminal':>12}")
    for name, d in results.items():
        fam = d.assign(family=d["head"].str.split("_h").str[0])
        cells = []
        for f in ("peak", "trough", "terminal"):
            sub = fam[fam["family"] == f]
            cells.append(f"{sub['ok'].sum()}/{len(sub)}" if len(sub) else "-")
        print(f"{name:14} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12}")

    print("\n" + "=" * 78)
    print("ISOTONIC ANCHOR  (validate 3%/5d base rate -- speaks to the ~5pp bias)")
    print("=" * 78)
    for name, br in base_rates.items():
        print(f"  {name:14} {br:.4f}")
    print(
        "  The shipped table is anchored to the `fixed` arm's rate. A published\n"
        "  probability inherits the gap between its anchor and the period being\n"
        "  scored, however well the model ranks."
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
