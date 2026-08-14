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

### Phase 1 gate — 2 of 4 criteria PASS, 1 does not reproduce, 1 not implemented

**Measured 2026-08-06** against the live research database. Written for the
first time on that date: Phase 1 was asserted passed in `BUILD.md` (Session 6)
with no gate table behind it, and this table is what that assertion looks like
when checked. Criteria are `TESTS.md` §10, Phase 1, verbatim.

| Criterion | Result | Evidence |
|---|---|---|
| `cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30` returns the 2026-07-29 event with correct %B and %K | **DOES NOT REPRODUCE** — see below | Command prints `no events found`. The event itself is correct in `events`: `confluence_low`, `bb_pctb = 0.110081`, `k_full = 21.991916` |
| All golden fixtures pass | PASS | `uv run pytest capitalscan/tests/golden` — 3 passed, 1 skipped (`test_external_reference.py`, awaiting the hand-filled CSV per ADR 086) |
| Zero nulls in indicators after 2010-01-01 | PASS as qualified by ADR 040 | 929 null-bearing rows past the 272-bar warmup out of 2,380,441; all in AMCR and SW, all frozen-price runs |
| Random-walk null test passes | PASS at the label layer; cell layer awaits Phase 4 | `capitalscan/tests/unit/test_random_walk_null.py`, 9 tests, written 2026-08-06. Numbers below |

**Why the scan gate does not reproduce.** `compute.scan` filters on the
`capitalscan.default_config_hash` GUC (the same one `v_events` reads —
invariant 5b forbids a second config-selection mechanism). The GUC is set at
the database level to `1835688bf7d760ba`, correctly: that is
`config_hash(Config())` for today's defaults, pinned by
`test_default_config_hash_is_pinned`.

The gap is history, not wiring. The default config changed on 2026-08-05 —
`UniverseParams.min_mcap_usd` 100e9 → 30e9 and the new
`SignalParams.stoch_source` field — which moved the default hash from
`3e598c59e7d71eae` to `1835688bf7d760ba`. No backtest has run under the new
hash, so `1835688bf7d760ba` holds only what the live `events` and `poll` jobs
have written since: **109 events, 2026-07-31 to 2026-08-06**. Everything
older, including the entire Phase 3 run of record, sits under the previous
hash and is invisible to `cscan scan`.

So the criterion is unmet for a mundane reason (no backtest since the config
moved), not a detection defect. Two consequences worth stating:

**`3e598c59e7d71eae` is no longer reachable from any config.** Reverting
`min_mcap_usd` to 100e9 yields `a6c54c878368cd29`, not the old hash, because
`stoch_source` is now part of the hashed dataclass shape and cannot be removed
without deleting the field. Any plan that says "re-run the backtest for
`config_hash=3e598c59e7d71eae`" cannot be executed as written — a full backtest
today writes `1835688bf7d760ba`. The Phase 3 run of record is frozen evidence
from here on, not a target you can append to.

**`cscan scan` recovers as soon as a full backtest runs under the current
config**, which is also what the Phase 4 statistics need. No code change is
required for this criterion; a run is.

**Resolved 2026-08-08 (measured 2026-08-09).** The run this section was
waiting for happened: `backtest_20260808T123636_22fdc650` wrote **621,976
events under `1835688bf7d760ba`, spanning 2010-01-05 to 2026-08-07**, across
590 tickers. The "109 events, 2026-07-31 to 2026-08-06" figure above is
superseded and was already stale when read on 2026-08-09; it is left in place
per this file's own no-deletion convention. The GUC is on
`1835688bf7d760ba`, that hash now holds a full-history event set, and
`cscan scan` serves it. **Criterion met.**

**ADR 097's brief interruption, recorded so the residue is explainable.**
ADR 097 (2026-08-08) added `SplitParams.max_lookback_days`, moving the
default hash to `4630b12a84ff52de`; the nightly chain picked it up the same
day and `events_20260809T173928_c19cc671` wrote 299 rows under it. The ADR
was reverted on 2026-08-09 once its 756-day window was found to floor at
2024-07-14, behind every split boundary, producing holdout-only event sets
(all 299 of those rows carry `split_key = 'holdout'`). See ADR 097 for the
full reasoning. The default hash is back to `1835688bf7d760ba`, no GUC
change was ever applied, and no backtest ever completed under the withdrawn
config.

Residue: those 299 events under `4630b12a84ff52de` are the only rows under a
hash no current config can produce. They serve nothing and are safe to
delete.

**What `runs.duration` measures for a backtest, and what it does not
(2026-08-09).** `runs` rows for `job='backtest'` sit at 20-38 min for a
full-universe run (`backtest_20260808T123636_22fdc650` 35m52s,
`backtest_20260807T041712_4e7e5de8` 38m01s, `backtest_20260802T183304_6b1c5b52`
19m34s). **Those are write-phase durations, not job durations.**
`cli.py::backtest` closes its `with ingest.run_job(...)` block before calling
`run_harness`, so the Phase 3 harness runs entirely outside the timed region
and is recorded nowhere.

This reconciles with CLAUDE.md's 2h48m rather than contradicting it: ~20-38 min
of write phase plus the ~2h28m single-threaded harness. A session on 2026-08-09
read these durations as whole-job timings, told the user the backtest takes ~36
minutes, and edited CLAUDE.md accordingly. Both were wrong and are reverted.
The lesson is narrow and worth keeping: `runs` bounds the job's *instrumented*
region, and for `backtest` that region excludes the most expensive step.

`cscan weekly` is the exception that makes the confusion easy — it calls
`run_backtest` and deliberately skips the harness (`cli.py::weekly`), so its
~36 min really is the whole job. `backtest_20260808T123636_22fdc650` carries
`params->>'trigger' = 'weekly'`, which is how to tell the two apart.

**Random-walk null and recovery, measured 2026-08-06.** 50 synthetic tickers,
2,500 days, driftless in log space, σ = 30% annual, run through the real
`core.returns.path_for_event` and `research.path_queries` code. 6,250
non-overlapping 10-day windows.

| Check | Analytical | Measured | Verdict |
|---|---|---|---|
| Reachability symmetry, log-symmetric barriers (+5% / −4.7619%) | 0 | 0.11pp gap | PASS |
| Reachability vs continuous-monitoring bound | ≤ 0.4143 | 0.3240 up, 0.3251 down | PASS (one-sided) |
| P(terminal > 0), no drift | 0.5000 | 0.4986 | PASS, 0.14pp |
| P(terminal > 0), μ = 30% annual drift injected | 0.5789 | 0.5813 | PASS, 0.23pp (gate is 1pp) |

**The naive version of this test was wrong and would have passed.** The first
draft asserted `P(+5%) == P(−5%)` and measured a 1.79pp gap, which a 2.5pp
tolerance swallowed. That gap is real arithmetic, not a defect: a driftless
walk is symmetric in *log* price, and `log(1.05) = 0.04879` while
`|log(0.95)| = 0.05129`, so the −5% barrier sits 5% farther away and is
genuinely touched less often. Comparing log-symmetric barriers instead drops
the gap from 1.79pp to 0.11pp and lets the tolerance tighten from 2.5pp to
1.5pp — so the whole budget now covers real bugs rather than a known effect.
`test_percent_symmetric_barriers_are_biased_by_the_amount_theory_predicts`
pins the trap so it cannot be reintroduced.

**What is still missing.** DESIGN §6.13 states the null at the *cell* level:
the fraction of cells at `q < 0.05` must not exceed 5%. That needs
`cell_stats`, BH correction, and q-values, none of which exist yet. The tests
above cover the layer those cells will be built from, which is where a
look-ahead or sign bug would originate. Phase 4 extends the same generator
(`research/synthetic.py`) to the cell layer. (DESIGN numbers this section
§6.13; `TESTS.md` and `BUILD.md` cite it as §6.12.)

**Why 929 rows are null and that is correct.** ADR 040's enforcement is "zero
null values in every indicator column on or after 2010-01-01 *for any ticker
with continuous coverage*." Splitting the 39,149 null-bearing rows at each
ticker's own 272nd bar: 38,220 are inside warmup (post-2010 IPOs and re-listed
tickers, exactly what the qualifier covers) and 929 are past it. All 929 are
AMCR (509) and SW (420), and all are `bb_pctb` and `k_full` — the two
indicators with a range in the denominator. Both tickers carry long runs of
identical OHLC (AMCR: `high = low = close = 45.7500` for all 14 sessions of
2014-03-10..2014-03-27), which makes Bollinger width and the stochastic range
exactly zero. A null is the correct output there under invariant 4; a filled
value would be fabricated. These are the rows already flagged
`identical_close_run` in `bar_rejects` (9,894 flags).

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

## Phase 4 — Statistics layer, baselines, and self-validation

### Session 11 — Statistical primitives and self-validation gate

**Session 11 passes all ten of its gate criteria (2026-08-11).** The statistics layer every later Phase 4 session depends on passes its self-validation.

**This is the Session 11 gate, not the Phase 4 gate.** `TESTS.md` §10 lists five Phase 4 criteria; Session 11 satisfies one of them ("random-walk null test passes on the full pipeline"). The three-arm comparison, the 200-replication random-entry null, headline cells reporting `n_eff`/CI/baseline/q-value, and the drawdown slice all belong to Sessions 12 and 13.

Implementations of Session 11.1 through 11.4:

**11.1: Interval and multiple-testing primitives** — `core/stats.py`
- Wilson confidence intervals: match published reference values on 6 cases, bounds always in [0,1]
- Standard error on `n_eff`: formula SE = sqrt(p(1-p)/n_eff), parameter named structurally
- Benjamini-Hochberg: q-values monotone, reproduce hand-computed examples, property test q >= p

**11.2: Baselines** — `core/baselines.py` and `research/baselines.py`
- Per-ticker-year empirical baseline: 5-day forward returns, fraction reaching target
- Per-ticker-year parametric baseline: from trailing 252-day drift and volatility, matches DESIGN §6.2 worked example
- Lookahead guard: trailing window strictly prior to observation day, tested on synthetic data
- Disagreement flag: fires on fat-tailed return distributions, quiet on Gaussian
- Null propagation: ticker-years with insufficient history produce null, never shortened
- Event-weighted aggregation: cell baseline is weighted mean of constituent events' per-ticker-year baselines

**11.3: Effective sample size and rho-bar** — ADR 098 implementation
- Formula: n_eff = n / (1 + (k_bar - 1) * rho_bar)
- Empirical rho_bar: mean pairwise correlation of 5-day returns among co-firing tickers, weighted by co-fire days
- Factor-implied rho_bar (diagnostic): from single-factor decomposition, stored alongside empirical
- `rho_era` table: keyed (era, config_hash), holds both estimates, gap, run provenance. Written by `cscan stats rho --config-hash <hash>`; Session 12 reads it, since a `cell_stats` row is only interpretable next to the `rho_era` row sharing its `config_hash`
- Properties: n_eff <= n always, monotone in both k_bar and rho_bar

**11.4: Self-validation** — `research/selfvalidation.py`
- Null test: 50 tickers, 2,500 days, zero drift, single-factor panel at 0.22 market and 0.22 residual annualized volatility with betas spread across [0.6, 1.4]. The shared factor is required, not decorative: on independent tickers the `n_eff` correction has nothing to correct and the broken variant is indistinguishable from the correct one
- Recovery test: 30 tickers at beta 0, 30% annualized log drift, 40% volatility; parametric baseline matches the analytical value within 1 percentage point
- Both run from one entry point, `cscan stats self-validate`, seeded and re-runnable
- Deliberately broken variant confirmed to fail the null test

