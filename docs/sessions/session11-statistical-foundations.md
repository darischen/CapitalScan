# Session 11 — Statistical primitives and self-validation

Read `DECISIONS.md`, `DESIGN.md` §6, and `BUILD.md` first. This document says what to build and in what order. Those say why.

Session 11 opens Phase 4. It is a pure-computation session. Nothing in it produces a number about a real signal.

---

## 0. Scope

### In scope

The statistical machinery every later Phase 4 session depends on, plus the self-validation proving the machinery works before any real event touches it.

Four things get built:

1. Interval and multiple-testing primitives. Wilson confidence intervals, standard errors computed on `n_eff` rather than `n`, and the Benjamini-Hochberg procedure producing both `p_value` and `q_value`.
2. Baselines. Per ticker-year empirical baseline, per ticker-year parametric baseline, and the disagreement flag between them.
3. `n_eff` and its `rho_bar` inputs, per ADR 098. Empirical co-fire-weighted correlation as the value in use, factor-implied correlation as a stored diagnostic.
4. Self-validation. The null test on driftless synthetic data and the recovery test on known drift.

### Out of scope

- Any write to `cell_stats` or `benchmarks`. Those tables stay empty through this session. Session 12 populates the first.
- Cell enumeration, the headline grid, suppression logic. Session 12.
- Any benchmark arm. Session 13.
- Breadth reporting. ADR 099 places it in Session 12 and 13, not here.
- Any change to indicator computation, signal definitions, exit rules, event detection, or the backtest engine.
- Anything touching the serving store.

### Non-goal worth stating explicitly

This session produces no finding about the signal. Every number it generates comes from synthetic data with a known answer. Success looks like a set of functions whose behavior is pinned by test and a null test refusing to find an edge in a random walk.

### Why self-validation comes first

ADR 087 makes the null test the primary statistical guard. A guard built after the thing it guards gets tuned until it passes. Building it against synthetic data with a known answer, before any real event enters the pipeline, is what keeps it honest.

`research/synthetic.py` already exists and is the one piece of Phase 4 infrastructure built ahead of time. Read it before starting task 11.4.

---

## 1. Prerequisites

Three runs must complete and be recorded in `RESULTS.md` before this session opens. None involves new code.

| Step | Command | Why |
|---|---|---|
| 1 | Full-universe backtest under the live config | Phase 4 reads `events` label columns. The live hash has no full backtest labels |
| 2 | `cscan path peak-labels` | Rewrites `peak_ret_*d` after the 2026-08-09 filter fix |
| 3 | `cscan path reconcile` | Confirms the residual sits at the documented boundary events under the new hash |

Also confirm before starting:

- `db/schema.sql` matches a live dump. `tests/integration/test_schema_drift.py` is the check, and it skips silently when `pg_dump` is unreachable, so confirm it ran rather than skipped.
- ADRs 098, 099, and 100 are committed to `DECISIONS.md`.

If step 1 has not run, stop. A statistics layer built against unlabelled events produces suppressed cells everywhere and tells you nothing about whether the layer works.

---

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 11.1 Interval and multiple-testing primitives | Haiku | Textbook formulas against a specified contract. Verifiable against published reference values |
| 11.2 Baselines | Sonnet | Per ticker-year windowing with lookahead risk. Errors here bias every reported edge |
| 11.3 `n_eff` and `rho_bar` | Sonnet | Two estimators, a new table, and the correction feeding every confidence interval |
| 11.4 Self-validation | Sonnet | The correctness gate for the whole session, and for Phase 4 |
| 11.5 Tests and documentation | Haiku | Inventory work against a settled design |

No Opus needed. The session isolates the two high-risk steps, baselines and self-validation, into separately verifiable tasks rather than threading them through everything.

Order strictly. Do not begin 11.4 before 11.3 passes.

---

## 3. Task breakdown

### 11.1 Interval and multiple-testing primitives

Pure functions. No IO, no database, no configuration reads. These live under `core/` per invariant 1, or under `research/` if any of them needs a config object.

**Wilson confidence interval.** Not the normal approximation. At `p = 0.03` and `n_eff = 35` the normal interval's lower bound goes below zero, and a suppressed cell beats a negative probability.

**Standard error on `n_eff`:**

```
SE = sqrt( p * (1 - p) / n_eff )
```

`n_eff`, never `n`. Any function accepting a raw count where `n_eff` belongs is a bug regardless of whether the caller currently passes the right thing.

**Benjamini-Hochberg.** Sort the `m` p-values ascending, find the largest `k` where `p_(k) <= (k/m) * alpha`, reject 1 through k. The q-value is `min` over `j >= i` of `(m/j) * p_(j)`. Store both `p_value` and `q_value`.

`alpha` comes from `StatsParams.fdr_alpha`, never a literal.

Acceptance:

- Wilson bounds match published reference values on at least six documented cases spanning small `n`, `p` near 0, `p` near 1, and `p` near 0.5.
- The Wilson lower bound never goes below 0 and the upper never exceeds 1, pinned by property test across the full parameter space.
- Benjamini-Hochberg reproduces a hand-computed example, including the monotonicity enforcement in the q-value definition. A naive implementation omitting the running minimum produces non-monotone q-values and passes a careless test.
- Feeding `m` p-values all equal to 1.0 rejects nothing. Feeding `m` p-values all near 0 rejects everything. Both pinned.
- A property test asserting `q_value >= p_value` for every input.
- No function in this module accepts a raw sample count in a position where `n_eff` is required. Enforced structurally, by parameter naming and a test reading the signatures, not by comment.

### 11.2 Baselines

Two baselines per ticker-year, both stored, per ADR 062 and DESIGN §6.2.

**Empirical.** For ticker `i` in year `y`: every trading day, the 5-day forward return, and the fraction reaching the target. Roughly 250 overlapping observations per ticker-year.

**Parametric.** From trailing 252-day drift and volatility:

```
P_base = 1 - Phi( (X - mu_5d) / sigma_5d )
mu_5d    = mu_ann / 50.4
sigma_5d = sigma_ann * sqrt(5 / 252)
```

Rules the implementation must follow:

- The trailing 252-day window for the parametric baseline ends strictly before the observation day. A window including the day being predicted is lookahead, and it will produce a baseline that looks excellent.
- Return measurement uses total-return `adj_close` through `core/returns.forward_returns`, not `path.terminal`. See the label source contract in `BUILD.md`. `path.terminal` is split-adjusted close anchored to entry price, a different quantity by design.
- A cell's baseline is the event-weighted mean of its constituent events' per-ticker-year baselines. Never a pooled rate over all days. DESIGN §6.2 is explicit and the difference is material.
- Both baselines are stored. Edge is reported against the empirical one. Parametric is a diagnostic, and a large gap flags a non-normal return distribution for that ticker-year.
- Ticker-years with insufficient history produce null, never a silently shortened window.

Acceptance:

- Hand-verify the empirical baseline on three ticker-years against a spreadsheet built from raw price data, including one spanning a stock split.
- The parametric baseline reproduces the worked example in DESIGN §6.2: at 30% annualized drift and 40% volatility, `mu_5d` near 0.60%, `sigma_5d` near 5.6%, and `P(R_5d >= 2%)` near 40.1%, against 36.1% at zero drift.
- A test constructing a ticker-year where the trailing window would include the observation day, asserting the computed value matches the strictly-prior window.
- Event-weighted cell aggregation verified against a hand-computed case where a pooled rate gives a visibly different answer.
- The disagreement flag fires on a deliberately fat-tailed synthetic series and stays quiet on a Gaussian one.
- Null propagates. A ticker-year without 252 days of prior history produces null, and a cell containing such events reports how many.

### 11.3 Effective sample size and rho-bar

Implements ADR 098. Read it before starting.

```
n_eff = n / (1 + (k_bar - 1) * rho_bar)
```

`k_bar` is the mean `cofire_count` across the cell's events, already stored.

**Empirical `rho_bar`, the value in use.** Mean pairwise correlation of 5-day returns among co-firing tickers, on overlapping windows, weighted by the number of days each pair fired together. Pairs never co-firing are excluded entirely. One value per era.

**Factor-implied `rho_bar`, diagnostic only.** From a single-factor decomposition against the S&P series in `market_days`:

```
r_i = alpha_i + beta_i * r_m + eps_i

rho_ij = (beta_i * beta_j * sigma_m^2) / (sigma_i * sigma_j)
```

This is not the value feeding `n_eff`. It assumes residual independence, understates `rho_bar`, and therefore inflates `n_eff`, which is the unsafe direction.

**New table.** `rho_era`, keyed `(era, config_hash)`, with `run_id`, `computed_at`, `git_sha`, both estimates, the gap, `n_pairs`, `n_cofire_days`, and `mean_beta`. Schema is in ADR 098. Migration is mechanical and reversible.

Era bounds come from `StatsParams.era_bounds`, never literals.

Acceptance:

- Migration applies cleanly to a fresh database and to a copy of the current research database, and reverses with no orphaned objects.
- Empirical `rho_bar` hand-verified on a small constructed event set with three tickers and known correlations, where the weighted and unweighted answers differ visibly.
- A test asserting pairs that never co-fired are excluded, constructed so their inclusion would move the result.
- Factor-implied `rho_bar` reproduces the analytical value on synthetic data generated from a known single-factor model with known betas and zero residual correlation, within a documented tolerance.
- On synthetic data with deliberately correlated residuals, the empirical estimate exceeds the factor-implied one and `rho_gap` is positive. This pins the direction of the bias ADR 098 relies on.
- `n_eff <= n` always, and `n_eff = n` exactly when `k_bar = 1` or `rho_bar = 0`. Property test.
- `n_eff` is monotone decreasing in both `k_bar` and `rho_bar`. Property test.
- Two runs against identical data write identical values ignoring `run_id` and `computed_at`.
- A row is written per era per config, and running against a second config does not overwrite the first.

