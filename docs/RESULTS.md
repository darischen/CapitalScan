# RESULTS.md

Every result gets recorded here with its `run_id`, **including null results** (ADR 033).

This file exists so that a null result is a recorded finding rather than a conversation that never happens. A project reporting "I tested this rigorously and found no edge, here is the infrastructure that proved it" is stronger than a suspiciously profitable backtest.

**Rules for this file**

- Append, never edit a past entry. Corrections go in a new entry referencing the old one.
- Every quantitative claim cites a `run_id`.
- Every reported rate carries `n_eff` and a confidence interval.
- Negative and inconclusive results are recorded with the same detail as positive ones.
- Holdout is evaluated **once**, at the end, and the result is published whatever it says.

---

## Template

```markdown
### <YYYY-MM-DD> — <short title>

- run_id:       <run_id>
- git_sha:      <sha>
- config_hash:  <hash>
- split:        train | validate | holdout
- universe:     <n tickers>, <date range>

**Question**

<what this run was meant to answer, in one sentence>

**Method**

<config in one paragraph: signal, entry kind, exits, target, stop>

**Result**

| Cell | n | n_eff | p_hit | baseline | edge | CI | q |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

**Benchmarks**

| Arm | Total ret | Sharpe | Max DD | Deployed | Cap. eff. |
|---|---|---|---|---|---|
| Buy and hold | | | | 100% | |
| Random entry (200 reps, mean) | | | | | |
| Signal entry | | | | | |

**Interpretation**

<what this does and does not support>

**Follow-up**

<what to run next, or none>
```

---

## Phase 1 — Data and detection

### Backfill record

**2026-08-02, measured against the live research database, HEAD `455d64b`.**

The 51-ticker Session 7 dry run above never became the production backfill.
The state below supersedes it — every figure is a `SELECT` against the
research database, shown alongside its query so it can be re-run.

**Ticker registry**

```sql
SELECT count(*) FROM tickers;
```

711 tickers registered (`data/universe_union.csv`, ADR 055).

**Bars ingested**

```sql
SELECT interval, count(*) AS bars, count(DISTINCT ticker) AS tickers,
       min(ts), max(ts)
FROM bars GROUP BY interval ORDER BY interval;
```

| Interval | Bars | Tickers | Range |
|---|---|---|---|
| `1d` | 2,900,865 | 615 | 2005-10-11 .. 2026-07-31 |
| `1h` | 2,069,250 | 605 | 2024-08-06 .. 2026-07-31 |

615 of the 711 registered tickers have any daily bars at all.

**`bar_rejects`, by rule and severity**

```sql
SELECT rule, severity, count(*) FROM bar_rejects
GROUP BY rule, severity ORDER BY count(*) DESC;
```

| Rule | Severity | Count |
|---|---|---|
| `price_below_min` | flag | 30,493 |
| `zero_or_null_volume` | flag | 18,565 |
| `identical_close_run` | flag | 9,894 |
| `large_unexplained_return` | flag | 526 |
| `insufficient_history` | flag | 22 |
| `unexplained_split_like_move` | reject | 11 |
| `large_return_explained_by_split` | flag | 3 |
| `open_outside_range` | reject | 1 |

59,515 rows total. Only two rules ever reach `reject` severity (12 rows
combined); everything else is a `flag`, logged per invariant 4 rather than
silently dropped or filled.

**Coverage gaps — dropped tickers**

```sql
SELECT is_active, delisted_on IS NOT NULL AS has_delisted_date,
       first_bar IS NOT NULL AS has_first_bar, count(*)
FROM tickers t
WHERE NOT EXISTS (SELECT 1 FROM bars b WHERE b.ticker = t.ticker)
GROUP BY 1, 2, 3 ORDER BY 4 DESC;
```