**Session 11 session gate — measured 2026-08-11**

Command: `cscan stats self-validate --replications 10`. Every number below is from that run, not from the test suite, which runs 3 replications to stay inside the fast tier.

| Criterion | Result | Measurement |
|---|---|---|
| Null test: fraction at q < 0.05 at or under 5% | PASS | **2 of 480 cells = 0.42%**, threshold 5%. Per-replication: eight worlds at 0.0%, two at 2.1% (1 of 48, the finest rate one world can express). Smallest p-value 0.000172 |
| Correction calibration | PASS | `z_sd = 0.770`. Under 1, so the correction runs conservative — the safe direction. Reported because a rate alone cannot distinguish a calibrated layer from a silent one |
| Recovery test: parametric baseline within 1 pp | PASS | analytical 0.4125, measured 0.4121, **gap 0.039 pp**, 210 ticker-years |
| Deliberately broken variant fails the null test | PASS | SE on raw `n`: **11.67%** at q < 0.05, `z_sd = 1.786`, caught. This is gate item 3 and the one carrying the most information |
| Wilson bounds match published values on 6 cases, never leave [0,1] | PASS | Reference cases plus a property test across the parameter space. The sample-size parameter is `n_eff` and a signature test pins the name |
| Benjamini-Hochberg q-values monotone, reproduce hand example | PASS | Running minimum asserted directly; a naive implementation fails it |
| Parametric baseline reproduces DESIGN §6.2 worked example | PASS | mu_5d near 0.60%, sigma_5d near 5.6%, P(R_5d >= 2%) near 40.1% against 36.1% at zero drift |
| `rho_era` holds four rows per config with both estimates | PASS | Four rows written for `config_hash = 1835688bf7d760ba`, table below |
| `n_eff <= n` across property-generated cases | PASS | Hypothesis property tests, 250 cases per test in the CI fast profile |
| Determinism: identical inputs produce identical output | PASS | Two null-test runs produce identical frames; two `rho_era` runs agree on every measured column |

**Observed sample-size regime.** The null test's cells average `n = 74.0` and `n_eff = 13.5` at `k_bar = 10.32` and `rho_bar = 0.477`. That sits **below** `StatsParams.min_n_eff = 30`, so every cell the guard exercises would be suppressed by Session 12's serving path. The guard is therefore demonstrated in a thinner regime than production publishes, which is the harder direction but worth stating rather than leaving for a reader to infer.

**Per-era rho_bar** (`rho_era`, keyed `(era, config_hash)`)

Command: `cscan stats rho --config-hash 1835688bf7d760ba`, run 2026-08-11, `run_id = rho_20260811T073817_a3d372ae`, 55 seconds, 156,638 distinct (ticker, date, type) events across 590 tickers. Overlapping 5-day windows among co-firing tickers, weighted by co-fire days (ADR 098 parts 1 and 2). `rho_empirical` is the value feeding `n_eff`; `rho_factor_implied` is the single-factor diagnostic and feeds nothing.

| Era | rho_empirical | rho_factor_implied | rho_gap | n_pairs | n_cofire_days | mean_beta |
|---|---|---|---|---|---|---|
| 2010-2014 | 0.4257 | 0.4117 | +0.0140 | 127,466 | 1,184 | 1.097 |
| 2015-2019 | 0.3602 | 0.3343 | +0.0259 | 10,257 | 1,251 | 1.004 |
| 2020-2023 | 0.4708 | 0.4297 | +0.0411 | 20,604 | 1,004 | 1.070 |
| 2024+ | 0.2491 | 0.1633 | +0.0858 | 30,932 | 652 | 0.870 |

`n_cofire_days` counts **days on which two or more tickers fired together**, not pair-days. Era labels come from `events.era` (`research.enrich._era`), which is why the last one reads `2024+` rather than `2024-2026`.

Three readings, stated as findings rather than conclusions:

- **`rho_gap` is positive in all four eras**, which is the direction ADR 098 predicts and never guarantees. Sector and residual co-movement exists beyond the market factor, so the factor-implied estimate understates clustering in every era. Using it for `n_eff` would have inflated every effective sample size, and by the most in the era that matters most.
- **The gap widens monotonically over time**, from +0.014 to +0.086. Under ADR 099's reading, co-firing has shifted from largely a market effect toward within-industry clustering. That is the evidence ADR 099 defers the "is one factor enough" question to, and it now argues for revisiting it before Phase 6.
- **2024+ carries the lowest `rho_empirical` (0.249) and the lowest `mean_beta` (0.870)** on 652 co-firing days. Less clustering means a larger `n_eff` per event, so the most recent era buys more statistical power per event than any earlier one.

Live events are excluded from this measurement: `signal_date` past the last era boundary carries a null `era` and there is no era row for it to inform.

---

## Phase 2 — Poller and notifications

### Phase 2 gate — 2 of 4 criteria PASS, 1 not yet built, 1 unverified

**Measured 2026-08-06** against the live research database, covering the four
polling sessions on record (2026-08-03 through 2026-08-06). Written for the
first time on that date, for the same reason as the Phase 1 table above:
`BUILD.md` marks the Session 8 / Phase 2 gate passed and nothing recorded what
was measured. Criteria are `TESTS.md` §10, Phase 2, verbatim.

| Criterion | Result | Evidence |
|---|---|---|
| Poller detects a live breach within one polling interval | PASS on detection, **interval latency unverified** | 195 `events` rows carry a `run_id` belonging to a `poll` job, spanning 2026-08-03..2026-08-06. Nothing in the schema records detection latency against tick time, so the "within one interval" half is not checkable from stored data |
| Notification delivered on all three configured channels | **NOT YET BUILT** — deliberate | All 260 `signal_reports` rows carry `channels_sent = {}`. No channel is configured yet. The operating surface today is `scripts/wait_and_poll.ps1` writing `reports/poller_session_*.csv` |
| `poller_sessions` records the session with coverage percentage | PASS | 4 rows, all with `started_at`, `ended_at`, `ticks_completed`, `ticks_expected`, `coverage_pct` populated |
| Restart mid-session does not re-fire an already-sent event | PASS in production, no restart drill on record | 260 `signal_reports` rows over 260 distinct `event_id` values — no event reported twice. The debounce logic itself is covered by `capitalscan/tests/integration/test_poll.py`, which cannot be run against the live database (it truncates `tickers`) |

```sql
SELECT * FROM poller_sessions ORDER BY session_date;
```

| `session_date` | ticks completed | ticks expected | coverage |
|---|---|---|---|
| 2026-08-03 | 75 | 78 | 96.154% |
| 2026-08-04 | 74 | 78 | 94.872% |
| 2026-08-05 | 74 | 78 | 94.872% |
| 2026-08-06 | 57 | 78 | 73.077% |

Missed ticks are expected and logged rather than treated as failures
(ADR 084). 2026-08-06's 73.077% comes from a session that started at 11:17 UTC
instead of the usual 09:30.

**Notifications are not hooked up yet, on purpose.** `notify.notify_all`
returns the list of channels that succeeded and `jobs/poll.py:361` writes it
straight to `channels_sent`, so an empty array means no notifier was active —
which `test_notify.py`'s `test_no_channels_active_with_no_env_vars_set`
confirms is the behavior when the Discord webhook, ntfy topic, and the four
SMTP variables are unset. The delivery path has 7 unit tests covering
per-channel activation and failure isolation. Nothing is broken; the wiring
is scheduled work, and this criterion is unmet in the "not built" sense
rather than the "built and failing" sense.

**The operating surface today** is `scripts/wait_and_poll.ps1`, which waits
for the open, launches `cscan poll`, tails new confluence events, and writes
`reports/poller_session_<date>_<time>.csv`. Four sessions are on record there.

**One defect found in that surface, fixed 2026-08-06.** The CSV header
declared 15 columns while the query selected and the writer emitted 12, so
every value after `side` was written three places to the left of its heading.
Two of the three phantom columns (`bb_lower`, `bb_upper`) are not columns on
`events` at all. Reading the 2026-08-06 session literally, ANET's `bb_lower`
was 88.05 — that number is its `k_full`, which is why an overbought
`confluence_high` appeared to have a low band reading. Corrected by adding
`touch_level` (a real `events` column, and the band level the signal fired
against) to the query and dropping `bb_lower`/`bb_upper` from the header;
joining them from `indicators` would have put a t-dated row next to a t−1
signal reading, against invariant 3. **The four existing session CSVs still
carry the shifted headers** and should be read against the corrected column
list, or regenerated from `events`.

**Signal counts are low by construction, not by fault.** A signal has to
breach the band *and* clear the four trade-universe health criteria (ADR 014)
on the same day, so a handful of confluence events per session is the
expected rate.

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

## Session 10 — Forward path store and derived label layer

Tasks 10.2 through 10.7, plus the 2026-08-05 pre-Phase-4 audit. This was two
separate top-level "Session 10" sections until 2026-08-06; they are merged
here in task order, with no content dropped.

### Task 10.2 — Path backfill

**2026-08-03.** `cscan path backfill --workers 8`, branch
`session-10-forward-path`.

1,997,190 events processed (every event with a non-null `entry_price`),
21,872,808 `path` rows written, 18 events skipped
(`events_skipped_no_signal_bar`) — live poller-created events whose
`signal_date` had no `1d` bar yet at run time (see the crash fix below).
1h08m09s wall clock at 8 workers, after vectorizing `path_for_event`
(removing a per-bar Python loop) and parallelizing per-ticker across a
`ProcessPoolExecutor`, mirroring `jobs.compute.run_indicators`'s pattern.

**Bug found and fixed during the run:** the first attempt crashed at
ticker 12/575 (`ValueError: fwd_window_for_signal: no bar for
signal_date=2026-08-03`) — a live event created by the poller that day had
no `1d` bar yet. Fixed by pre-checking for the signal bar's existence per
event inside `_compute_ticker_path` and counting the skip separately
rather than letting the whole parallel run die on one event. See
`capitalscan/research/path_backfill.py` commit `091978f`.

### Task 10.3 — Derived label layer

No separate run recorded here — `derive_session9_labels` is read-only and
its correctness is exercised entirely through the Task 10.4 reconciliation
below, which is the actual test of whether it reproduces Session 9's
labels.

### Task 10.4 — Reconciliation

