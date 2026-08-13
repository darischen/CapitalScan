# Session 12 — Cell grid and `cell_stats`

Read `DECISIONS.md` (especially ADRs 098 through 106), `DESIGN.md` §6, and `BUILD.md` first. This document says what to build and in what order. Those say why.

Session 12 writes the first real statistics in the system. Session 11 built the machinery and proved it on synthetic data. This session points it at real events.

---

## 0. Scope

### In scope

1. `cell_key` parity between the Postgres function and any Python caller.
2. The twelve-cell headline grid per ADR 102, with suppression at `min_n_eff = 30`.
3. `cell_stats` written for the live config: hit rates, both baselines, edge, Wilson intervals, Benjamini-Hochberg q-values, return distribution, MFE and MAE, reachability at the four fixed targets, `exit_mix`, `earnings_frac`.
4. Descriptive era rows per ADR 103 and breadth tercile rows per ADR 099 as amended by ADR 104.
5. Per-ticker concentration reporting.
6. `v_screen` gains its `config_hash` and `arm` predicates, plus the `arm` column migration.

### Out of scope

- Any benchmark arm. Session 13.
- The drawdown slice and the three-arm chart. Session 14.
- Volatility-scaled reachability targets. Session 14.
- Any change to detection, indicators, exits, or the backtest engine.
- Anything touching the serving store.
- Any recommendation surface. `cell_stats` is a table, not a screen.

### The one-sentence version

Twelve cells, ten of which will render, computed pooled across three train eras, each carrying `n_eff`, a Wilson interval, both baselines, and a q-value, and none of which renders a bare percentage.

---

## 1. Prerequisites

Before this session opens:

| Item | Check |
|---|---|
| ADRs 101 through 106 committed | Present in `DECISIONS.md`, index rows added, ADR 011 and ADR 016 marked superseded, ADR 017 noted |
| ADR 100's DESIGN corrections applied | §6.9's `cell_key()` sentence and the `exit_mix` scoping sentence |
| ADR 099's breadth text amended per ADR 104 | Denominator is the train universe |
| `rho_era` populated for the live config | Four rows under `1835688bf7d760ba` |
| Session 11 gate recorded | `RESULTS.md` carries the measured numbers |

Re-measure the breadth terciles before task 12.4. The values in `RESULTS.md` were computed under ADR 099's original trade-universe denominator and are superseded by ADR 104.

---

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 12.1 `cell_key` parity | Sonnet | Two implementations of one identifier. This is the `peak_labels` failure mode and needs the SQL executed, not transcribed |
| 12.2 Grid enumeration and suppression | Haiku | Twelve cells, one floor, a settled specification |
| 12.3 `cell_stats` writer | Sonnet | Aggregation over real events with lookahead and null-propagation risk |
| 12.4 Era, breadth, and concentration reporting | Haiku | Descriptive splits on cells already tested |
| 12.5 `arm` migration and `v_screen` predicates | Sonnet | A test asserting the fix must fail under the pre-fix view |
| 12.6 Tests and documentation | Haiku | Inventory against a settled design |

Order strictly. 12.3 depends on 12.1 and 12.2. 12.4 depends on 12.3.

---

## 3. Task breakdown

### 12.1 `cell_key` parity

`cell_key()` exists as a Postgres `IMMUTABLE` SQL function taking nine parameters. Any Python caller building the same string is a second implementation.

Rules:

- The parity test executes the Postgres function. A Python replay of its logic is not an independent oracle. The 2026-08-09 peak-label defect survived because the test transcribed the statement under test and copied its bug.
- Cover every coalescing path: null `p_dd_bucket` to `'all'`, null `p_strength` to `'all'`, null `p_era` to `'pooled'`, and the `FM990.999` target formatting.
- Session 12 passes null `p_strength` for every headline cell, since ADR 102 removed it as a dimension. Pin that specific call shape.

Acceptance:

- Parity across at least 20 parameter combinations spanning all four coalescing paths, executed against Postgres.
- A test asserting the function is `IMMUTABLE`, since a volatile function would break any index or generated column built on it later.
- Target formatting verified at 0.02, 0.03, 0.05, and 0.10 from `StatsParams.reach_targets`.

### 12.2 Grid enumeration and suppression

Twelve cells per ADR 102: two sides, three signal-type pairs, two drawdown buckets.

Rules:

- Signal type pairs with side. A long cell reads `bb_lower_touch`, its short counterpart `bb_upper_touch`. Twelve cells, not thirty-six.
- Null `dd_bucket` events are excluded explicitly and the excluded count is reported. `cell_key()` coalesces null to `'all'`, so an unfiltered null would silently merge into an aggregate cell.
- `20-35` and `35+` are excluded per ADR 101. The exclusion is stated in output, not silent.
- Suppression fires at `n_eff < StatsParams.min_n_eff`, with a reason string. No number renders on a suppressed cell.
- `min_n_eff`, `fdr_alpha`, `reach_targets`, and `dd_buckets` all come from `StatsParams`. No literals.
- Cells are computed pooled across eras on the train split per ADR 103.

Two cells are expected to suppress: short `stoch_overbought` 10-20 at `n_eff` near 14, and short `confluence_high` 10-20 at `n_eff` near 5. If a third suppresses, or if either of those renders, something changed and it needs explaining before proceeding.

Acceptance:

- Exactly twelve cells enumerated, verified by test against the ADR 102 table.
- A suppressed cell returns nulls for `p_hit`, `edge`, `ci_low`, `ci_high`, and a populated `suppress_reason`.
- Null `dd_bucket` exclusion tested with a fixture where inclusion would move a cell's `n`.
- The `n_eff` for each of the twelve reproduces the ADR 102 table within a documented tolerance. This is the load-bearing test: it is the only thing connecting the code to the measurement the grid design rests on.

### 12.3 `cell_stats` writer

Every column DESIGN §6.9 specifies, for each of the twelve cells.

Consumer rules that apply to every query in this task:

- Filter on `fwd_window_days >= entry_offset + max_hold_days`. An event whose forward window has not closed carries frozen labels.
- Never read `first_touch_day` without pairing it with `touched_by`. The first is two-valued and the second three-valued, and they disagree on what null means.
- Read labels from `events`, not from `path`, per the label source contract in `BUILD.md`. `path.terminal` is a different quantity by design.
- `n_eff` uses the `rho_era` row matching the cell's era. For a pooled cell, weight `rho_empirical` by each era's event count, as ADR 102's table does.
- Wilson intervals take `n_eff`, never `n`. `wilson_ci`'s parameter is named `n_eff` and a signature test enforces it, but the caller still has to pass the right thing.
- Baselines are event-weighted per ticker-year, never a pooled rate. DESIGN §6.2 is explicit and the difference is material.

Benjamini-Hochberg runs across the family DESIGN §6.8 defines: all headline cells across all ladder targets for one config. Twelve cells times four targets is 48 tests. Era and breadth rows are descriptive and enter no family, per ADR 103 and ADR 099.

Acceptance:

- All twelve cells written with `arm = 'signal'`, keyed `(cell_id, config_hash)`.
- Every rendered cell carries `n_eff`, `ci_low`, `ci_high`, `baseline_empirical`, `baseline_parametric`, `edge`, `p_value_parametric`, and `q_value`. A cell missing any of these is a bug, not a partial result.
- `exit_mix` sums to 1.0 across exit reasons within tolerance, and the fractions match a hand count on one cell.
- `q_value >= p_value` for all 48 tests, and q-values are monotone in sorted p-value order.
- Two runs against identical data write identical values ignoring `run_id` and `computed_at`.
- A second `config_hash` adds rows rather than replacing the first's. This is what ADR 096's composite key was for and it has never been exercised.
- `n_tickers` and `mean_cofire` populated on every cell.

### 12.4 Era, breadth, and concentration reporting

Three descriptive splits on cells already tested pooled.

**Era rows.** One per cell per era, for 2010-2014, 2015-2019, and 2020-2023. Era 2024+ is excluded per ADR 103, because it is exactly the holdout split. Era rows carry no `q_value`.

**Breadth terciles.** Breadth is `cofire_count` over the count of distinct tickers with any event in that quarter, per ADR 104. Terciles cut on the empirical distribution per era. Three rows per cell, no `q_value`.

**Per-ticker concentration.** For each cell, the maximum share any single ticker contributes, and the cell's statistics recomputed with that ticker removed. DESIGN §6.7's threshold is 15%.

Concentration deserves care here. 2015-2019 holds 140,288 event rows across only 196 distinct tickers, so the cap is likely to bind. A cell where one ticker supplies 40% of events is a statement about that ticker.

Acceptance:

- Era rows written for three eras only. A test asserts no row carries era 2024+, and it must fail if the exclusion is removed.
- Era and breadth rows carry null `q_value`, asserted.
- Breadth denominator verified as the train-universe count, with a test that would fail under ADR 099's original trade-universe definition.
- Terciles re-measured under ADR 104 and recorded in `RESULTS.md`, superseding the earlier values.
- Concentration reported for all twelve cells, with the recomputed statistics for any cell exceeding 15%.

### 12.5 `arm` migration and `v_screen` predicates

One migration, one view rebuild, two predicates.

