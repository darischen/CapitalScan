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

### Data quality updates — 2026-08-03

**BNY hourly bars pruned.**

```sql
SELECT count(*) FROM bars_bny_hourly_bad_20260803;
SELECT interval, count(*) FROM bars WHERE ticker = 'BNY' GROUP BY interval;
SELECT max(high)/min(low) AS daily_range_ratio FROM bars WHERE ticker = 'BNY' AND interval = '1d';
```

2,888 hourly rows deleted and backed up to `bars_bny_hourly_bad_20260803` (row
count confirmed against the live table). `bars` now holds zero hourly rows
and 5,233 daily rows for BNY, both confirmed by direct count. The feed
showed a flat $9.30-11.10 band for roughly 18 months against a ~$95 daily
price, with a continuously drifting mismatch that ruled out a missing
split, then a hard jump to match daily on 2026-05-22. Diagnosed as a
wrong-instrument or stale-feed defect on the vendor's side. Symbol reuse
was investigated and refuted: BNY's daily high/low range ratio measures
10.6069x, a normal single-instrument range with no discontinuity. No fix
available in this codebase. BNY's 5,233 daily bars are intact and sound
(confirmed above; separately, the default-config sweep cell shows BNY
contributes 0 priced hourly-entry-kind events, see below).

**Double-adjustment defect found and fixed (`d33aaa5`).**

The original hourly split back-adjustment assumed Yahoo never adjusts
hourly bars. In fact Yahoo pre-adjusts a window of 1-41 sessions
immediately before each ex-date, and the code divided those bars again,
corrupting them. Verified on ANET: split ex_date 2024-12-04, the reject
window runs 2024-10-07 through 2024-12-03 (41 sessions, source:
`.superpowers/sdd/2026-08-01-session-9-backtest/hourly-residual-diagnosis.md`)
— a contiguous block ending exactly one day before the split, with
discrepancies landing at exact split multiples: ANET `99.87 / 24.9675 =
4.0004`, NFLX `117.914 / 11.79 = 10.0012`.

**Residual hourly/daily mismatch after the repair refetch, measured
directly.** A day-gap check (day present in `bars` daily but absent from
hourly) was tried first and produced spurious results — see the note at
the end of this subsection. The metric that actually finds the defect
described here is a **value mismatch**: for each ticker-day, compare that
day's hourly `max(high)` to the daily bar's `high`. A large ratio means
the hourly bars for that day are still on the wrong scale even after the
refetch.

```sql
WITH h AS (SELECT ticker, ts::date d, max(high) hhi FROM bars
           WHERE interval='1h' GROUP BY 1,2)
SELECT h.ticker, count(*) AS bad_days FROM h
JOIN bars b ON b.ticker=h.ticker AND b.ts::date=h.d AND b.interval='1d'
WHERE h.hhi/b.high > 1.5 OR h.hhi/b.high < 0.667
GROUP BY 1 ORDER BY 2 DESC;
```

| Ticker | Bad days |
|---|---|
| DD | 22 |
| PANW | 7 |
| SBNY | 2 |
| CVNA | 1 |
| ANET | 1 |
| CRWD | 1 |
| TSCO | 1 |
| NOW | 1 |
| BKNG | 1 |
| FAST | 1 |
| AMCR | 1 |
| ETR | 1 |
| TPL | 1 |
| NFLX | 1 |
| KLAC | 1 |
| ORLY | 1 |
| IBKR | 1 |

17 tickers, 45 bad days total. This is the same 17-ticker group named
under "Known open data-quality items" in the Backfill record above
(`KLAC`, `BKNG`, `CRWD`, `TPL`, `AMCR`, `CVNA`, `NOW`, and others), minus
`BNY` (its hourly table is now empty, so it cannot appear in this query)
and with `PANW` and `SBNY` now named explicitly. **DD carries 22 bad
days, the worst of the 17** — the repair refetch did not close DD's
mismatch.

*A day-gap query was run first and reported here in error, then
withdrawn.* It compared, per currently-hourly-tracked ticker, which of
that ticker's own daily-bar trading days had no matching hourly day at
all, and returned only `INFO` (32) and `NFX` (7), with `DD` at zero. Those
two tickers and their counts do not reproduce under the value-mismatch
query above and were never independently traced to a cause — most likely
the day-gap query's boundary handling (`min`/`max` of each ticker's own
sparse hourly range) manufactured false gaps at tickers with unusual
history rather than finding the real defect, which is corrupted values on
present days, not missing days. That query and its numbers are not
restated as findings.