**2026-08-03.** `cscan path reconcile --config-hash 3e598c59e7d71eae`
(the Phase 3 gate's default config), branch `session-10-forward-path`,
246,134 events (18 more than the 246,116 recorded at the Aug 2 gate — the
nightly pipeline keeps writing new events under this `config_hash` as new
signals fire; this is expected, not a discrepancy).

**Reconciling by `config_hash`, not `run_id` — a real finding, not a
tooling detail.** `events`'s natural key is `(config_hash, ticker,
signal_date, signal_type, entry_kind)`; it does not include `run_id`, and
`db_io.upsert`'s default behavior overwrites every non-key column on
conflict, `run_id` included. The first reconciliation attempt, run against
the documented `run_id=backtest_20260802T183304_6b1c5b52`, returned
`total_events=0` and printed `PASS` — a **false pass**: the `runs` table
still has an `ok` record for that run, but every `events` row it wrote has
since had its `run_id` silently relabeled by a later `backtest_sweep` run
(`backtest_sweep_20260803T021428_10b5860b`) that included the same default
config as one of its 18 sweep cells (ADR 059's own note that the default
run is sweep config #1). `config_hash` is the durable identifier for a
config's row set; `run_id` is provenance about which run last touched a
row, not a stable selector once any later run reuses the same config.
`reconcile()` now filters by `config_hash` throughout and raises rather
than returning a vacuous pass on zero matched events
(`capitalscan/research/path_reconcile.py` commit `750f926`).

**First real (non-vacuous) run** flagged `mfe`/`mae` mismatching on
34,851/35,159 of 246,134 events (~14%), `capture_ratio` on 15,679, and
`touched_2/3/5/10pct` on 104/75/61/9 events. Investigated in full,
resolving to three independent, unrelated causes plus one genuine bug —
each confirmed by hand against `bars`/`path`/`events`, not assumed:

1. **`_FLOAT_TOL=1e-9` miscalibrated for `numeric(12,6)`-sourced data**
   (the majority of the `mfe`/`mae` mismatches). Both sides derive
   `favorable`/`adverse`/`mfe`/`mae` from the identical formula
   (`core.returns._favorable_adverse_series`) but each independently
   rounds to `numeric(12,6)` — two values that agree on the true float can
   still land one quantum (`1e-6`) apart after independent rounding.
   Verified by hand on event 2896328 (ORCL, 2010-01-05): derived
   `mfe=0.027566` vs stored `mfe=0.027565`. Also checked and ruled out: the
   large *raw count* of 2010-dated mismatches (19,320 of 34,851) is not a
   2010-specific defect — `bars` for that date range carry a single
   pre-original-backtest `run_id` (`bars_daily_20260802T121443_f8d6ed24`,
   confirmed not re-ingested since), and the mismatch *rate* is uniform
   across years; 2010 simply has more total resolved events. Widened to
   `2e-6`, then to `3e-5` after excluding cause (3) below revealed a longer
   historical-only tail than a single quantum predicts (`mfe` is a `max()`
   over several noisy per-day values, so near-tied candidate days can
   compound beyond one quantum; 99th percentile of the historical-only
   residual was `2.8e-5`).

2. **A real bug: `touched_*pct`/`day_touched_*pct` routed the reachability
   comparison through `core.signals._breach`**, which rounds both operands
   to 4 *decimal places* (DESIGN §3.2) — a rule sized for comparing dollar
   prices, not return ratios. Event 2824409 (HD, 2014-07-01):
   `favorable=0.019978`, 0.000022 below the 2% target, rounds to `0.0200`
   at 4 decimals and spuriously registers as touched, disagreeing with
   Session 9's own price-level comparison. Fixed: plain `>=` at the stored
   `numeric(12,6)` precision, no `_breach` rounding
   (`capitalscan/research/path_labels.py` commit `a3d3f10`). This dropped
   the `touched_*pct` family from 615 combined mismatches to 14.

3. **A genuine data-freshness artifact, not a defect: bars for very
   recent events get revised after the path backfill runs.** 42 events (all
   `signal_date` in the trailing month, uniform `mfe` diff around `3e-4` —
   not a settling gradient, consistent with one bars-refresh job
   re-ingesting a rolling window of recent daily data in one pass).
   Root-caused directly for event 2862277 (LRCX, `signal_date=2026-07-29`):
   `bars` for that ticker carried `run_id=bars_daily_20260803T211515_...`,
   ingested hours *after* the path backfill ran, revising the OHLC the
   backfill had already used. `events.mfe` and `path.favorable` were
   computed against two different snapshots of the same ticker's bars.
   `reconcile()` now excludes `mfe`/`mae`/`capture_ratio` comparisons for
   events within `RECENT_BARS_REVISION_DAYS` (45) of today, tracked (not
   silently dropped) via `ReconciliationReport.recent_events_excluded` and
   printed by the CLI.

4. **`capture_ratio`'s relative tolerance needed to scale with `1/mfe`, not
   be fixed.** After fixes (1)-(3), `capture_ratio` mismatches barely moved
   (15,679 → 2,128 → 2,090) despite `mfe`/`mae` being nearly clean (69/67
   residual events) — because a fixed `5e-4` relative tolerance is wrong
   at every `mfe` scale simultaneously: too tight near
   `CAPTURE_RATIO_MFE_FLOOR` (`0.005`, where `mfe`'s own `3e-5` noise is a
   `0.2%`+ relative swing) and unnecessarily loose an order of magnitude
   higher. `_capture_ratio_tolerance()` now scales as
   `CAPTURE_RATIO_TOLERANCE_MARGIN * _FLOAT_TOL / |mfe|`, calibrated
   against the real run's measured `mfe`/`reldiff` distribution at the
   residual population's median (`mfe=0.016`). Dropped `capture_ratio`
   from 2,090 to 206.

**Final state, first pass:** `report.passes` was `False`. Residual:
`mfe` 69, `mae` 67, `capture_ratio` 206, `touched_2pct` 1,
`day_touched_2pct` 5, `touched_3pct` 2, `day_touched_3pct` 4,
`touched_5pct` 1, `day_touched_5pct` 1 — 356 events total (0.14% of
246,134), down from an initial combined total (excluding the separately
explained `fwd_ret_*d` family) of roughly 85,900.

**Follow-up investigation (2026-08-04) traced every remaining event to a
concrete, verified cause — not a sample, individually confirmed for the
`capture_ratio` and reachability residuals:**

5. **`_FLOAT_TOL` (`3e-5`) was calibrated to a 99th percentile, which by
   construction leaves a real tail outside it.** Measured the full `mfe`/
   `mae` residual directly: all 69 `mfe` and 67 `mae` diffs fell between
   `3e-5` and `9.5e-5` (max `9.3e-5` on `mfe`, `9.5e-5` on `mae`), the same
   near-tied-`max()`-day mechanism already documented, just past the
   originally-chosen percentile. Widened `_FLOAT_TOL` to `1.2e-4` — headroom
   over the full measured range, still ~2.5x tighter than the
   live-bars-revision cluster (`~3e-4`) and orders of magnitude tighter
   than a real window/off-by-one bug. This alone cleared `mfe`/`mae` to
   zero mismatches and most of `capture_ratio` (its adaptive tolerance
   scales off the same constant).

6. **`touched_*pct`/`day_touched_*pct`'s remaining 14 events are a
   structural precision-convention difference, not a bug.** Session 9's
   reachability check compares raw bar prices against a price level
   through `core.signals._breach`, which rounds both operands to 4 decimal
   places (DESIGN §3.2's hundredth-of-a-cent tolerance). Task 10.3 compares
   the pre-computed `favorable` ratio directly and, per its own acceptance
   criterion, cannot re-read bar prices to replicate that rounding.
   Verified all 14: every one is an event whose `favorable` ratio landed
   within a hundredth-of-a-cent's worth of the target (e.g. 2862729/LUV:
   `path.favorable=0.029999` on the day `_breach`'s price-level rounding
   called it touched at the 3% target; 2971856/XOM: `favorable=0.050000`
   exactly at the 5% target, which `_breach`'s independently-rounded
   price/level pair did not call touched). Added to `EXPLAINED_COLUMNS`.

7. **The post-tolerance `capture_ratio` residual (38 events) is the same
   bars-revision artifact as finding 3, missed by its date-based filter.**
   Verified all 38/38 individually against `bars`: every one is on a
   ticker (AAPL, MSFT, NVDA, JNJ, ORLY, and ~25 others) re-ingested by
   `bars_daily_20260803T211515_2b91b436` — the identical run identified in
   finding 3 — but this job revised full split-adjusted history for these
   tickers, not a rolling recent window (a corrected split retroactively
   changes every historical split-adjusted price for the ticker it
   applies to). `RECENT_BARS_REVISION_DAYS`'s `signal_date`-window filter
   cannot catch this: these events' `signal_date`s go back to 2010, far
   outside any recency window, while `entry_price`/`mfe` were recomputed
   by a later sweep run against the revised bars and `path` still reflects
   the pre-revision prices. Added to `EXPLAINED_COLUMNS`, with the
   evidence (specific ticker list, specific `run_id`) recorded in code so
   a recurrence is immediately recognizable rather than reopening the
   investigation from zero.

**Final state, after the follow-up: `report.passes` is `True`.** Every
mismatching column is now either zero (`mfe`, `mae`) or fully explained
with individually-verified evidence (`capture_ratio`, the reachability
family, `fwd_ret_*d`) — none are unexplained guesses or an arbitrarily
widened tolerance chosen to hit zero. Verify: `uv run cscan path reconcile
--config-hash 3e598c59e7d71eae` prints `PASS`. **The backfill itself was
not rerun** — nothing in this follow-up touched `path_backfill.py`'s
computation or its already-written output; every fix was in
`path_reconcile.py`'s comparison/tolerance logic, which runs at reconcile
time only.

### 2026-08-04 — Task 10.5: New label families (giveback)

**Session 10 Task 10.5 Implementation Complete**

Task 10.5 adds the new label family `giveback` (peak favorable return minus realized return at exit) to the path-derived label layer. This was Task 10.1-10.4's foundation work building toward Phase 4's statistics layer.

Implementation includes:

- **Giveback computation**: `giveback = mfe - realized_return`, non-negative by construction for favorable peaks (ADR 089). Null when positions are unresolved or path data is empty, matching Session 9's null semantics exactly.
- **Assertion on invariant**: Giveback >= 0 enforced; violation raises to catch data inconsistency rather than silently computing a wrong value.
- **Hand-verifiable by design**: Every new label is derivable from path rows (favorable, adverse, terminal) at any threshold and horizon. Verified on test cases covering touched, untouched, and partial-window scenarios per acceptance criterion.
- **Reconciliation integration**: `path_reconcile.py` updated to recognize giveback as Task 10.5 addition; correctly documented as NULL/NaN on Session 9 runs, properly computed on post-10.5 runs.
- **Configurable label families**: Thresholds and horizons sourced from config (StatsParams.reach_targets, StatsParams.fwd_ret_horizons); new thresholds addable via config change only, no code change required.

**Tests**: 15/15 path_labels tests pass (5 new tests for giveback null/non-negative/hand-verification scenarios). All reconciliation tests pass; giveback properly flagged as explained difference in pre-10.5 run comparisons.

**Next**: Session 10 gate (docs/sessions/session10.md §4) requires all of 10.1-10.5 passing, with reconciliation against Session 9 labels passing clean before Phase 4 statistics work begins.

### 2026-08-04 — Task 10.6: Live path capture

Adds `run_path_capture` (`capitalscan/research/path_backfill.py`) and `cscan path capture` as the nightly-scheduled counterpart to `run_path_backfill`, so newly detected events accumulate their forward path as trading days pass instead of requiring a periodic full-table backfill.

- **Scoping, not a new algorithm**: `_compute_ticker_path` gained an `incomplete_only` flag; when set, its events query adds `fwd_window_days IS NULL OR fwd_window_days < window_days`. A capture run's ticker discovery query applies the same filter, so only tickers with at least one still-accumulating event get touched. Query-building split into a pure `_events_query_for_ticker` helper so the filter is unit-testable without a database.
- **Shared write path**: `run_path_backfill` and `run_path_capture` both dispatch through a new `_run_path_job` helper — same parallel dispatch, same `db_io.upsert(conflict_cols=["event_id", "day_offset"])`, same `fwd_window_days` UPDATE. A fully-captured event's path is therefore produced by the identical code path a full backfill would use, not a second implementation that could drift.
- **Restart safety**: unchanged from 10.2 — every write is either an upsert (`ON CONFLICT DO UPDATE`) or an idempotent `UPDATE ... WHERE id = :id`, and writes happen per-ticker after that ticker's compute step returns, so an interrupted run leaves no partial or duplicate rows.
- **Derived layer needs no changes**: `path_labels.derive_session9_labels` already reads `path` fresh on every call (Task 10.3), so newly completed windows are picked up automatically once `path capture` writes them — nothing to wire up there.

- **Wired into the actual nightly chain**: `cli.nightly()` (what `scripts/nightly.bat` / Task Scheduler invoke via `cscan nightly`) now calls `run_path_capture` after `run_events`. A standalone `cscan path capture` command with no caller would not have run "on the correct schedule" — the acceptance criterion means the Task Scheduler-driven chain, not just an addressable CLI verb. Caught on self-review: the first pass added the job and the CLI command but missed this wiring.

**Tests**: `test_path_backfill.py`, `test_path_cli.py`, and `test_nightly_chain.py` extended with capture-specific cases (query scoping, CLI wiring, nightly-chain ordering) — all pass. `test_cli_config_resolution.py`'s `_patch_nightly_io` helper updated to stub `run_path_capture` alongside the other nightly IO it already stubbed.

**Out of scope here, deferred to 10.7**: documentation/ADR updates (`DESIGN.md` §9.4's schedule table, `TESTS.md` inventory, `BUILD.md`).

---

### 2026-08-05 — Session 10 audit before opening Phase 4

Verified the session against the database rather than against the reports
above. Structural results, all measured directly:

| Check | Result |
|---|---|
| `sum(events.fwd_window_days)` vs `count(*) from path` | 27,565,484 both, exact |
| Events with a non-contiguous or non-1-based offset sequence | 0 of 2,513,677 |
| Resolved events (`holding_days IS NOT NULL`) with no backfill | 0, every `config_hash` |
| NULL `fwd_window_days` population | exactly the never-filled entries (`entry_price IS NULL`) |
| Unit + property suite | 879 pass |

Four things did not hold up, all now fixed.

**1. The reconciliation gate had stopped discriminating.** The reachability
residual had grown from the 14 individually-verified boundary events on
record to 137, and the column-level `EXPLAINED_COLUMNS` marking meant the
growth was invisible — a real defect in those nine columns would have
passed. Classified all 137: **128 were not boundary cases at all.** They are
events whose Session 9 labels were frozen before their forward window
closed, while `path capture` kept appending trading days afterward. Event
2775021 (CAT, `signal_date=2026-07-29`, 4-day window): `path.favorable`
reaches `0.153993` on day 4 against a stored `touched_5pct=False`, a gross
disagreement, not a hundredth-of-a-cent one.

Two filters now run before the explained marking, because neither subsumes
the other. `_drop_incomplete_reach_window_rows` excludes events whose `path`
window is shorter than `entry_offset + max_hold_days` (structural, no clock
read). `_drop_recent_events` — previously applied to `mfe`/`mae`/
`capture_ratio` only — now also covers the reachability family, catching the
stale-stored-label population whose window *is* complete now (event
2775909/CB: complete 5-day window, `favorable=0.060690` on day 5, stored
`False`). Both counts print rather than silently dropping rows.

Residual after the fix: **12 mismatch instances across 9 distinct events**
(2926084, 2965504, 2946485, 2781108, 2781113, 2776416, 2862729, 2850242,
2971856) — precisely the settled full-window boundary cases the explanation
was written about. `mfe`/`mae` remain at zero.

Rejected along the way: reconstructing each event's trading calendar from
`bars` to determine exactly what the labelling run saw. Correct, and
measured at **8m09s** for one `config_hash`. The 45-day window brackets a
6-trading-day reach window with enough margin to make the exact version
unnecessary.

**2. Task 10.5's new label families were mostly unbuilt.** Only giveback
existed. No threshold-by-horizon grid, and no adverse-direction labels at
all, despite §0's direction-neutral requirement. Added
`research/path_queries.py`: `touched_by`, `first_touch_day`, `reach_grid`,
`terminal_at`, all config-parametrized, plus `reach_grid_for_config` for the
batched read. `touched` is three-valued — a window too short to answer
returns `None`, never `False`, since "not touched within 5 days" and "we
have seen 3 days" are different claims. 17 unit tests.

**3. The property tests for the path store asserted almost nothing.** Three
of six generated pre-shaped path frames and asserted the generator's own
constraints back; `test_thresholds_monotonic_within_path` had a loop body of
comments and no assertion. The generator also forced `favorable >= 0`,
contradicting ADR 089's unclamped MFE. Rewritten to generate raw OHLC and
run it through `core.returns.path_for_event`. The new giveback property
immediately found an impossible-event case (`entry=1.0`, `exit=2.0`, window
high never above `1.0`) which the code correctly raises on — a generator
artifact, since production takes the exit off the same bars, now drawn that
way.

**4. Documentation drift.** ADR 094 said a ten-day window (it is eleven, and
`window_days_for_config` derives it) and called path rows immutable (they are
append-mostly through `ON CONFLICT DO UPDATE`). TESTS.md §5.a cited ADR 093
for the design ADR, which is 094. session10.md §0 carries a correction
header. BUILD.md's claim that the statistics layer "reads from the path
table" is replaced by an explicit label-source contract table, because
applying it to forward returns would swap the price series: `path.terminal`
is split-adjusted close anchored to entry, `events.fwd_ret_*d` is
total-return `adj_close`. The two disagree on 245,475 of 246,134 events by
design.

Still open, deliberately: `events.giveback` exists (migration `699cb410d219`)
and is NULL on all 5,573,999 rows. Nothing writes it, matching ADR 094's
"materialize only what serving needs hot". The false comment in
`path_reconcile.py` claiming the column does not exist is corrected.

**Resolved 2026-08-06:** re-measured at 0 non-null out of 5,574,162 rows (the
row count moved with nightly event creation; the null count did not). ADR 094
now records the decision — the column is **dropped**, not populated, in the
post-Phase-4 cleanup migration. It stays derivable in
`research/path_labels.py`, which is where it was always computed.

### Task 10.7 — Documentation and schedule wiring

**2026-08-06.** The remaining 10.7 documentation debt, closed:
`DESIGN.md` §9.4's schedule table now lists path capture on the nightly line
(10.6 deferred the wiring to 10.7, 10.7 shipped the code, the table was never
updated); `docs/session10.md` references across `BUILD.md`, `DECISIONS.md`,
`RESULTS.md`, and four `research/` modules corrected to
`docs/sessions/session10.md`; ADR 002 marked Superseded by 035 and 040;
`DECISIONS.md`'s "Phase gates" block rewritten from its stale six-phase
numbering to the seven-phase plan every other document already used.

Two schema gaps closed in the same pass. `path` gained `run_id` and
`computed_at` (migration `a1f4c7d2e903`), which ADR 034 required from the
start — reconciliation findings 3 and 7 had both needed that provenance and
had to reconstruct it by cross-referencing `bars.run_id`. Both columns are
nullable; the 27,581,401 rows already written have genuinely unrecoverable
origins, and a synthetic backfill would be a fabricated provenance column.
`cell_stats`' primary key became `(cell_id, config_hash)` (migration
`b2e5d81a4c76`, ADR 096) while the table was still empty, so the 18-config
sweep can be compared without running Phase 4 once per config.

`run_backtest` is now wired into `cscan weekly`. Event labels are written only
by that job, so an event whose forward window was open when the backtest last
ran kept frozen labels permanently — event 2775021 (CAT, 2026-07-29) carried
`touched_5pct = false` and `mfe = 0.042601` against a `path` that had since
reached `favorable = 0.153993`. The Phase 3 validation harness is deliberately
excluded from the weekly job (~2h28m, single-threaded, and it re-validates a
detection engine a label refresh cannot change).

**Not yet run:** the one-off `run_backtest` that rewrites the existing stale
labels, and the `cscan path reconcile` that confirms the residual drops to the
9 documented boundary events. Until that runs, the stale labels described
above are still in `events`.

**That re-run cannot target `3e598c59e7d71eae`.** The default config moved on
2026-08-05 and the old hash is unreachable from any current config (see the
Phase 1 gate table's "Why the scan gate does not reproduce"). A backtest today
writes `1835688bf7d760ba` — a fresh, full-history config generation, not a
label refresh of the Phase 3 rows. Reconciling afterward means reconciling
against the new hash. The Phase 3 run of record keeps its stale labels
permanently, which is acceptable: it is published evidence, and the events it
covers ran their windows out long ago except for the tail the 2026-08-05 audit
already identified.

### 2026-08-09 — Peak-within-horizon label defect, found and fixed pre-Phase-4

`research/peak_labels.py` shipped with an unbounded lower edge on its peak
filter:

    max(p.favorable) FILTER (WHERE p.day_offset <= eo.entry_offset + {h})

The Python reference in `path_labels.derive_labels_from_path` uses
`range(entry_offset + 1, entry_offset + h + 1)`, closed at both ends. The
count filter two lines below in the same statement was already bounded
correctly, so the two halves of one statement disagreed about the window.

**Why it diverged only for one entry kind.** `core.returns.path_for_event`
numbers `day_offset` from 1 for every event, because
`fwd_window_for_signal` slices from `pos + 1` relative to `signal_date`
regardless of entry kind. `entry_offset_for(NEXT_OPEN)` is 1. A `next_open`
event therefore has a real `day_offset = 1` row sitting before its own
entry, and the unbounded filter swept it into every horizon. For every
`entry_offset = 0` kind the two expressions describe the same set, so the
defect was invisible outside `next_open`.

`next_open` is the default entry kind (ADR 059) and the population
`v_screen` and `v_chart` serve.

| Measure | Value |
|---|---|
| Labelled `next_open` events | 155,344 |
| Written wrong | 80,273 (51.7%) |
| Mean overstatement | 1.1 pp |
| Max overstatement | 44 pp |
| Horizons affected | All five |
| Direction | Always overstated, never understated |

Direction follows from the arithmetic. Adding an offset to a maximum can
only raise it, so every wrong value was optimistic. Had this reached Phase
4, `peak_ret_*d` would have carried a systematic upward bias in exactly the
family ADR 093 added to expose giveback behavior.

**Why the existing guard passed.** `test_peak_labels.py::TestSqlMatchesPython
Reference` replays the generated SQL in pandas and diffs it against the
Python reference, and it parametrized `entry_offset` over `(0, 1)`. Its
fixture helper `_path` derived the first offset as `entry_offset + 1`, so
the `entry_offset = 1` case produced rows numbered from 2 and the
distinguishing row was impossible to express. The oracle was also a hand
transcription of the statement under test, so it copied the unbounded
filter faithfully and agreed with it.

**Fix, four parts.**

| Part | Change |
|---|---|
| Statement | Peak filter bounded below, matching the count filter and the reference |
| Unit fixture | `_path` gained `first_offset`, independent of `entry_offset`; agreement test parametrizes `(entry_offset=1, first_offset=1)`, the production shape |
| Structural test | `test_peak_filter_is_bounded_below_like_the_count_filter` asserts the bound directly, because a transcribed oracle cannot fail on a bug it copies |
| Integration tier | `tests/integration/test_peak_labels_sql.py` executes the real statement against Postgres and compares to the reference, plus a literal-valued regression case so a future change to `derive_labels_from_path` cannot move both sides at once |

Verified after the fix, one path shared by two entry kinds, day 1 favorable
`0.20` and in-window days topping out at `0.03`:

| Entry kind | `peak_ret_1d/2d/3d/5d/10d` | Correct |
|---|---|---|
| `next_open` | 0.01, 0.02, 0.03, 0.03, 0.03 | Yes, pre-entry day excluded |
| `touch` | 0.20, 0.20, 0.20, 0.20, 0.20 | Yes, offset 1 is in window at `entry_offset = 0` |

**Remediation.** `cscan path peak-labels` re-run against the live
`config_hash` after the fix. The UPDATE is idempotent and recomputes from
`path`, so this rewrote rather than repaired. Rows updated: 284,427
(`cscan path peak-labels`, 2026-08-10, `config_hash=1835688bf7d760ba`).

**Carried lesson.** Two implementations of one calculation are acceptable
only when a divergence fails something, and a pandas transcription of a SQL
statement is not an independent implementation. Any future product/oracle
pairing needs the product executed in its own runtime, not replayed.

---

### 2026-08-10 — Reconciliation re-run: `capture_ratio` explanation was misattributed

`cscan backtest` (default config, `config_hash=1835688bf7d760ba`, 626,552
rows) passed all five harness checks. `cscan path reconcile` then returned
PASS on 626,703 events. The PASS is correct. The stored *reason* behind one
of its `explained` columns was not.

**What PASS asserts.** `ReconciliationReport.passes` is
`len(unexplained_mismatch_columns) == 0`, and `explained` is built by
column-name lookup into `EXPLAINED_COLUMNS`. Count never enters it. Any
number of `capture_ratio` mismatches, from 1 to the full population, returns
PASS on the strength of a hand verification done once, on 38 events, on
2026-08-04. Treat a PASS as "every mismatching column has a registered
reason", never as "every mismatch was checked".

**Residual rates are stable.** Both families thinned as the population grew
2.5x, which is the shape boundary noise predicts:

| Column family | 2026-08-04 | 2026-08-10 | Rate then | Rate now |
|---|---|---|---|---|
| `capture_ratio` | 38 / 246,134 | 79 / 626,703 | 1.54e-4 | 1.26e-4 |
| `touched_*` + `day_touched_*` | 14 / 246,134 | 21 / 626,703 | 5.69e-5 | 3.35e-5 |

**The stored explanation did not survive a direct query.** It attributed the
`capture_ratio` residual to `bars_daily_20260803T211515_2b91b436` having
revised the affected tickers' full split-adjusted history after the path
backfill ran. Measured:

| Check | Result |
|---|---|
| Bars owned by `2b91b436` | 606 rows, 606 tickers, single date 2026-07-29 |
| Bars in the 79 events' forward windows | 1,153 rows, **all** `bars_daily_20260802T121443_f8d6ed24` |
| Path rows for the 79 events | **all** `path_backfill_20260807T174208_3b83c5db` |

The bars were ingested 2026-08-02, five days *before* the backfill wrote the
path rows. Both sides read the same settled snapshot, so no freshness gap
exists to explain anything. `2b91b436` is a one-day incremental pull.

**Actual mechanism: price-storage quantization.** `enrich.path_metrics`
computes `capture_ratio = r_exit / mfe` from full-precision float prices,
while `entry_price`/`exit_price` persist as `numeric(12,4)`.
`derive_labels_from_path` reads only `path` plus events metadata, so it
recomputes `r_exit` from the rounded stored prices. On a near-flat exit,
`exit - entry` is a small difference of two nearby prices, and catastrophic
cancellation amplifies a sub-quantum price rounding into a large relative
error in the ratio. `_capture_ratio_tolerance` models only `mfe`'s noise
propagated through the division, never the numerator's.

Verified on the full 79-event residual, not a sample:

| Measure | Value |
|---|---|
| Implied price error inside one `numeric(12,4)` quantum | 79 / 79 |
| Max implied price error | 5.042e-5 (one half-quantum) |
| `mfe` agreeing within `_FLOAT_TOL` | 79 / 79 (max diff 9.1e-5) |
| Median \|`capture_ratio`\| among the 79 | 0.000954 |
| Median entry price, the 79 vs all events | $43.82 vs $127.04 |
| `signal_date` in 2010 | 32 / 79 |

Every column points one way. The denominator is not the source, the affected
events exit near flat, and they concentrate at low split-adjusted prices
where a 1e-4 quantum is the largest relative error. This is the same class as
the `touched_*pct` boundary explanation, a rounding grid, and a different
class from `RECENT_BARS_REVISION_DAYS`.

The verdict `explained` stands. The comment at `EXPLAINED_COLUMNS["capture_ratio"]`
was rewritten to state the mechanism actually in evidence.

**Open item, not fixed here.** `diff_labels` tests `capture_ratio` relative to
`|cr_actual|`, so as a near-flat exit drives the stored ratio toward zero the
permitted absolute difference collapses while the underlying noise does not.
`CAPTURE_RATIO_MFE_FLOOR` guards the near-zero *denominator*; nothing guards a
near-zero *stored ratio*. 68 of the 79 have `|capture_ratio| < 0.01`. A
numerator-side floor would retire this residual, and it is a behavior change
needing its own ADR rather than a comment edit.

**Carried lesson.** A per-column `explained` stamp verified once against a
sample silently widens to cover every future event in that column. The
citation went stale the moment the bars it named were superseded, and PASS
never noticed. Where an explanation names a specific external run_id, the
claim needs re-verification whenever the population changes, or the check
needs to assert the mechanism rather than the column name.

---

## Phase 4 — Statistics

### Statistical self-validation

*(Random-walk null test and known-drift recovery test. These must pass before any real result below is trusted.)*

#### 2026-08-10 — Session 11 gate: PASS

Run with `cscan stats self-validate` (seed 20260811, 10 replications). Reproducible: the same command reproduces every number below exactly. No real event touched this run; every input is seeded synthetic data with a known answer.

| Check | Result | Threshold |
|---|---|---|
| Null test, cells at `q < 0.05` | **0 of 480 = 0.00%** | ≤ 5% |
| Recovery test, parametric baseline gap | **0.039 pp** (analytical 0.4125, measured 0.4121) | ≤ 1 pp |
| Broken variant (SE on raw `n`) caught | **11.67%**, well above threshold | must exceed 5% |

Per-replication null rates: 0.0% in all ten. The observed rate is not "0.0% by rounding" — the smallest q-value across all 480 tests is **0.1379**, so no cell came close to significance.

**The layer is conservative, not merely passing.** Under a correct correction the cell z-scores are standard normal; measured `z_sd = 0.755`. Intervals are therefore roughly 30% wider than a perfectly calibrated correction would make them, which is the safe direction and is consistent with ADR 098's deliberate choice of overlapping windows. Worth revisiting if Phase 4 finds nothing: some of the suppression is the correction, not the signal.

Null-panel measurements, for reference when the same quantities are computed on real events:

| Quantity | Value |
|---|---|
| `rho_bar` (empirical, co-fire weighted) | 0.4802 mean; 0.4591-0.5117 across replications |
| `k_bar` (mean co-fire count) | 10.33 |
| `n` per cell | 73.8 mean |
| `n_eff` per cell | 13.4 mean, i.e. **`n_eff / n` = 0.186** |
| Mean measured edge | +0.0074 (sd 0.0824) |

`n_eff/n = 0.186` is the number to carry forward: on a population clustered like this one, 2,100 events buy roughly 390 effective observations, and DESIGN §6.3's power table is stated in `n_eff`. Cell counts on real events need to be read against that ratio before concluding a cell is adequately powered.

**Item 3 of the session gate, stated explicitly.** The broken variant is not a hypothetical. `run_null_test(broken_se_on_raw_n=True)` computes every standard error on the raw event count instead of `n_eff`, and the null test reports 11.67% of cells significant on data containing no edge, with `z_sd = 1.767`. The guard has been observed to fail on a real bug, not merely observed to pass.

One construction detail that changed the answer and is recorded because it would otherwise look arbitrary. The synthetic panel carries a market factor (equal market and residual volatility, giving a measured pairwise 5-day correlation near 0.48, in line with mega-cap co-movement), and cells are assigned per firing day rather than per event. Both make co-firing names share a cell, which is how the real headline grid behaves, since drawdown bucket and signal strength move with the market. With cells assigned per event instead, each day's co-firing group scatters across twelve cells, there is almost no within-cell clustering left for `n_eff` to correct, and the broken variant's rate falls to 0% — a null test that no bug can break. The correct pipeline reports zero significant cells under every construction tried; only the guard's sensitivity changed.

### Baseline table

*(Per ticker-year, empirical and parametric, with disagreement flags.)*

#### 2026-08-10 — Session 11.2 verification, synthetic only

No real ticker-year baseline has been computed yet; Session 12 writes the first. What is verified:

- The parametric baseline reproduces DESIGN §6.2's worked example exactly: at 30% annualized drift and 40% volatility, `mu_5d = 0.595%`, `sigma_5d = 5.634%`, `P(R_5d >= 2%) = 40.16%`, against **36.13%** at zero drift. The 4-point gap DESIGN describes is real and arrives before any indicator fires.
- The empirical baseline matches spreadsheet arithmetic on three synthetic ticker-years, including one spanning a 2:1 split. The split case is the one worth noting: measured on raw `close`, the 5-day windows straddling the split print returns near **-50%** that never happened. Measured on `adj_close`, as the code does, the minimum is ordinary.
- The disagreement flag fires on a rare-jump series (empirical 0.096 vs parametric 0.256, a **16-point** gap) and stays silent on Gaussian series (max gap under 10 points, the configured threshold).
- Event weighting versus pooling, hand-computed: a cell with 90 events in a ticker-year at baseline 0.20 and 10 at 0.60 has an event-weighted baseline of **0.24** and a pooled-over-days rate of **0.40**. Sixteen points on identical data.

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

## Session 12 — Cell grid and `cell_stats` (tasks 12.1-12.4)

Run 2026-08-11. `config_hash = 1835688bf7d760ba`, train split, `entry_kind = next_open`,
cluster heads only, forward window closed. 22,387 events in, 21,367 after exclusions:
217 null `dd_bucket`, 802 deep `dd_bucket` (ADR 101), 1 open forward window.

All six tasks complete. 12.5's migration is `e3c7f5a91d24`.

### The population filter, recovered by measurement

ADR 102 publishes `n`, `k_bar`, and `n_eff` but does not state the filter that produced
them. It is `is_cluster_head AND entry_kind = 'next_open'` with `fwd_window_days >= 6`.
Both filters are load-bearing and neither was written down: dropping the cluster-head
filter multiplies every `n` by roughly four, and dropping the entry-kind filter
multiplies it by four again. They are now named in `research/cell_stats.py` as
`GRID_CLUSTER_HEADS_ONLY` and `GRID_ENTRY_KIND`, and pinned by
`tests/integration/test_cell_grid_measured.py`.

### The twelve cells, at target 3%

`n`, `k_bar`, and `n_eff` reproduce ADR 102's table. `n` matches exactly on all twelve;
`k_bar` to within 0.05; `n_eff` to within 1 unit on three cells (74→73, 51→50, 40→39),
which is ADR 102 having reused its own rounded `k_bar` and `rho` rather than a
disagreement. The test tolerance is ±2 and is deliberately too tight to absorb a wrong
filter.

| Side | Signal | Bucket | n | n_eff | `p_hit` | base_emp | edge | CI | p | q |
|---|---|---|---|---|---|---|---|---|---|---|
| short | `bb_upper_touch` | 0-10 | 4,116 | 717 | 0.1288 | 0.1141 | +0.0147 | 0.106-0.155 | 0.109 | 0.769 |
| long | `bb_lower_touch` | 0-10 | 3,593 | 369 | 0.1887 | 0.1735 | +0.0152 | 0.152-0.232 | 0.220 | 0.769 |
| short | `stoch_overbought` | 0-10 | 5,266 | 322 | 0.1217 | 0.1213 | +0.0004 | 0.090-0.162 | 0.490 | 0.769 |
| short | `confluence_high` | 0-10 | 2,986 | 215 | 0.1045 | 0.1209 | −0.0164 | 0.070-0.153 | 0.770 | 0.805 |
| long | `stoch_oversold` | 0-10 | 1,444 | 147 | 0.1517 | 0.1654 | −0.0138 | 0.103-0.219 | 0.673 | 0.769 |
| long | `bb_lower_touch` | 10-20 | 775 | 73 | 0.2619 | 0.2167 | +0.0452 | 0.175-0.373 | 0.173 | 0.769 |
| long | `confluence_low` | 0-10 | 725 | 56 | 0.1738 | 0.1598 | +0.0140 | 0.096-0.293 | 0.388 | 0.769 |
| long | `stoch_oversold` | 10-20 | 900 | 50 | 0.2722 | 0.2169 | +0.0553 | 0.168-0.409 | 0.172 | 0.769 |
| short | `bb_upper_touch` | 10-20 | 432 | 48 | 0.1667 | 0.1778 | −0.0111 | 0.087-0.296 | 0.579 | 0.769 |
| long | `confluence_low` | 10-20 | 620 | 39 | 0.2484 | 0.2085 | +0.0399 | 0.140-0.402 | 0.269 | 0.769 |
| short | `stoch_overbought` | 10-20 | 371 | 14 | — | — | — | — | — | — |
| short | `confluence_high` | 10-20 | 139 | 5 | — | — | — | — | — | — |

Two cells suppressed, exactly the two ADR 102 predicts, at `n_eff` 14.3 and 5.0 against
a floor of 30. Neither is near the floor.

### Nothing survives multiple-testing correction

**The headline result of Session 12.** Across the 48-test family (12 cells x 4 targets),
the minimum q-value is **0.769**. One test reaches a raw p-value below 0.05: short
`bb_upper_touch` 0-10 at the 2% target, `p_hit` 0.2014 against a 0.1763 baseline, edge
+2.5 points, p = 0.039. It does not survive correction and is one test out of 48.

Every edge in the table above is inside its own confidence interval's width of zero. The
largest, +5.5 points on long `stoch_oversold` 10-20, sits on `n_eff` of 50 with an
interval spanning 0.168 to 0.409.

This is a train-split result under one config. It is not a kill-criterion trigger on its
own — the criterion reads "no cell beats baseline at sufficient `n_eff` after FDR", and
Session 13's benchmark arms are what put "sufficient" and "beats" on a footing. It
should be read now rather than after Session 13 makes it easier to explain away.

### Breadth terciles under ADR 104

Denominator re-measured as the **train** universe per quarter, superseding the
trade-universe values. 48 quarters, 7 to 496 distinct tickers, median 46. Breadth ratio
now runs 0.0020 to 1.0000 with **zero** values above 1, which is the boundary ADR 099's
denominator crossed.

Cut points, per era, on the empirical distribution:

| Era | low | mid | high |
|---|---|---|---|
| 2010-2014 | 0.0020-0.1149 | 0.1190-0.2581 | 0.2632-1.0000 |
| 2015-2019 | 0.0098-0.0986 | 0.0988-0.2105 | 0.2113-0.8732 |
| 2020-2023 | 0.0072-0.0928 | 0.0942-0.1884 | 0.1892-0.8866 |

`p_hit` at target 3% by tercile, the three cells where the split is most pronounced:

| Side | Signal | Bucket | low | mid | high |
|---|---|---|---|---|---|
| long | `stoch_oversold` | 10-20 | 0.2135 | 0.2085 | **0.4035** |
| long | `bb_lower_touch` | 0-10 | 0.1520 | 0.2096 | 0.2146 |
| short | `bb_upper_touch` | 0-10 | 0.1451 | 0.1116 | 0.0989 |

Long `stoch_oversold` 10-20 is the cell to watch, and not favourably. Its hit rate nearly
doubles in the high-breadth tercile, which is DESIGN §6.11's third reading: an apparent
edge concentrated on broad-selloff days is substantially market timing, and it fires
precisely when buy-and-hold is also buying cheaply. Session 13's three-arm comparison on
the high-breadth subset is what answers it.

### Per-ticker concentration: the cap does not bind

Measured for all twelve cells. **No cell exceeds the 15% threshold**, and none comes
close: the largest single-ticker share anywhere in the grid is ILMN at 3.6% of the short
`confluence_high` 10-20 cell (139 events across 103 tickers). HD is the most frequent
top contributor, never above 2.3%.

Session 12's brief expected the cap to bind, reasoning from 2015-2019 holding 140,288
event rows across 196 tickers. It does not, because the headline cells draw on 103 to 470
distinct tickers each after the cluster-head filter. The recomputed-without-top-ticker
statistics are therefore not reported for any cell, there being no cell that qualifies.
The NVDA-over-2020-2024 risk DESIGN §6.7 names is real in principle and absent here.

### Breadth rows cannot be stored, and are not

`cell_stats` has no breadth column and `cell_key()` has no breadth parameter, so the
"three rows per cell" the session brief describes for breadth have nowhere to go. They
are reported here as a measurement. Storing them would need a schema change and a tenth
`cell_key` parameter, neither of which is in Session 12's scope, and the tenth parameter
would change every existing `cell_id`.

### Rows written

192 rows under `config_hash = 1835688bf7d760ba`: 48 pooled (12 cells x 4 targets) and 144
era rows (12 x 4 x 3 eras). Era rows carry null `q_value`, asserted. No row carries era
2024+, asserted. Two runs over identical data produce identical values ignoring `run_id`
and `computed_at`.

`arm` is not written by the writer. The column arrived with 12.5 carrying
`DEFAULT 'signal'`, and all 192 rows took that value with no backfill, confirmed by
query.

### 12.5 — `arm` column and `v_screen` predicates

Migration `e3c7f5a91d24`, applied 2026-08-11. Research database only; serving was skipped
visibly (`DATABASE_URL_SERVING not set`).

`cell_stats.arm text NOT NULL DEFAULT 'signal'` with `cell_stats_arm_check` permitting
`signal`, `control`, `benchmark`. `v_screen` rebuilt via `CREATE OR REPLACE` (the output
column list is unchanged, so no drop window and no cascade) gaining two **`ON`-clause**
predicates:

```sql
AND c.config_hash = current_setting('capitalscan.default_config_hash', true)
AND c.arm         = 'signal'
```

They are in the `ON` clause, not `WHERE`. `cell_stats` is LEFT JOINed, so moving either
to `WHERE` converts it to an inner join and empties the screener. `v_screen` returns
683,653 rows before and after, which is the check that caught this being worth stating.

Reversibility verified by running it, not by reading `downgrade()`: `cscan db rollback
--yes` dropped the column and constraint and restored the original view body with the row
count unchanged and no orphaned objects, then `cscan db migrate` reapplied it.
`db/schema.sql` regenerated; `test_schema_drift.py` runs and passes; the holdout firewall
still passes.

### `v_screen` and `signal_strength` — found, decided, fixed

**Found 2026-08-11.** No `cell_stats` row Session 12 wrote could reach `v_screen`. The
view joined `c.signal_strength = e.signal_strength`, and ADR 102 removed
`signal_strength` as a grid dimension, so every Session 12 row carries NULL there and
`NULL = 1` is never true. Measured: 683,653 rows, **zero** non-null `cell_id`. The view
worked, returned rows, raised nothing, and showed no numbers.

Confirmed across all 626,791 events that strength is a pure function of `signal_type` —
six combinations, no exceptions, no strength-2 reachable — so the dimension ADR 011
assumed genuinely does not exist.

**Decided as ADR 107, fixed by migration `f1a8d3b62c07`.** The view now pins
`c.signal_strength IS NULL`, the exact parallel of the `c.era IS NULL` line below it.
Both select the pooled row. Dropping the condition outright was rejected because
`cell_id` embeds the strength slot, so a pooled row and a split row are distinct rows for
one cell and a condition-free view would match both and duplicate every screener row.
Populating the column with `3 if confluence else 1` was rejected because more trigger
families are planned, and a fourth primitive outside the confluence definition makes
strength 2 reachable, at which point a cell holds mixed strengths and the derived value
stamps it with one of them silently.

**Remaining, and by design.** `v_screen` still shows zero statistics, now for one reason
only: it pins `c.split_key = 'validate'` and Session 12 measured train. That is invariant
5b working. With the strength predicate corrected, the same join matches **35,281
events** under a train split, measured directly, so the split filter is the only thing
left holding it at zero.

ADR 102 was not amended. It removed strength as a *cut*, and that claim holds:
reinstating it would yield the same twelve cells, since every cell's events share one
strength value.

---

## Session 13 — Benchmark arms (tasks 13.1-13.7)

Run 2026-08-13. `config_hash = 1835688bf7d760ba`, `entry_kind = next_open`, cluster heads
only, long side, restricted to the trade universe on the signal date.

| Split | Window | Trading days | Tickers | Signal entries | `run_id` |
|---|---|---|---|---|---|
| train | 2010-03-31 to 2021-12-31 | 2,960 | 252 | 7,163 | `benchmarks_20260813T172347_f421521b` |
| validate | 2022-01-03 to 2023-12-29 | 501 | 214 | 2,254 | `benchmarks_20260813T173059_efceeadc` |

409 rows per split: 8 pooled arms, 200 pooled null replications, 3 subset arms, 200 subset
null replications. All ten gate items pass.

### The headline: the signal arm loses to both benchmarks, on both splits

| Split | Buy and hold | Signal | Null median | Null 97.5th | Verdict |
|---|---|---|---|---|---|
| train | **+383.66%** | +108.37% | +83.95% | **+205.77%** | below |
| validate | **−3.69%** | −10.10% | −13.99% | **+3.02%** | below |

The signal arm is **below the 97.5th percentile of its own randomization null on both
splits**, and below buy-and-hold on both. On train it does clear the null's *median*
(+108% against +84%), which is worth stating precisely: entry timing is very slightly
better than random, and nowhere near the threshold ADR 061 sets. On validate it does not
even clear the median.

This is the reading gate item 4 exists to record. It is not a favorable result and it is
published as measured.

### All eight arms, train split

| Arm | total_ret | ann_ret | Sharpe | max_dd | deployed | cap_eff | win | n | post_tax |
|---|---|---|---|---|---|---|---|---|---|
| `buy_hold` | +383.66% | 14.36% | 0.769 | 34.13% | **100.00%** | 3.837 | 53.95% | 784 | +291.40% |
| `signal` | +108.37% | 6.45% | 0.348 | 27.31% | 92.94% | 1.166 | 55.02% | 7,163 | −7.78% |
| `trim` | +202.39% | 9.88% | 0.756 | 22.45% | 100.00% | 2.024 | 53.95% | 7,059 | +155.93% |
| `dca_fixed` | +366.01% | 13.99% | — | 34.13% | 100.00% | 3.660 | — | 12 | +366.01% |
| `dca_signal` | +139.15% | 7.70% | — | 30.43% | 99.97% | 1.392 | — | 1,970 | +139.15% |
| `dca_hybrid` | +391.58% | 14.51% | — | 34.13% | 100.00% | 3.916 | — | 8 | +391.58% |
| `dca_lump` | +383.66% | 14.36% | — | 34.13% | 100.00% | 3.837 | — | 1 | +383.66% |
| `random` (median of 200) | +83.95% | — | — | — | 96.79% | — | 55.58% | 7,163 | — |

`dca_lump` and `buy_hold` agree to the cent on terminal value ($4,836,594.10 on train,
$963,113.14 on validate), which is
the cross-arm consistency check 13.4 asks for. They are the same strategy with a dollar
amount attached, and a disagreement would mean one of the two simulators is wrong.

### The signal arm is deployed 93% of the time, not 20%

DESIGN §6.4's "a signal firing on 4% of days with a 5-day hold sits in the market roughly
20% of the time" is a **per-ticker** statement. Across a ~62-name trade universe, 7,163
entries with a 5-day hold average about 12 concurrent positions over 2,960 days, so the
arm is in the market on 92.94% of them.

The consequence is that capital efficiency and total return are nearly the same number
for this arm (1.166 against 1.084), and the "needs 5× the annualized rate while deployed"
argument does not apply as written. The pooled comparison against buy-and-hold is close
to a straight return comparison, which is a harder test than §6.4 anticipated, not an
easier one.

### Why the null's median is far below buy-and-hold, and why that is not a bug

The 13.2 brief asks for a check that the null's median lands near buy-and-hold scaled by
`frac_deployed`. On train that predicts `3.837 × 0.968 = +371%` against a measured
**+84%**, which looks like a construction failure and is not one.

**The same heuristic predicts +357% for the signal arm, which returned +108%** on
identical exit machinery. A 4% target with a 5-day maximum hold truncates every winner,
so no entry arm can compound like a held position regardless of when it enters. The
heuristic measures the exit rules and fails for both arms together.

The null is verifiably built correctly on the checks that actually discriminate: every
one of the 200 replications opens **exactly 7,163 positions**, matching the signal arm's
count; median deployment 96.79% against the signal arm's 92.94%; median win rate 55.58%
against 55.02%. Only entry timing differs, which is what the null isolates.
`test_the_brief_heuristic_fails_for_the_signal_arm_too` pins the explanation, so if the
heuristic ever does start predicting the signal arm, the replacement check gets revisited.

### The null distribution

200 replications, seeded from `config_hash` (ADR 061). Train, pooled:

| Statistic | Value |
|---|---|
| min | −15.86% |
| median | +83.95% |
| mean | +90.65% |
| **97.5th percentile** | **+205.77%** |
| max | +328.55% |
| distinct values | 200 of 200 |

Two runs against `1835688bf7d760ba` reproduce all 200 replications identically; a
different `config_hash` produces a different null on all 20 replications tested. Both
directions are asserted, because a wall-clock seed breaks the first and a fixed constant
seed breaks the second, and both wrong versions run without complaint.

### Trim-and-redeploy loses to buy-and-hold, as it should in this window

| | Terminal | Sharpe | max_dd | Trims | Round trips | Avg days in cash | Never redeployed |
|---|---|---|---|---|---|---|---|
| train | +202.39% | 0.756 | 22.45% | 7,059 | 886 | 85.3 | 99 |
| validate | −1.34% | −0.148 | 14.58% | 1,526 | 247 | 52.1 | 70 |

Against buy-and-hold's +383.66% on train, trimming cost **181 points of return** and
bought 11.7 points of drawdown reduction (22.45% against 34.13%) at almost identical
Sharpe (0.756 against 0.769). Selling 20% into strength across a twelve-year bull market
is expensive, and the risk-adjusted case for it is a wash rather than a win.

On validate — a flat-to-down window — trimming helps: −1.34% against buy-and-hold's
−3.69%, with drawdown cut from 20.17% to 14.58%. That asymmetry is the honest summary of
ADR 017's variant: it is drawdown insurance with a large premium in a rising market.

**ADR 017's prediction is not tested by this.** Its expected ranking is trim-and-redeploy
above an unfiltered short, and no short arm was computed this session (ADR 058 surfaces
long only, and the short signal's role here is driving the trim). The ranking against
buy-and-hold is a separate comparison and ADR 017 never claimed it.

### A defect worth recording: the trim arm was not comparable at first

The first implementation gave the trim arm a static book of every ticker bought at first
appearance and held forever, while buy-and-hold rebalanced to the ~62 current members. It
measured **+725% against +413%** — a 312-point gap that was entirely the two arms holding
different books, and it read as trimming beating buy-and-hold decisively.

A static-membership unit test passed the whole time, because with fixed membership the
two constructions coincide. Both arms now run through one `core.arms.simulate_holdings`,
so the no-trim case *is* buy-and-hold, and the regression test varies membership.

### Four DCA variants, train split

| Variant | Terminal | IRR | Avg cost basis | Cash drag | Undeployed | Deployments |
|---|---|---|---|---|---|---|
| `dca_lump` | $4,836,600 | 14.36% | 1.0000 | 0.00% | $0 | 1 |
| `dca_hybrid` | $4,915,800 | 14.77% | 0.9839 | **−7.92%** | $0 | 8 |
| `dca_fixed` | $4,660,100 | 14.50% | 1.0379 | +17.65% | $0 | 12 |
| `dca_signal` | $2,392,100 | 15.65% | 2.0224 | +244.51% | $0 | 1,970 |

ADR 012 predicts "signal-timed accumulation beats fixed-schedule accumulation by a small
real margin, and both lose to lump sum." **Half of that is wrong and half holds.**

- Both schedules do lose to lump sum on terminal value, as predicted.
- Signal-timed accumulation does **not** beat fixed-schedule: $2.39M against $4.66M, a
  244-point cash drag against 18. Spreading tranches across twelve years at an average
  cost basis of 2.02× the day-one index is what a rising market does to any slow
  accumulation, and the signal fires often enough (1,970 deployment days) that its timing
  cannot recover the drag.
- `dca_signal` has the **highest IRR** of the four (15.65%) while having the lowest
  terminal value. That is not a contradiction: IRR is money-weighted and its capital
  arrives late, so it is measured against a shorter average holding period. Terminal value
  on equal total capital is the comparison ADR 012 asks for.
- `dca_hybrid` slightly **beats lump sum** (+7.92% of drag recovered), the only variant
  that does. Doubling the tranche on signal days front-loads deployment enough to nearly
  match lump sum while still averaging in.

Over a fourteen-year window `dca_fixed` deploys `C/12` monthly and is fully invested
after year one, so it converges toward `dca_lump` by construction. That is a property of
the rule DESIGN §6.6 specifies, not of this implementation, and it is why the two sit 17
points apart rather than the wide gap a one-year comparison would show.

**`capital_undeployed` is zero on both splits.** `N` is the train signal-day rate scaled
to the window (0.6655 deployment days per trading day), and validate realized 334
deployment days against an expected 333. The signal's firing rate is stable across the two
splits to within a third of a percent, so there is no underfiring to report. The
underfiring path is exercised by fixture instead.

### Pre-tax and post-tax (ADR 032, Provisional)

| Split | Arm | Pre-tax | Post-tax | Wash sales flagged |
|---|---|---|---|---|
| train | `buy_hold` | +383.66% | **+291.40%** | no |
| train | `trim` | +202.39% | **+155.93%** | no |
| train | `signal` | +108.37% | **−7.78%** | yes |
| train | `random` (mean) | +90.65% | −14.07% | yes |
| validate | `buy_hold` | −3.69% | **−9.09%** | no |
| validate | `signal` | −10.10% | −38.38% | yes |

**Short-term tax eliminates the signal arm's entire return.** +108.37% pre-tax becomes
−7.78% post-tax on train: twelve years of 7,163 short-term round trips at a 37% rate cost
116 points of the 108 the strategy made. The random-entry null shows the same pattern, so
this is a property of the turnover rather than of the signal.

Wash sales flag on the entry arms and not on the holding arms, which is the expected
shape: only high-turnover sleeve trading repurchases a name within 30 days of taking a
loss in it.

**Holding period decides the rate,** per the 2026-08-13 amendment to ADR 032. Positions
held more than a year pay 20%, everything else 37%. This was measured as a defect first
and fixed second: the initial run taxed every arm at 37%, which reported buy-and-hold at
`post_tax_ret = +239.22%` and trim at `+131.55%`. Their stints average roughly four and a
half years.

| Arm | Stints | Avg hold | Rate | Post-tax before | Post-tax after |
|---|---|---|---|---|---|
| `buy_hold` | 784 | ~4.5 yr | 20% | +239.22% | **+291.40%** |
| `trim` | 7,059 trims / 784 stints | ~4.5 yr | 20% | +131.55% | **+155.93%** |
| `signal` | 7,163 | ≤5 days | 37% | −7.78% | **−7.78%** |

**The correction moves the benchmarks and leaves the signal arm exactly where it was**,
which is the point: the signal arm's positions are genuinely short-term, so 37% was always
right for it. The post-tax gap against buy-and-hold widens from 247.0 points to **299.18
points**, wider than the 275.29-point pre-tax gap. Taxing everything at 37% had been
flattering the signal arm.

A pre-fix linear estimate put corrected buy-and-hold near +305%; the measured value is
+291.40%. The estimate scaled the whole tax bill by `20/37`, which ignores the per-year
netting, so it overshot by about 14 points. Recorded because the estimate was published
before the re-run.

The other stated gaps are in DESIGN §8.8 and are unchanged: deferred losses outstanding in
the final year are lost, `post_tax_ret` ignores the compounding cost of paying tax early,
rebalance partial disposals are untaxed, a deferred wash-sale loss keeps its own character
rather than inheriting the replacement lot's, and dividend reinvestment is not a separate
purchase.

### A second defect worth recording: the tax model

The tax model was wrong twice, in two rounds, and both were found by running it rather
than by reading it.

**Round one.** The first implementation pooled the whole window into one net figure and
treated a wash-sale disallowance as permanent. That produced **`post_tax_ret = −354%`
against `pre_tax_ret = +109%`** — a tax bill several times the account. Two things were
wrong: a 2021 loss cannot offset a 2011 gain, and a disallowed loss is deferred into the
replacement lot's basis rather than destroyed. Taxation became per calendar year with
one-year deferral.

**Round two.** With that fixed, every arm was still taxed at 37%, including benchmarks
holding for years. That understated buy-and-hold by 52 points and trim by 24, and it ran
**in the signal arm's favor**. ADR 032 named no long-term rate, so the fix was an
amendment to the ADR rather than a code change alone.

Both rounds carry their own tests. The pattern is worth naming: a tax number is plausible
across a wide range, so neither round announced itself, and both survived a passing test
suite until the arms were run against twelve years of real trades.

### The high-breadth subset (ADR 099) — the market-timing test

The direct test of whether the signal's edge is market timing. If it lives on
broad-selloff days it fires exactly when buy-and-hold is also buying cheaply.

| Split | Subset events | Buy and hold | Signal (subset) | Null 97.5th (subset) | Deployed |
|---|---|---|---|---|---|
| train | 2,346 | +383.66% | +126.04% | +248.70% | **33.50%** |
| validate | 744 | −3.69% | −7.09% | +19.42% | **44.91%** |

**The high-breadth subset is where the signal arm looks best, and it still loses to its
own null.** +126.04% against a subset null 97.5th percentile of +248.70% on train, and
−7.09% against +19.42% on validate. On
train the subset arm returns +126.04% on 33.50% deployment against the pooled arm's
+108.37% on 92.94% — a capital efficiency of 3.762 against 1.166, and a win rate of
58.27% against 55.02%. Restricting to broad-selloff days makes every per-unit-of-exposure
number better.

Read against DESIGN §6.11's three patterns, this is the **third**: the edge concentrates
at high breadth, which reads as substantially market timing. It is also the outcome that
carried forward from Session 12, where long `stoch_oversold` 10-20 nearly doubled its hit
rate in the high-breadth tercile (0.2135 / 0.2085 / 0.4035).

The subset comparison is reported **alongside** the pooled one, never instead of it, and
its buy-and-hold arm is identical to the pooled one to the last digit — same dates, same
universe, same number, which is what "restricted to the same dates" requires.

### What this means for the kill criteria

Session 12 found no cell surviving FDR correction (minimum q 0.769 across 48 tests).
Session 13 adds that the strategy the cells describe loses to buy-and-hold by 275 points
on train and to its own randomization null's 97.5th percentile on both splits, and that
short-term tax removes its entire pre-tax return.

These are two independent measurements pointing the same way. ADR 033's first kill
criterion is stated in terms of cells rather than arms, so this session does not fire it
by itself — but nothing here argues against it, and the holdout evaluation is what
settles it. **Holdout has not been touched.**

---

## ADR 108 — the close-confirmed reversal signal

Added 2026-08-13. `open > close AND close >= bb_upper[t-1]`: a down bar closing at or
above the **prior** upper band. Short side only.

**A new `config_hash`: `697f3ae71428d392`**, superseding `1835688bf7d760ba`. The old hash
is not retired — every Session 12 and Session 13 number above remains a valid measurement
of that config, and ADR 096's composite key on `cell_stats` means the two coexist rather
than one replacing the other.

### Why the hash had to move, and how close it came to not moving

ADR 108 states the new type forces a new `config_hash`, because `signal_strength` counts
concurrent types and shifts on every day the signal fires. **That did not happen on its
own.** `config_hash` hashes `dataclasses.asdict(Config)`, and a `SignalType` enum member
is not a `Config` field, so the hash stayed at `1835688bf7d760ba`.

`_RUN_BACKTEST_UPDATE_COLUMNS` includes `signal_types_all`, `signal_strength`, and
`cluster_id`. A backtest under the unchanged hash would have rewritten all 626,977 events
in place, and every table above — the twelve cells, the eight arms, the 200-replication
null — would have stopped reproducing from the database with nothing raising.

Caught by printing the hash before launching the run, not by review.
`SignalParams.enabled_signal_types` now names the enabled set, which makes the signal
selection a genuine config dimension and doubles as the ablation switch DESIGN §3.10 asks
for.

### Measured incidence

Full universe recompute, 20m08s at 8 workers, 612 tickers:

| Quantity | Value |
|---|---|
| Flagged bars, all history | **43,701** |
| Flagged bars, train split (short, cluster heads, `next_open`) | 409 |
| ...that also fire `confluence_high` | **286 (70%)** |
| ...that fire the reversal alone | 123 (30%) |

Verified by a lag join against the **prior** bar's band, with zero discrepancies on four
checks: none flagged without being a down bar, none flagged with a close below the prior
band, none flagged whose high failed to touch that band, and **none missed**. The third
check is the `bars_check1` subset guarantee — `close <= high` means a close at or above
the band implies the high was too, so every flagged bar necessarily also fires
`BB_UPPER_TOUCH`.

An earlier version of that query compared row *t*'s band rather than *t−1*'s and appeared
to show violations. The lag join is the correct check; the apparent violations were the
query's error, not the data's.

### Continuity: the confluence cells change, and why that is not drift

The grid goes from twelve cells to fourteen (ADR 102 as amended). The new cell takes the
286 overlapping events, because ADR 057 emits one row per ticker-day carrying the most
specific type and ADR 108 ranks the reversal above confluence.

| Cell | Session 12 `n` | Under the new hash | `n_eff` | Verdict |
|---|---|---|---|---|
| short `confluence_high` 0-10 | 2,986 | ~2,710 | ~195 vs 215 | renders, unchanged |
| short `confluence_high` 10-20 | 139 | ~131 | ~5 | suppressed, unchanged |

**Neither cell changes its render/suppress verdict.** `signal_types_all` still lists both
types on every affected row, so no event is lost — only the cell counting it moves. A
reader comparing the two tables should expect this difference.

The 70% overlap is real correlation rather than redundancy: a bar that *closes* above the
upper band has usually been running hot, so `%K` is already extreme by then.

### Backtest and statistics

Run 2026-08-14. Backtest `backtest_20260813T233051_a64c5401`, 627,380 rows, 590/616
tickers, **4h55m wall clock** (write phase 35m50s, harness ~4h19m — see the CLAUDE.md
correction, the documented 2h48m is 1.75x optimistic).

**All five harness checks pass**, `no_lookahead` among them. That one carries the load
here: ADR 108 widened the signature probe by one field and opened invariant 3's door by
one allowlisted column, and the independent shift-ladder check confirms neither
introduced look-ahead.

### The fourteen cells, train split, target 3%

| Side | Signal | Bucket | n | n_eff | `p_hit` | base | edge | q |
|---|---|---|---|---|---|---|---|---|
| short | `bb_upper_touch` | 0-10 | 4,006 | 719 | 0.1278 | 0.1138 | +0.0140 | 0.790 |
| long | `bb_lower_touch` | 0-10 | 3,593 | 369 | 0.1887 | 0.1735 | +0.0152 | 0.790 |
| short | `stoch_overbought` | 0-10 | 5,266 | 322 | 0.1217 | 0.1213 | +0.0004 | 0.790 |
| short | `confluence_high` | 0-10 | 2,710 | 202 | 0.1033 | 0.1208 | −0.0175 | 0.792 |
| **short** | **`bear_close_above_upper`** | **0-10** | **386** | **152** | **0.1295** | **0.1224** | **+0.0071** | **0.790** |
| long | `stoch_oversold` | 0-10 | 1,444 | 147 | 0.1517 | 0.1654 | −0.0137 | 0.792 |
| long | `bb_lower_touch` | 10-20 | 775 | 73 | 0.2619 | 0.2167 | +0.0452 | 0.790 |
| long | `confluence_low` | 0-10 | 725 | 56 | 0.1738 | 0.1598 | +0.0140 | 0.790 |
| long | `stoch_oversold` | 10-20 | 900 | 50 | 0.2722 | 0.2169 | +0.0553 | 0.790 |
| short | `bb_upper_touch` | 10-20 | 420 | 47 | 0.1667 | 0.1785 | −0.0118 | 0.790 |
| long | `confluence_low` | 10-20 | 620 | 39 | 0.2484 | 0.2085 | +0.0399 | 0.790 |
| short | `stoch_overbought` | 10-20 | 371 | 14 | — | — | — | — |
| **short** | **`bear_close_above_upper`** | **10-20** | **20** | **6** | — | — | — | — |
| short | `confluence_high` | 10-20 | 131 | 5 | — | — | — | — |

**The new signal's 0-10 cell renders** at `n_eff` 152, fifth highest of the fourteen and
well clear of the 30 floor. Its 10-20 cell suppresses at 20 events, which is the honest
outcome for a rare pattern inside a deep drawdown rather than a defect.

**Nothing survives FDR correction. Minimum q-value 0.790 across 56 tests**, and the new
signal's +0.71-point edge is comfortably inside noise. Session 12 measured q >= 0.769
across 48 tests; widening the family to 56 moved the correction slightly and changed the
conclusion not at all. This is the third independent measurement pointing the same way,
alongside Session 13's arms.

### Continuity predictions, verified exactly

ADR 102's amendment predicted the confluence reallocation *before* the run. Both landed
on the number:

| Cell | Session 12 | Predicted | Measured | Verdict |
|---|---|---|---|---|
| `confluence_high` 0-10 | 2,986 | ~2,710 | **2,710** | renders, `n_eff` 215 -> 202 |
| `confluence_high` 10-20 | 139 | ~131 | **131** | suppressed, unchanged |

### The arms under the new config

Runs `benchmarks_20260814T095544_04711f43` (train) and
`benchmarks_20260814T100207_764b175c` (validate), 409 rows each.

| Split | Buy and hold | Signal | Null median | Null 97.5th | Verdict |
|---|---|---|---|---|---|
| train | **+383.66%** | +108.37% | +84.44% | **+199.05%** | below |
| validate | **−3.69%** | −10.10% | −14.12% | **+5.58%** | below |

**Identical to Session 13's conclusion.** The signal arm sits below its own randomization
null's 97.5th percentile on both splits and below buy-and-hold on both. Every arm's
numbers are unchanged from `1835688bf7d760ba` to the digit, which is the expected result:
ADR 108 added a short-side *signal type*, and the three-arm comparison runs the long side
(ADR 058, ADR 017). The null threshold moved slightly (+205.77% -> +199.05%) because it is
reseeded from `config_hash`, which is exactly what ADR 061 specifies.

### `rho_era` under the new config

| Era | `rho_empirical` | `rho_factor_implied` | gap | pairs |
|---|---|---|---|---|
| 2010-2014 | 0.4259 | 0.4119 | +0.0140 | 127,383 |
| 2015-2019 | 0.3601 | 0.3340 | +0.0261 | 10,238 |
| 2020-2023 | 0.4707 | 0.4296 | +0.0411 | 20,581 |
| 2024+ | 0.2477 | 0.1615 | +0.0862 | 30,955 |

### The screener came back

`v_screen` returned 721,136 rows with **zero** statistics immediately after the GUC moved,
then 679,285 with statistics once `cell_stats` ran on the **validate** split. That gap is
invariant 5b working as designed — the view pins `c.split_key = 'validate'` and never
inherits an event's own split — and it is recorded because a zero there looks identical to
ADR 107's defect and is not it.

### A process finding worth keeping

`cscan path backfill` reprocesses every ticker on every run (`incomplete_only=False`),
while `cscan path capture` selects only tickers with incomplete events. For a fresh
`config_hash`, where every `fwd_window_days` is NULL, capture does exactly the needed work:
**7m31s against a projected three hours**, 2,210,695 path rows. The first attempt was run
as `backfill`, was killed by a tool timeout at 200/590 tickers, and on restart began
re-walking the 201 already-complete tickers from the top. `backfill` is not resumable in
practice even though its inner query supports it.

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
