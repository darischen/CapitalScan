# Phase 6 — Sessions 22 through 25

Read ADRs 064, 067, 068, 093, 112, 113 and `DESIGN.md` §7 first. ADR 113 is the governing decision: it opens the phase, cuts the head count from ~56 to 20, retires the reachability heads, and adds a fifth promotion check plus a kill criterion of its own.

**The phase is authorized, not expected to succeed.** ADR 113 says so in its own rationale: fourteen cells at a mean `n_eff` of 93 finding nothing is weak prior support for a model conditioning on more features with less effective data per condition. The argument for building it is that it tests a different hypothesis, not that the hypothesis is likely.

---

## 0. Prerequisites, and one of them is blocking

### Resolved before planning: `mcap_log`'s corrupt input

**Closed, and further than this plan asked for.** The plan proposed guarding
`mcap_usd` at `universe` write time. That landed, and the cause was closed at
ingest as well:

| Piece | What it does |
|---|---|
| ADR 145 | Restates the filed share count onto the split-adjusted price basis. 446 of ~929 tickers carry a split, so this was the larger of the two defects |
| ADR 146 | Catches the x1,000 class by local shape: >50x the median of the four nearest neighbours per side, recovering to within 5x on division by exactly 1,000. 33 filings across 17 tickers, zero false positives, and it survives PSKY and WULF — which a global-median test does not |
| `McapPlausibility` | The $6T output ceiling in `run_universe`, writing `bar_rejects` rather than silently nulling. This is the guard the plan specified |

**Stage 7 measures the result:** zero universe rows above $5T, maximum $4.84T
(AAPL, real), 51,837 universe rows, 6,295 `in_trade`, 863,489 events. The 33
stored rows were deleted via `scripts/adr146_clear_scale_errors.sql`.

Session 22 reads `events.mcap_usd` directly. No feature-time plausibility
filter is needed, and adding one would be a second implementation of a rule
that already has a home.

### The measurement Phase 6 must cite

ADR 113 requires Phase 6 to cite ADR 112's measurement rather than route
around it. **Cite stage 7, not the figures in earlier drafts of this plan:**

| | Stage 7 |
|---|---|
| Events | 863,489 |
| Min q, train | 0.6729 |
| Min q, validate | 0.7317 |
| Scored denominator | 48 train / 28 validate |
| Confirmation | Sixth |

The denominator is 48/28 across all six measurements, so the comparison is
like-for-like rather than a shifting grid.

### Blocking: ETF rows have no sector, and `sector` is a categorical feature

`sector` is in §7.3's feature list, as a categorical. `BACKLOG.md` records that `sector` and `industry` are NULL on QQQ, and names IBIT as the case that makes it concrete rather than hypothetical.

ADR 068 pins sector as the granularity that replaces ticker identity. A NULL category is a category, and the model will learn "this is the ETF bucket," which is ticker identity wearing a different label for a set of one or two names.

Decide before Session 22: ETFs get their own sector value, or ETFs are excluded from training while remaining tradeable. Either is defensible. Silence is not, because the default is the third option nobody chose.

### Read before Session 22: the sweep that confirmed its own error

Stage 7's stale-event sweep matched nothing, because
`'backtest_compute_20260822T093630_...'` sorts greater than
`'backtest_compute_20260822'`. **The verification query reused the broken
predicate**, so it confirmed the error rather than catching it. 644 events
survived, and the harness passed them: those three tickers were absent from
the new run entirely, and `_check_non_overlap` cannot catch what has no
competing cluster head.

This is the same shape as the `peak_labels` defect in Session 10, where the
test replayed the statement's own logic in pandas and therefore copied its
bug. In both cases the check was derived from the thing being checked.

**It reappears directly in task 22.4.** Frame reconciliation is also a check
that passes when a population is *missing* rather than wrong: a frame built
from a filter that silently matches too little reconciles perfectly against
the same filter.

Reconcile against a `run_id` count, not against a filter that assumes one run
per day. The acceptance criteria in 22.4 are written against that.

### Housekeeping: the ADR index, now fixed and now tested