96 registered tickers have zero rows in `bars`. All 96 are `is_active = false`
(delisted/acquired historical constituents — e.g. `ABMD`, `ADS`, `AKS`,
`ALXN`, `ANR`, `ANSS`, `ARG`, `ARNC`, `ATVI`, `AYE`, `BXLT`, `CCR`, `CDAY`,
`CERN`, `CMCSK`, `COG`, `COV`, `CPGX`, `CTLT`, `CVC`, `CXO`, `DAY`, `DFS`,
`DISCK`, `DISH`, ...). None has a `delisted_on` date recorded, so the reason
they were never fetched is not stored anywhere — worth a follow-up rule if a
future backfill needs to distinguish "delisted before the data source's
coverage" from "fetch never attempted."

19 of the 96 have a `first_bar`/`last_bar` populated on `tickers` (e.g.
`CPWR`: `first_bar=2009-01-02`, `last_bar=2026-07-30`) despite having no rows
in `bars` — those two columns on `tickers` are stale relative to the actual
`bars` content for this subset and should not be trusted as a coverage proxy
on their own.

**Known open data-quality items, not corrected as part of this documentation
pass:**

- 17 tickers (`KLAC`, `BKNG`, `CRWD`, `TPL`, `AMCR`, `BNY`, `CVNA`, `NOW`, and
  others) carry hourly bars on a different split-adjustment basis than their
  daily bars — exact split-factor ratios (e.g. BKNG ~25:1, KLAC ~10:1). A
  fix to reject the mismatch at ingestion is committed; the repair refetch
  for the 17 affected tickers' hourly history has not run yet.
- `BNY` has no split row in `corporate_actions`, so back-adjustment cannot
  repair its hourly/daily mismatch even after a refetch.
- Two `BRK-B` shares filings (2009-2010, ~1.1B shares) look like Class A
  share counts filed under the Class B ticker — implausible against BRK-B's
  contemporaneous price, and not caught by the shares-outstanding
  plausibility guard because the values sit inside its bounds.

### Indicator verification

*(Append after the external reference check. Five dates × two tickers, computed vs external, max deviation.)*

---

## Phase 3 — Engine validation

### Default config run

**2026-08-02.** `run_id=backtest_20260802T183304_6b1c5b52`,
`config_hash=3e598c59e7d71eae`, `git_sha` on the `runs` row is `unknown`
(not populated by this job — a gap, not a data error), branch
`session-9-backtest`, HEAD `455d64b`.

246,116 event rows written, 575 of 615 ticker-with-bars tickers producing at
least one event, 2h48m17s wall clock at 8 workers (write phase ~20 min,
single-threaded validation harness ~2h28m — the harness, not the writer, is
the bottleneck).

```sql
SELECT split_key, count(*) AS rows, count(DISTINCT ticker) AS tickers,
       count(*) FILTER (WHERE entry_price IS NOT NULL) AS priced,
       count(*) FILTER (WHERE exit_date IS NOT NULL) AS exited,
       count(*) FILTER (WHERE ambiguous) AS ambiguous,
       min(signal_date), max(signal_date)
FROM events WHERE config_hash = '3e598c59e7d71eae'
GROUP BY split_key ORDER BY split_key;
```

| `split_key` | rows | tickers | priced | exited | ambiguous | range |
|---|---|---|---|---|---|---|
| `train` | 156,848 | 564 | 61,535 | 61,535 | 15 | 2010-01-05 .. 2021-12-31 |
| `validate` | 21,672 | 69 | 8,418 | 8,418 | 0 | 2022-01-03 .. 2023-12-29 |
| `holdout` | 67,596 | 124 | 41,001 | 40,941 | 13 | 2024-01-02 .. 2026-07-31 |

**`validate` holds far fewer tickers than `train` or `holdout` (69 vs 564 and
124).** Likely cause: the trade-universe filter (ADR 014, `crit_above_sma200`
and `crit_sma200_slope` in particular) meeting the 2022-2023 drawdown —
those two criteria would fail broadly across a down market, thinning the
in-trade population for exactly the years `validate` covers. Not diagnosed
further here. It weakens the train-vs-validate comparison ADR 033's kill
criteria depend on, since 69 tickers is a materially smaller base than the
other two splits.

**Phase 3 gate — all five criteria PASS.**