`cell_stats` gains `arm text NOT NULL DEFAULT 'signal'` with a check constraint permitting `signal`, `control`, and `benchmark`, per ADR 105.

`v_screen` gains both predicates in the same rebuild:

```sql
AND c.config_hash = current_setting('capitalscan.default_config_hash', true)
AND c.arm = 'signal'
```

The `config_hash` predicate is ADR 100's correction. Without it, the first Phase 4 run writing two configs duplicates every screener row, and the composite key ADR 096 introduced is what makes that possible.

Acceptance:

- Migration applies to a fresh database and to a copy of the research database, and reverses with no orphaned objects.
- `db/schema.sql` regenerated and committed. `test_schema_drift.py` must run, not skip; a stopped Docker container turns that guard off silently.
- A test asserting `v_screen` returns exactly one row per event when two configs hold `cell_stats` rows. A test under a single config passes today and passes again after the defect returns.
- A test asserting `v_screen` returns nothing for a `control` or `benchmark` row. Asserting only that `signal` rows appear would pass on a view with no predicate at all.
- `test_holdout_firewall.py` still passes.

### 12.6 Tests and documentation

Acceptance:

- `TESTS.md` gains the Session 12 inventory: `cell_key` parity, grid enumeration, suppression, `cell_stats` column completeness, BH family construction, era exclusion, breadth denominator, `v_screen` predicates.
- `RESULTS.md` gains a Session 12 section recording all twelve cells with their measured `n_eff`, `p_hit`, both baselines, edge, interval, and q-value; the two suppressed cells with their reasons; the re-measured breadth terciles; and the concentration findings.
- `DESIGN.md` §6.7 rewritten to the ADR 102 grid. §6.11 amended per ADR 103.
- `BUILD.md` lists Session 12 and its gate outcome.

---

## 4. Session gate

1. Twelve cells enumerated, ten rendered, two suppressed with reasons matching the ADR 102 prediction.
2. Every rendered cell carries `n_eff`, a Wilson interval, both baselines, and a q-value. No bare percentages anywhere.
3. Measured `n_eff` reproduces ADR 102's table within tolerance.
4. `cell_key` parity holds against the executed Postgres function across all coalescing paths.
5. Benjamini-Hochberg runs over 48 tests, q-values monotone, `q >= p` throughout.
6. Era rows exist for three eras and none for 2024+.
7. Breadth terciles computed under the train-universe denominator and recorded.
8. `v_screen` returns one row per event under two configs and nothing for non-signal arms.
9. `test_holdout_firewall.py` and `test_schema_drift.py` both pass, the latter having run rather than skipped.
10. Determinism: two runs, identical output ignoring run identifiers.

Items 1 and 3 are the ones to watch. If the measured `n_eff` disagrees with ADR 102's table, either the writer is wrong or the grid design rests on a miscalculation, and both need resolving before Session 13.

---

## 5. What will be tempting and should not be done

**Lowering `min_n_eff` to rescue the two suppressed cells.** They suppress at 14 and 5 against a floor of 30. A floor that moves for inconvenient cells is not a floor, and both ADR 101 and ADR 103 already rejected this reasoning once.

**Reading a short cell's `p_hit` as a trade signal.** ADR 106 made shorts a measured signal population, not a recommendation. ADR 105's `arm` column and `v_screen`'s predicate are the mechanism, and neither substitutes for the reader understanding what the number is.

**Treating a strong pooled result as settled.** The breadth split exists because a pooled edge concentrated on high-breadth days is market timing wearing a stock-selection label. Session 13's three-arm comparison on the high-breadth subset is what answers it, and a good pooled number is exactly the situation where nobody asks.

---

## 6. What Session 13 needs from this one

| Session 13 needs | From |
|---|---|
| Twelve populated `cell_stats` rows keyed `(cell_id, config_hash)` | 12.3 |
| `arm` column with a working check constraint | 12.5 |
| Breadth terciles under the ADR 104 denominator | 12.4 |
| The high-breadth subset definition for the three-arm comparison | 12.4 |
| Per-ticker concentration flags | 12.4 |

Session 13 builds buy-and-hold, signal, and random-entry arms across 200 replications, plus trim-and-redeploy per ADR 017 and the DCA variants. ADR 106 changed one thing there: ADR 017's expected ranking placed trim-and-redeploy above regime-filtered short above naive short, and the middle term no longer exists. The comparison becomes trim-and-redeploy against unfiltered short.

---

## 7. Rollback

- One additive column with a safe default.
- One view rebuilt, reversible.
- `cell_stats` and its era and breadth rows can be deleted by `config_hash`.
- No existing table's data is modified.
- No consumer outside `v_screen` reads anything this session writes until Session 13.
