# Session 23 — the fit

**Date:** 2026-08-25. Follows Session 22's training matrix. ADR 113's twenty
heads, purged walk-forward CV, and the first numbers the model has produced.

## What was built

`core/folds.py` — the fold ladder, purge and embargo. Pure computation; the
trading calendar is an argument because `core/` may not read one. Tests
written first, per the `core/` rule.

`core/pinball.py` — the loss and the unconditional baseline. This is the
instrument ADR 113's kill criterion is measured with, so its failure
direction matters: wrong in the optimistic direction and the criterion
cannot fire.

`research/train.py` — `fit_head`, fitting one head across every fold and
returning fold results. **No booster is returned.** Fitting and promoting
are separate acts (ADR 067) and a function handing back a fitted model
invites someone serving it before the gate has run.

## What was measured

All twenty heads, 125,714 rows, **281 seconds**. Full table in
`RESULTS.md`. Headline: **15 of 20 beat the unconditional baseline on every
fold**.

That number is real and it is not yet evidence of an edge. Three reasons,
all in `RESULTS.md` and worth repeating because the headline travels
further than the caveats:

1. **This is CV inside train.** ADR 113's check 5 is the validate split,
   which has not been touched. The holdout has not been looked at.
2. **Relative improvement flatters the tails.** At τ=0.95 the baseline loss
   is ~0.005, so a 12% gain is ~0.0006. The largest percentages sit where
   the absolute numbers are smallest.
3. **The peak family may be forecasting volatility, not direction.** $M_h$
   is a maximum — bounded below, driven by dispersion — and dispersion is
   predictable from `rv_pct_252d`, `bb_width_pct` and `vix_close`, all in
   the feature set.

The directional heads are the weak ones: `terminal_h5_q50` and
`terminal_h10_q50` improve 0.55–1.00% and each lose a fold. That is
consistent with ADR 112, not against it.

## What the baseline wording turned out to be

ADR 113 check 5 names "the unconditional baseline — the same per-ticker-year
empirical distribution the cell grid measured against". Those are two
different objects and the second cannot be built here:

    train    2010-2021   1,649 ticker-year keys
    validate 2022-2023     430 ticker-year keys
    overlap                  0

ADR 019's split is temporal, so a per-ticker-year baseline fitted on train
has nothing to look up for any validation event. Measured both ways, the
twenty losses agreed to five decimals — the symptom, not a coincidence.

Recorded as an open item rather than resolved. A per-*ticker* baseline
without the year is constructible and is a harder bar, so choosing it makes
the kill criterion more likely to fire. That is a change to a
pre-registered criterion and belongs to whoever set it.

## Two tests that initially checked nothing

Worth recording because both passed while asserting zero against zero.

`test_the_purge_actually_removes_rows` and its embargo twin ran against a
fixture spanning 2010–2013 while the fold under test validated on 2015. No
rows matched, both masks were empty, and the comparison held trivially. The
fixture now spans 2010–2017 and carries a comment saying why.

This is the same class as the contaminated percentile and the `run_id`
string-prefix comparisons from earlier sessions: **the measurement
apparatus agreeing with itself**. It is the recurring failure in this
project and it has now appeared in a test fixture as well as in queries.

## Next

- Check 5 proper: validate split, per head, against the bar in `RESULTS.md`
- Coverage (DESIGN §7.6): the realised fraction below $\hat{Q}_{0.25}$
  should be 25%. A head can fail this while posting a good pinball loss,
  and the product is the probability, so coverage is the metric that
  matters more.
- Isotonic calibration, the promotion gate, the forward log.