| Criterion | Result |
|---|---|
| Exit invariants, 10,000 property cases | PASS — `full` profile, 5/5 tests, `capitalscan/tests/property/test_exit_invariants.py` |
| Ambiguity rate < 10% | PASS — 28 / 110,954 priced rows = 0.025% |
| Event rate, BUILD §9a three checks | PASS — confluence 18.34% all-ticker, 19.07% in-trade-only, inside the 10-25% headline band; see BUILD §9a for the check definitions |
| Two runs identical ignoring `run_id` | PASS — confirmed twice independently (in-process, monkeypatched `db_io.upsert`, zero DB writes), zero differing cells across 22,168 rows / 62 columns |
| All five validation-harness checks | PASS — `no_lookahead`, `entry_sanity`, `exit_sanity`, `return_identity`, `non_overlap`, all against the `3e598c59e7d71eae` run |

Full measurement detail, including the queries behind the event-rate check
and the harness-gate debugging history, is in
`.superpowers/sdd/2026-08-01-session-9-backtest/phase3-gate-measurement.md`
and `progress.md` in the same directory.

**Caveats on record, not blocking the gate:**

- The event-rate measurement flagged a +2.2-2.7pp drift on the raw
  band-touch marginals (`P(close <= bb_lower)`, `P(low <= bb_lower)`)
  against the 2026-08-01 baseline — real and moderate-sized (20-50%
  relative), unexplained after a bounded investigation, and not reproduced
  in the confluence/headline composites. See phase3-gate-measurement.md §2
  for what was ruled out.
- The 17-ticker hourly/daily split-adjustment mismatch above currently
  affects zero priced `touch_5m`/`touch_30m` rows in this run — by
  coincidence, not by a guard that prevents it. A universe-threshold change
  or a new signal date on one of those tickers could make it live.
- `BNY` cannot be back-adjusted (no split row) even once the refetch runs.
- The two `BRK-B` filings noted under coverage gaps above feed
  `crit_mcap`/`in_trade` for those two years if uncorrected.

### Entry timing sweep

*(Touch vs next-open on the full window. Touch+5m vs touch+30m on the hourly subset only, with the coverage limitation stated.)*

---

## Phase 4 — Statistics

### Statistical self-validation

*(Random-walk null test and known-drift recovery test. These must pass before any real result below is trusted.)*

### Baseline table

*(Per ticker-year, empirical and parametric, with disagreement flags.)*

### Headline grid

*(12 cells, all targets, validate split, with BH correction.)*

### Three-arm comparison

*(Equity curves and summary table.)*

### DCA comparison

*(Fixed schedule vs signal-triggered vs hybrid vs lump sum.)*

### Drawdown slice

*(Edge by drawdown bucket. ADR 015 calls this the project's central claim.)*

### Era breakdown

*(Same cells across 2010-14, 2015-19, 2020-23, 2024-26. A cell appearing in one era only is a regime artifact regardless of pooled significance.)*

### Cluster sequence

*(Does `seq_in_cluster = 2` outperform `seq_in_cluster = 1`? This is the averaging-down question, answered with a number.)*

### Long vs short asymmetry

*(ADR 016's band-walking hypothesis, tested before any short is recommended.)*

---

## Phase 6 — Model

### Calibration

*(Reliability diagrams, Brier, ECE, coverage per quantile, pinball loss. Against the cell-lookup baseline.)*

### Promotion history

*(One entry per model version: what changed, gate results, promoted or held.)*

### Forward log

*(Rolling 90-day calibration. Model vs cell lookup vs reality, out of sample. This is the artifact most projects cannot show.)*

---

## Holdout

**Evaluated once. Published whatever it says.**

*(Empty until the end. Do not look.)*

---

## Kill criteria status

| Criterion | Threshold | Status |
|---|---|---|
| No cell beats baseline at sufficient `n_eff` after FDR | — | Not yet evaluated |
| Validation edge under half of training edge | — | Not yet evaluated |
| Holdout edge negative | — | Not yet evaluated |

If any fires, record it here with the same detail as a positive result, then pivot per ADR 033: keep the engine, swap the input signal. Volatility term structure, earnings drift, cross-sectional momentum residuals, and volume-price divergence are all testable in the same framework with no rewrite.