### 11.4 Self-validation — session gate

This is the task the session exists to pass. Implements DESIGN §6.13 and ADR 087.

**Null test.** Driftless geometric Brownian motion, 50 synthetic tickers, 2,500 days. Run the full pipeline built in 11.1 through 11.3. The fraction of cells at `q < 0.05` must not exceed 5%.

A random walk contains no edge. If the layer finds one, the layer has a bug, and every real result Phase 4 later produces is suspect.

**Recovery test.** Inject known drift. The computed parametric baseline must match the analytical value within 1 percentage point.

Rules:

- Synthetic generation is seeded and reproducible. Two runs produce identical series.
- The null test runs the real code path, not a simplified version of it. A test exercising a parallel implementation proves nothing about the implementation shipping.
- A failing null test blocks the session. Do not tune the threshold to pass. If the observed rate exceeds 5%, the cause is a bug in 11.1 through 11.3 and it gets found.
- Report the observed rate, not only pass or fail. A run at 4.9% and a run at 0.2% are different states of the world, and the first deserves investigation even though it passes.

Acceptance:

- Null test passes at the specified thresholds, with the observed rate recorded in `RESULTS.md`.
- Recovery test passes within 1 percentage point, with the observed gap recorded.
- Both tests run from a single documented entry point, re-runnable at will.
- Seeding verified. Two runs produce identical output.
- A deliberately broken variant is confirmed to fail the null test. Introduce a known bug, for example standard errors on `n` rather than `n_eff`, and confirm the null test catches it. A guard nobody has seen fail is not known to work.

### 11.5 Tests and documentation

Acceptance:

- Test inventory added to `TESTS.md` covering interval bounds, Benjamini-Hochberg monotonicity, baseline lookahead, `rho_bar` weighting, `n_eff` monotonicity, and both self-validation tests.
- Property-based tests for the invariants named in 11.1 through 11.3.
- `RESULTS.md` gains a Session 11 section under the existing Phase 4 heading, recording the null test rate, the recovery test gap, and the four per-era `rho_bar` values with their factor-implied counterparts and gaps.
- `DESIGN.md` §6.3 updated to reference `rho_era` and ADR 098 rather than describing `rho_bar` as an unspecified stored constant.
- `BUILD.md` updated to list Session 11 and its gate outcome.

---

## 4. Session gate

Session 11 passes when all of the following hold:

1. The null test on driftless synthetic data produces no more than 5% of cells at `q < 0.05`, and the observed rate is recorded.
2. The recovery test matches the analytical parametric baseline within 1 percentage point.
3. A deliberately introduced bug is confirmed to fail the null test.
4. Wilson bounds match published reference values and never leave `[0, 1]`.
5. Benjamini-Hochberg q-values are monotone and reproduce a hand-computed example.
6. The parametric baseline reproduces DESIGN §6.2's worked example.
7. `rho_era` holds four rows per config with both estimates, and `rho_gap` is positive on correlated-residual synthetic data.
8. `n_eff <= n` holds across the property-generated case set.
9. Everything is deterministic. Two runs on identical inputs produce identical output ignoring run identifiers.
10. Documentation and `RESULTS.md` entries committed.

Failing item 1 or item 3 blocks the session regardless of everything else. Item 3 is the one most likely to be skipped and the one carrying the most information: it is the difference between a guard believed to work and a guard observed to work.

---

## 5. Keeping this inside its time budget

Sessions 3 and 9 both ran long because implementation and bug discovery interleaved. Three structural choices here prevent a repeat.

**Every number in this session has a known correct answer.** Wilson bounds are published. The parametric baseline has a worked example in DESIGN §6.2. Synthetic data is generated from a known model. Nothing here requires deciding whether a surprising number is a bug or a finding, which is where the time went before.

**The gate sits on synthetic data, not real events.** A failure is reproducible in seconds and does not depend on a database state, a config generation, or a backfill.

**Nothing downstream reads this session's output.** `cell_stats` stays empty. Rolling back is deleting a module and dropping one table.

---

## 6. What Session 12 needs from this one

Stated here so 11.5 can verify the handoff rather than Session 12 discovering a gap.

| Session 12 needs | From |
|---|---|
| Wilson interval on `(p_hit, n_eff)` | 11.1 |
| Benjamini-Hochberg across a cell family | 11.1 |
| Per-ticker-year baselines, both kinds, plus the disagreement flag | 11.2 |
| Event-weighted cell baseline aggregation | 11.2 |
| `n_eff` from `k_bar` and the stored `rho_bar` for the cell's era | 11.3 |
| A `rho_era` row for every era present in the event set | 11.3 |

Session 12 additionally carries three things not built here: the `v_screen` `config_hash` fix from ADR 100, the breadth tercile split from ADR 099, and the headline grid with its suppression floor.

---

## 7. Rollback

- No existing table is modified. `rho_era` is new and additive.
- `cell_stats` and `benchmarks` stay empty through this session.
- No consumer reads anything built here until Session 12.
- Reversal is a migration down plus removing the new modules.