129 index rows against 146 bodies; ADRs 130-146 were absent from the table.
Third occurrence after ADR 094 and ADRs 110-111. Fixed 2026-08-22, and
`test_decisions_index.py` now checks index-versus-body membership, duplicate
numbers, and ascending order in both — so the fourth occurrence fails CI
instead of waiting for an audit.

---

## 1. Session map

| Session | Delivers | Gate |
|---|---|---|
| 22 | Feature builder, purged walk-forward splitter, the training frame | The frame reproduces `events` exactly; no feature reads t or later |
| 23 | Twenty heads, LightGBM, the CV loop | Twenty heads fit; cross-horizon monotonicity holds on the peak fan |
| 24 | Isotonic calibration, five-check promotion gate, **ADR 113's kill criterion** | Check 5 evaluated and recorded, whichever way it falls |
| 25 | Forward log, live inference, `/forward`, Phase 6 close | Predictions written before outcomes exist; Phase 6 gate |

Session 24 is the phase's decision point. If check 5 fails, Session 25 becomes a close-out rather than a build.

---

# Session 22 — Features and the training frame

## Scope

In: the twenty-two features from §7.3, the purged walk-forward splitter, and the training frame assembled from `events` and `path`.

Out: any model. Nothing in this session fits anything.

## Model assignment

| Task | Model | Reason |
|---|---|---|
| 22.1 Feature builder | Sonnet | Twenty-two features, every one a lookahead opportunity |
| 22.2 Purged walk-forward splitter | Sonnet | The purge and embargo are where leakage hides |
| 22.3 Target construction from `path` | Sonnet | $M_h$ must come from `path`, not from `events.mfe` |
| 22.4 Training frame assembly | Haiku | Join and materialize against a settled contract |
| 22.5 Tests and documentation | Haiku | Inventory |

## 22.1 Feature builder

Rules:

- All twenty-two features come from the event row, which is what makes "available at t−1" structural rather than tested. Any feature computed by re-reading `bars` or `indicators` breaks that guarantee and needs its own argument.
- §7.3's exclusions are enforced, not documented: no raw price, no raw band level, no `era`, no `ticker` identity. A test reads the feature list and fails if any appears.
- `mcap_log` reads the guarded `mcap_usd`. A NULL propagates as NULL; it is not imputed. LightGBM handles missing natively and an imputed market cap is a fabricated one.
- `k_minus_d` and `atr_14/close` are derived, so their derivation is pinned by test against hand-computed values.
- `distance to mid in ATR units` is the one feature in §7.3 with no column behind it. Define it explicitly: $(\text{close} - \text{bb\_mid}) / \text{atr}_{14}$, and say so in the docstring.

Acceptance:

- Twenty-two features, count asserted against §7.3.
- A structural test asserting no excluded field appears in the frame.
- Derived features hand-verified on three events.
- NULL `mcap_usd` produces NULL `mcap_log`, not zero and not a mean.
- A test asserting every feature column exists on `events` or is derived from columns that do, with the derivation named.

## 22.2 Purged walk-forward splitter

The mechanism that makes cross-validation honest on overlapping labels.

Rules:

- Purge: training rows whose label window overlaps a validation row's are dropped. With $h = 10$ the window reaches eleven trading days, so the purge is eleven sessions, not five.
- Embargo after each validation fold, sized to the same eleven sessions.
- Splits are chronological. A random K-fold on time-series events is the leakage this exists to prevent.
- The splitter is pure: it takes dates and returns index arrays. No database, no frame.

Acceptance:

- A test constructing an event whose label window crosses a fold boundary and asserting it is purged.
- Purge width verified as eleven sessions rather than five, since $h = 10$ is in ADR 113's horizon set.
- No training index ever appears after any validation index in the same fold.
- Embargo verified by asserting a gap of the specified width after each fold.
- A property test: for any fold, $\max(\text{train date}) + \text{purge} \le \min(\text{validate date})$.

## 22.3 Target construction

Twenty targets: $\hat{Q}_\tau(R_h)$ and $\hat{Q}_\tau(M_h)$ for $\tau \in \{0.05, 0.25, 0.50, 0.75, 0.95\}$ and $h \in \{5, 10\}$.

Rules:

- $M_h = \max_{1 \le t \le h} R_t$ comes from `path.favorable`, **not** from `events.mfe`. ADR 093's amendment is explicit about why: `mfe` is bounded by `exit_idx` and therefore by `ExitParams.max_hold_days`, so it cannot see day 10 and it moves on every exit sweep. Training on it couples the model to config.
- $R_h$ comes from `events.fwd_ret_5d` and `fwd_ret_10d`, which is the same source Phase 4's labels used.
- Events whose forward window has not closed are excluded. `fwd_window_days >= entry_offset + h` per the label source contract.
- Structural properties are asserted on the constructed targets, not assumed: $M_h \ge R_h$, and $M_5 \le M_{10}$.

Acceptance:

- A test asserting no target reads `events.mfe`. Structural, by import or by column name.
- $M_h \ge R_h$ on every training row.
- $M_5 \le M_{10}$ on every training row. Per ADR 093's amendment this is arithmetic, so a violation is a bug and not a tolerance.
- Incomplete-window exclusion verified against a fixture where inclusion would change a target.

## 22.4 Training frame assembly

Rules:

- The frame's row count is reconciled against a **`run_id` count**, not
  against the filter that built it. A filter reconciled against itself agrees
  with itself; that is what the stage 7 sweep demonstrated at a cost of 644
  surviving events.
- Every `run_id` present in the training population is enumerated and counted
  before assembly. A frame drawn from two `cscan events` runs is a different
  population from one drawn from one, and nothing downstream would say so.
- Any prefix match on `run_id` is rejected. `LIKE 'backtest_compute_2026MMDD%'`
  is the exact predicate that failed; equality against enumerated values is
  the replacement.

Acceptance:

- Row count reconciles against `SELECT run_id, count(*) FROM events GROUP BY 1`
  restricted to the config, with every `run_id` accounted for by name.
- A test constructing a frame from a filter that silently matches a subset,
  asserting the reconciliation **fails**. A reconciliation that only passes on
  correct input has not been shown to detect anything.
- Two builds against identical data are byte-identical.
- The frame carries `event_id` so any row is traceable back.
- The `run_id` set used to build the frame is persisted alongside it.

## Session 22 gate

1. Twenty-two features, no excluded field present.
2. Every feature available at t−1 by construction.
3. Purge and embargo at eleven sessions, verified by property test.
4. Twenty targets, none reading `events.mfe`.
5. $M_h \ge R_h$ and $M_5 \le M_{10}$ on every row.
6. Frame reconciles against an enumerated `run_id` count, and the
   reconciliation is shown to fail on a deliberately truncated population.
7. No prefix match on `run_id` anywhere in the assembly path.
8. Deterministic.

---

# Session 23 — Twenty heads

## Scope

In: LightGBM quantile regression, twenty heads, the CV loop, feature importance.

Out: calibration, promotion, serving.

## Model assignment

| Task | Model | Reason |
|---|---|---|
| 23.1 Head definitions and the fit loop | Sonnet | Twenty heads sharing one CV loop and one feature set |
| 23.2 Quantile crossing and monotonicity | Sonnet | Two distinct constraints, one arithmetic and one not |
| 23.3 Feature importance | Haiku | Reporting against a settled fit |
| 23.4 Training artifacts and provenance | Haiku | Model files, `git_sha`, `config_hash` |
| 23.5 Tests and documentation | Haiku | Inventory |

## 23.1 Head definitions and the fit loop

Rules:

- One shared feature set, twenty independent heads, per ADR 064's structure carried forward by ADR 113.
- `sector` is categorical, per ADR 068. LightGBM's native categorical handling, not one-hot.
- Heads share the walk-forward CV loop and feature construction, which is why ADR 093 could say training time scales toward rather than with head count.
- Every artifact carries `git_sha` and `config_hash`. A model file that cannot say which population it was fit on is not reproducible.

Acceptance:

- Twenty heads fit and persisted.
- Two runs with the same seed produce identical predictions.
- A test asserting `sector` is passed as categorical rather than encoded.
- Training time recorded. ADR 064's five-minute figure was for eleven heads on a smaller population; the current figure belongs in `RESULTS.md`.