**Downstream impact on priced hourly entries**, measured directly against
the live `events` table, all `entry_kind` values and all 18 configs (no
filter beyond ticker):

```sql
SELECT ticker, count(*) AS all_configs,
       count(*) FILTER (WHERE entry_kind IN ('touch_5m','touch_30m')
                        AND entry_price IS NOT NULL) AS hourly_priced
FROM events WHERE ticker IN ('BNY','DD','ANET','IBKR') GROUP BY 1;
```

| Ticker | Event rows (all entry kinds, all configs) | Hourly-priced |
|---|---|---|
| BNY | 2,376 | 0 |
| DD | 4,824 | 0 |
| ANET | 7,416 | 2,196 |
| IBKR | 8,640 | 2,124 |

Zero priced hourly entries for BNY and DD confirmed. ANET and IBKR are
nonzero, consistent with the brief's claim that they are the two affected
tickers that clear `in_trade`. **Correction:** an earlier pass through
this file reported 1,188 / 2,412 event rows for BNY / DD — exactly half
the true counts above, because that query filtered to `entry_kind IN
('touch_5m','touch_30m')` in the row-count column as well as the priced
column. Each ticker carries four entry kinds (`next_open`, `touch`,
`touch_5m`, `touch_30m`) at equal counts per ticker, so restricting the
row count to the two hourly kinds happened to land on exactly half the
true total — a halving error that looked plausible and was not caught
before writing. The priced column was already correct in that pass, since
it was always meant to filter to the hourly kinds.

**`runs.git_sha = 'unknown'`.**

```sql
SELECT count(*) FROM runs WHERE git_sha = 'unknown';
```

27 rows carry `git_sha = 'unknown'`, fixed by commit `528ca90`. This
includes the original ADR 059 default-config run
(`backtest_20260802T183304_6b1c5b52`) and every ingest/indicator/universe
job run from 2026-08-01 through the morning of 2026-08-02. Historical rows
were not backfilled — those runs genuinely happened under unknown
provenance and rewriting the column would fabricate a record. All 18
sweep runs (`backtest_sweep_*`, 2026-08-03) carry a real `git_sha`
(`aacee77d827f9953f3193faaabcbd793798028f2`), confirming the fix holds for
current work.

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

### ADR 059 hand-inspection

**Gate condition met, 2026-08-03, attributed to the user.**

Per ADR 059 / BUILD §9.9, roughly 20 events from the default-config run
were inspected by hand against charts before the sweep executed. The user
performed this inspection and reported the events looked correct. This
record states what was done and by whom; it is not independently
re-verified here and does not itself constitute a statistical claim about
the engine's correctness beyond the inspected sample.

### Exit config sweep

**2026-08-03.** 18 runs, `job = 'backtest_sweep'` in `runs`, each `status =
'ok'`, git_sha `aacee77d827f9953f3193faaabcbd793798028f2`.

**Config and row counts**

```sql
SELECT count(DISTINCT config_hash) AS n_configs, count(*) AS total_rows FROM events;
SELECT config_hash, count(*) AS rows FROM events GROUP BY config_hash ORDER BY rows DESC;
```

18 distinct `config_hash` values in `events`, 4,430,088 total rows. Every
config produces exactly **246,116 rows** — perfectly uniform, no config
resolves materially fewer events than its siblings.

```sql
SELECT count(*) FROM events WHERE config_hash = '3e598c59e7d71eae';
```

The default config `3e598c59e7d71eae` is confirmed as one of the 18. Its
246,116 rows in `events` now belong to run_id
`backtest_sweep_20260803T021428_10b5860b` (the sweep's own pass over that
config), not to the original ADR 059 run:

```sql
SELECT run_id, count(*) FROM events WHERE config_hash='3e598c59e7d71eae' GROUP BY run_id;
SELECT count(*) FROM events WHERE run_id='backtest_20260802T183304_6b1c5b52';
SELECT count(DISTINCT run_id) FROM events WHERE config_hash='3e598c59e7d71eae';
```