## 23.2 Quantile crossing and monotonicity

Two different problems, and conflating them is the failure mode.

**Within a horizon**, quantiles can cross: $\hat{Q}_{0.25}(R_5) > \hat{Q}_{0.50}(R_5)$ is possible from independent fits. DESIGN §7.4 fixes this post-fit by sorting. That is accepted practice and stays.

**Across horizons**, two cases differ:

| Fan | Constraint | Status |
|---|---|---|
| Peak | $Q_\tau(M_5) \le Q_\tau(M_{10})$ | **Arithmetic.** A maximum over a longer window cannot be smaller. A violation is a bug |
| Terminal | $Q_\tau(R_5) \le Q_\tau(R_{10})$ | **Not arithmetic.** A drifting-down series violates it legitimately |

ADR 093 deferred the terminal question to Phase 6. This is Phase 6.

Rules:

- Peak-fan cross-horizon violations raise. They cannot be sorted away, because sorting would hide a fit that contradicts the data's own arithmetic.
- Terminal-fan cross-horizon ordering is measured and reported, not enforced. Record how often it inverts and by how much; that is a finding about the population, not a defect.

Acceptance:

- A test constructing a peak-fan violation and asserting it raises.
- Within-horizon sorting verified.
- Terminal-fan inversion rate measured and recorded in `RESULTS.md`.

## 23.3 Feature importance

Needed by ADR 067 check 4, which fails a candidate whose feature importance shifted more than 40%.

Acceptance:

- Per-head importance persisted alongside the model.
- A first model has no incumbent, so check 4 is recorded as not evaluable rather than passed.

## Session 23 gate

1. Twenty heads fit, persisted with `git_sha` and `config_hash`.
2. Deterministic under a fixed seed.
3. `sector` categorical.
4. Within-horizon crossing fixed by sorting.
5. Peak-fan cross-horizon violations raise.
6. Terminal-fan inversion rate measured and recorded.
7. Feature importance persisted per head.

---

# Session 24 — Calibration, the gate, and the decision

**This is the session the phase turns on.**

## Scope

In: isotonic calibration, the five-check promotion gate, and ADR 113's kill criterion evaluated and recorded.

Out: serving, live inference, `/forward`.

## Model assignment

| Task | Model | Reason |
|---|---|---|
| 24.1 Isotonic calibration | Sonnet | Fit on validation, not training, and the distinction is the whole point |
| 24.2 Checks 1 through 4 | Haiku | ADR 067's four, mechanically specified |
| 24.3 Check 5, the baseline comparison | Sonnet | The check that makes the phase falsifiable |
| 24.4 Kill criterion evaluation | Sonnet | The decision, recorded whichever way it falls |
| 24.5 Documentation | Haiku | `RESULTS.md`, `BUILD.md` |

## 24.1 Isotonic calibration

Rules:

- Fit on the validation fold, applied to held-out predictions. Fitting on training data calibrates to what the model already memorized.
- Quantile coverage per $\tau$ measured before and after.
- Twenty heads means twenty coverage checks, against ADR 093's amended fifty. ADR 113 cut the head count partly for this reason.

Acceptance:

- Calibration fit on validation only, asserted structurally.
- Coverage per $\tau$ recorded before and after for all twenty heads.
- A test asserting isotonic regression is monotone by construction.

## 24.2 Checks 1 through 4 (ADR 067)

1. Expected calibration error strictly decreased
2. Brier score did not increase by more than 0.002
3. Quantile coverage within 5 points of nominal for every $\tau$
4. No feature's relative importance shifted by more than 40%

Rules:

- All four are relative to an incumbent. On a first model there is no incumbent, so checks 1, 2, and 4 are **not evaluable** and are recorded as such rather than as passes. That gap is exactly why ADR 113 added check 5.
- Check 3 is evaluable on a first model and applies.
- Checks 2 and 3 reference Brier score, which ADR 067 wrote for binary reachability heads. ADR 113 retired those. Restate check 2 against pinball loss for the quantile fans, or record it as not applicable, but do not silently apply a binary metric to a quantile head.

Acceptance:

- Checks 1, 2, 4 recorded as not evaluable on a first model, with the reason.
- Check 3 evaluated for all twenty heads.
- The Brier-versus-pinball question resolved explicitly in an ADR amendment, not in code comments.

## 24.3 Check 5 — the baseline comparison (ADR 113)

> The model's out-of-sample pinball loss must beat the unconditional baseline — the same per-ticker-year empirical distribution the cell grid measured against, fit with no features.

Rules:

- The baseline is the per-ticker-year empirical distribution from `research/baselines.py`, the same one Phase 4 used. Not a global constant, not a Gaussian fit.
- Pinball loss per $\tau$, per horizon, on the validation split.
- The comparison is per head. A model beating the baseline on eight of twenty heads is a different result from beating it on all twenty, and the report says which.
- ADR 067's own baseline requirement said the same thing for the retired reachability heads: "If the model does not beat the empirical lookup on Brier score, the lookup ships alone." Check 5 is that rule carried to the quantile surface.

Acceptance:

- Pinball loss computed against the same baseline machinery Phase 4 used, verified by calling it rather than reimplementing.
- Per-head, per-$\tau$, per-horizon results recorded.
- A test asserting the baseline is fit with no features.

## 24.4 The kill criterion (ADR 113)

> If the model fails check 5 on the validation split — no better than the unconditional baseline in pinball loss at any horizon — the two-indicator hypothesis is retired at the model layer as well as the cell layer, and Phase 6 closes with that recorded.

Rules:

- Evaluated and recorded whichever way it falls. The gate is that the number is computed correctly, not that the model wins. Session 13's gate item 4 was worded the same way for the same reason.
- If it fires, `RESULTS.md` records it with the detail ADR 033 asks for, and an ADR records the closure. Session 25 becomes a close-out.
- If it does not fire, the result is a model that beats the base rate on some heads, which is a finding and not a validated edge. It says nothing about tradeable profit, and the surfaces in Session 25 must not imply otherwise.

Acceptance:

- Criterion evaluated, result recorded in `RESULTS.md` and in an ADR.
- The recording happens before any Session 25 work begins.

## Session 24 gate

1. Calibration fit on validation only.
2. Coverage per $\tau$ recorded for twenty heads, before and after.
3. Checks 1, 2, 4 recorded as not evaluable with the reason; check 3 evaluated.
4. The Brier-versus-pinball question resolved in an ADR.
5. Check 5 computed against the Phase 4 baseline machinery, per head.
6. ADR 113's kill criterion evaluated and recorded, whichever way.
7. Deterministic.

Item 6 is the phase's decision point.

---

# Session 25 — Forward log, serving, and the Phase 6 close

**Scope depends on Session 24.** If the kill criterion fired, this is a close-out: record, retire, document, and leave `predict()` returning `NotFound`.

What follows assumes it did not fire.

## Scope

In: `predictions` and `outcomes` wired, live inference in the poller, `/forward`, `predict()` returning real predictions, Phase 6 close.

Out: anything not in ADR 113's twenty heads.

## The schema question that comes first

`predictions` was written in Session 1 against ADR 064's eleven heads:

```sql
q05, q25, q50, q75, q95,
p_touch_2, p_touch_3, p_touch_5, p_touch_10,
p_adverse_3, p_adverse_5
```

Five terminal quantiles at one horizon, four reachability, two adverse. ADR 113's surface is twenty heads: five $\tau$ across two horizons, twice.

The reachability and adverse columns have no head behind them any more. The quantile columns carry no horizon.

Decide before building: migrate `predictions` to a horizon-aware shape, or store the fan as JSONB. A migration is cleaner to query and the table is empty, so there is no data to move. Either way it is an ADR, because `outcomes` and `v_forward` both read these columns and DESIGN §8's `/forward` route renders them.

## Model assignment

| Task | Model | Reason |
|---|---|---|
| 25.1 `predictions` migration | Sonnet | Schema change with three consumers |
| 25.2 `predict()` handler | Sonnet | The Session 15 contract's `NotFound` becomes real |
| 25.3 Live inference in the poller | Sonnet | Feature construction at t−1 in a live path |
| 25.4 Outcome resolution | Haiku | Join predictions to realized paths |
| 25.5 `/forward` route | Haiku | Rendering against a settled contract |
| 25.6 Phase 6 close | Haiku | Gate, documentation, phase record |

## 25.2 `predict()` handler

Session 15's contract returns `NotFound` for every input, with a test asserting it that is expected to fail here. That failure is the deliberate signal.

Rules:

- Predictions are never regenerated, per ADR 029. A prediction is a record of what the model said at a moment.
- Every prediction stores `features_json`, so a later disagreement can be reproduced.
- `model_version` on every row.
- The response validator applies. A quantile fan is not a probability, so invariant 8's `n_eff` requirement needs an explicit ruling rather than a silent exemption: decide in an ADR what a quantile head must carry to leave the layer.

Acceptance:

- Session 15's `NotFound` test updated deliberately, with the change visible in the diff.
- `features_json` round-trips and reproduces the prediction exactly.
- A test asserting no code path regenerates an existing prediction.

## 25.3 Live inference

Rules:

- Features come from the same builder Session 22 wrote. A second implementation in the live path is the `peak_labels` failure mode: two implementations of one calculation, agreeing until they do not.
- ADR 140: the nightly is authoritative for every grain, and the poller's observation is not an entry price. Live inference predicts; it does not create an event.

Acceptance:

- A test asserting the live path calls the same feature builder, by import.
- Live and backtest features agree on the same event, byte for byte.

## 25.5 `/forward` route

Rules:

- Renders `v_forward`, which joins predictions to outcomes.
- Unresolved predictions render as unresolved, not as zero.
- ADR 112's result is visible here as on every other surface, per the Phase 5 gate's item 8. A model that beats a base rate on some heads is not an edge, and the page says so.

## Phase 6 gate

1. Twenty heads fit, calibrated, and promoted or refused by the five checks.
2. ADR 113's kill criterion evaluated and recorded.
3. `predictions` schema matches the twenty-head surface.
4. `predict()` returns real predictions or the phase closed without it.
5. Live and backtest features agree byte for byte.
6. No prediction is ever regenerated.
7. `/forward` renders, unresolved predictions distinguishable from zero.
8. ADR 112's result visible on the forward surface.
9. Coverage per $\tau$ within 5 points of nominal, or recorded as failing.
10. `test_holdout_firewall.py` and `test_schema_drift.py` pass, the latter having run.

---

## What will be tempting across the whole phase

**Reading a model that beats the baseline as an edge.** Check 5 asks whether the model beats no model. Beating a base rate on a quantile is not the same claim as a tradeable edge, and Phase 4 measured the second one directly and found nothing.

**Training on `events.mfe` because the column is right there.** ADR 093's amendment names this explicitly. `mfe` is bounded by the exit policy, so it couples the model to a config that changes on every sweep.

**Sorting away a peak-fan cross-horizon violation.** Within-horizon crossing is a fit artifact and sorting is correct. Cross-horizon on the peak fan is arithmetic, and sorting hides a fit that contradicts the data.

**Recording checks 1, 2, and 4 as passed on a first model.** They compare against an incumbent that does not exist. Passing by default is the gap ADR 113's check 5 was added to close.

**Imputing a NULL `mcap_log`.** LightGBM handles missing natively. An imputed market cap is a fabricated one, and the guard exists precisely to produce that NULL.

**Skipping the mcap guard because the corrupt rows are only 22 of 51,828.** Six are `in_trade`, and a 1000x error on a log-scaled feature is larger than the feature's entire real range.

---

## Sequencing

```
ADR 145 + 146 + McapPlausibility             <- done, stage 7 measured
rebuild (events + path + harness)            <- done, 863,489 events
ADR: ETF sector decision                     <- the one blocker left
Session 22  features, splitter, targets
Session 23  twenty heads
Session 24  calibration, gate, kill criterion  <- decision point
Session 25  serving, or close-out
```

One blocker remains, and it is a decision rather than a build: whether ETFs
get a sector value or are excluded from training while staying tradeable.
`sector` is a categorical feature under ADR 068, chosen as the granularity
that replaces ticker identity. A NULL category is a category, and on a set of
one or two names it is ticker identity under another name.