The original run_id `backtest_20260802T183304_6b1c5b52` (cited in "Default
config run" above) now has **zero** rows in `events`, and `config_hash =
'3e598c59e7d71eae'` has exactly **one** distinct `run_id` in `events`
today (`backtest_sweep_20260803T021428_10b5860b`) — its rows were
overwritten when the sweep wrote its own default-config cell under the
same natural key. **Consequence:** the run that passed the Phase 3
validation harness no longer owns the rows it validated. The rows
themselves are expected to be byte-identical, since `config_hash` plus
identical input data is the whole determinism contract (ADR 060,
confirmed separately by the "two runs identical ignoring `run_id`" gate
check above) — but the specific `run_id` the harness ran against is gone,
so a future reader tracing provenance by `run_id` for this config must
use the sweep's run_id, not the one in "Default config run" above.

**Wall-clock time**

```sql
SELECT min(started_at) AS sweep_start, max(finished_at) AS sweep_end,
       max(finished_at)-min(started_at) AS wall_clock, count(*) AS n_runs,
       avg(finished_at-started_at) AS avg_per_config
FROM runs WHERE run_id IN (SELECT DISTINCT run_id FROM events);
```

18 runs recorded individually in `runs`, one per config. Total wall clock
2026-08-03 00:58:22 to 06:58:24 = **6h00m01s**, averaging 20m00s per
config. This is far longer than DESIGN §5.9's "~4 minutes total" estimate
for the sweep (the per-config-write-once-entries-reused optimization); the
per-config time here is close to the single-threaded validation-harness
cost noted for the default run, suggesting the harness runs per sweep cell
rather than once over the union. Not diagnosed further here — a Phase-4-
adjacent engineering question, not a correctness one.

**Config axis breakdown**

The 18 configs are not a full `3 stop_mode × 4 stop_atr_k × 3 target_pct`
cross (which would be 36 or, restricted to `stop_atr_k` only mattering
under `stop_mode='atr'`, 12+3+3=18 — confirmed against `runs.params`):

```sql
WITH cfg AS (
  SELECT DISTINCT
    params->'config'->'exits'->>'stop_mode' AS stop_mode,
    (params->'config'->'exits'->>'stop_atr_k')::numeric AS stop_atr_k,
    (params->'config'->'exits'->>'target_pct')::numeric AS target_pct,
    params->>'config_hash' AS config_hash
  FROM runs WHERE job='backtest_sweep'
)
SELECT count(*) AS n_distinct_combos, count(DISTINCT config_hash) AS n_hashes FROM cfg;
```

18 distinct combos, 18 hashes — one config per hash, no collision.
`stop_mode='atr'` varies `stop_atr_k` over `{1.0, 1.5, 2.0, 2.5}` (12
configs); `stop_mode='fixed'` and `stop_mode='none'` each hold
`stop_atr_k` at 1.5 and vary only `target_pct` (3 configs each).

**Priced / exited / ambiguous, per config**

```sql
SELECT config_hash,
       count(*) FILTER (WHERE entry_price IS NOT NULL) AS priced,
       count(*) FILTER (WHERE exit_date IS NOT NULL) AS exited,
       count(*) FILTER (WHERE ambiguous) AS ambiguous
FROM events GROUP BY config_hash ORDER BY config_hash;
```

`priced` is uniform at 110,954 across all 18 configs (entry prices compute
once and are reused, per DESIGN §5.9 — confirmed). `exited` is uniform at
110,894 across all 18 configs. `ambiguous` varies by config, from 0 (the
three `stop_mode='none'` configs) to 318 (`fixed`, `stop_atr_k=1.5`,
`target_pct=0.03`); the default config (`3e598c59e7d71eae`) shows 28,
matching the sum of its three split-level ambiguous counts (15+0+13=28)
recorded under "Default config run" above.

**`exit_reason` distribution and mean `net_ret`, per config**

```sql
WITH cfg AS (
  SELECT DISTINCT
    params->'config'->'exits'->>'stop_mode' AS stop_mode,
    (params->'config'->'exits'->>'stop_atr_k')::numeric AS stop_atr_k,
    (params->'config'->'exits'->>'target_pct')::numeric AS target_pct,
    params->>'config_hash' AS config_hash
  FROM runs WHERE job='backtest_sweep'
)
SELECT c.stop_mode, c.stop_atr_k, c.target_pct,
       count(*) FILTER (WHERE e.exit_date IS NOT NULL) AS n_exited,
       round(avg(e.net_ret)::numeric,6) AS mean_net_ret
FROM cfg c JOIN events e ON e.config_hash=c.config_hash
GROUP BY 1,2,3 ORDER BY c.stop_mode, c.stop_atr_k, c.target_pct;
```

| stop_mode | stop_atr_k | target_pct | n_exited | mean `net_ret` |
|---|---|---|---|---|
| atr | 1.0 | 0.03 | 110,894 | -0.000841 |
| atr | 1.0 | 0.04 | 110,894 | -0.000947 |
| atr | 1.0 | 0.05 | 110,894 | -0.001032 |
| atr | 1.5 | 0.03 | 110,894 | -0.000813 |
| atr | 1.5 | 0.04 | 110,894 | -0.000965 |
| atr | 1.5 | 0.05 | 110,894 | -0.001040 |
| atr | 2.0 | 0.03 | 110,894 | -0.000709 |
| atr | 2.0 | 0.04 | 110,894 | -0.000870 |
| atr | 2.0 | 0.05 | 110,894 | -0.000923 |
| atr | 2.5 | 0.03 | 110,894 | -0.000630 |
| atr | 2.5 | 0.04 | 110,894 | -0.000808 |
| atr | 2.5 | 0.05 | 110,894 | -0.000860 |
| fixed | 1.5 | 0.03 | 110,894 | -0.001144 |
| fixed | 1.5 | 0.04 | 110,894 | -0.001217 |
| fixed | 1.5 | 0.05 | 110,894 | -0.001283 |
| none | 1.5 | 0.03 | 110,894 | -0.000212 |
| none | 1.5 | 0.04 | 110,894 | -0.000360 |
| none | 1.5 | 0.05 | 110,894 | -0.000392 |

`n_exited` is identical across every config: total priced-and-entered
events is fixed by entry resolution (independent of exit config), and
every entered event resolves to some `exit_reason` (`target`, `stop`,
`stoch_80`, `upper_band`, or `timeout`), so the exited count never varies
by exit policy.

```sql
WITH cfg AS (
  SELECT DISTINCT
    params->'config'->'exits'->>'stop_mode' AS stop_mode,
    (params->'config'->'exits'->>'stop_atr_k')::numeric AS stop_atr_k,
    (params->'config'->'exits'->>'target_pct')::numeric AS target_pct,
    params->>'config_hash' AS config_hash
  FROM runs WHERE job='backtest_sweep'
)
SELECT c.stop_mode, c.stop_atr_k, c.target_pct, e.exit_reason, count(*) AS n
FROM cfg c JOIN events e ON e.config_hash=c.config_hash
WHERE e.exit_date IS NOT NULL
GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;
```

87 rows (15 configs × 5 reasons + 3 `stop_mode='none'` configs × 4
reasons, since `none` never emits `exit_reason='stop'`). Full table
omitted here for length; retained in
`.superpowers/sdd/2026-08-01-session-9-backtest/results-sweep-report.md`.
Qualitative shape, reading straight off the numbers with no
interpretation: raising `stop_atr_k` (looser stop, `atr` mode) shifts
mass from `stop` toward `timeout`; raising `target_pct` shifts mass from
`target` toward `timeout`; `stop_mode='none'` has no `stop` exits by
construction and the largest `timeout` share of the three modes at every
`target_pct`. These are descriptions of the exit-reason counts, not a
ranking of configs — that judgment belongs to Phase 4.

### Phase 3 gate — sweep closes the last outstanding item

The five acceptance criteria in BUILD §9 passed against the default
config and are recorded unchanged under "Default config run" above. BUILD
§9.10 (the 18-config sweep) was the one remaining item in the Session 9
task list and is now complete: 18 runs recorded in `runs`, 18 distinct
`config_hash` values in `events`, 4,430,088 rows, uniform 246,116 rows per
config, confirmed above. Combined with the ADR 059 hand-inspection above,
Session 9 has no outstanding BUILD §9 items.

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
