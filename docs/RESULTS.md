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
`docs/sessions/session-9-backtest/hourly-residual-diagnosis.md`)
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
`docs/sessions/session-9-backtest/phase3-gate-measurement.md`
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
`docs/sessions/session-9-backtest/results-sweep-report.md`.
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

> **Resolved 2026-08-20. There was never a contradiction — only a missing
> label.** This table is `1835688bf7d760ba`, and it matches that config's
> stored `benchmarks` rows exactly: validate signal −10.10%, null 97.5th
> +3.02%. The live config `86e91448a65aa40b` gives +12.63% against +6.36%,
> and it too reproduces exactly — twice, across a full universe rebuild
> (2026-08-16 and 2026-08-20 runs agree to the cent).
>
> Two configs, two answers, both correct and both stored. The arms are
> deterministic; the appearance of disagreement came from quoting a number
> without the hash that produced it. **Every benchmark figure in this
> document now carries its `config_hash`, and that is the fix** — the
> alternative, editing this table to match the newer run, would have erased
> a measurement that was never wrong.
>
> The live config's numbers are recorded below under "Live config
> `86e91448a65aa40b`". Neither reading is an edge: on the live config the
> signal arm sits **below** its null on train (+81.68% against +216.70%)
> and the breadth_high split disagrees in sign on validate.

The signal arm is **below the 97.5th percentile of its own randomization null on both
splits**, and below buy-and-hold on both. On train it does clear the null's *median*
(+108% against +84%), which is worth stating precisely: entry timing is very slightly
better than random, and nowhere near the threshold ADR 061 sets. On validate it does not
even clear the median.

This is the reading gate item 4 exists to record. It is not a favorable result and it is
published as measured.

### Live config `86e91448a65aa40b`, measured 2026-08-20

The same eight arms under the config the system currently serves, after
ADR 129 (`in_trade` fails closed) and ADR 135 (a universe evaluation must
rest on data from inside its period).

| Split | Buy and hold | Signal | Null median | Null 97.5th | Verdict |
|---|---|---|---|---|---|
| train | **+392.97%** | +81.68% | +102.50% | **+216.70%** | below |
| validate | **−3.76%** | +12.63% | −12.45% | **+6.36%** | above |

The validate row is the only arm comparison in this project that clears
its null, and it should be read narrowly. It is one uncorrected test on
one split; the breadth_high split of the same run gives −6.45% against a
97.5th percentile of +23.61%, and train is far below on both eras. ADR
112's cell grid — which is FDR-corrected and is where an edge would have
to appear — reports **zero cells surviving on either split** after this
rebuild, minimum q 0.7604 train and 0.7061 validate.

ADR 135 moved buy-and-hold on train by +9.31 points (from +383.66%) by
removing a delisted position that had been held at a frozen 2018 price for
seven years. Validate's signal arm did not move at all, because the signal
arm trades events and no event changed — the structural claim in that ADR,
confirmed by measurement.

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

## Session 14 — Closing Phase 4

Run 2026-08-14, `config_hash = 697f3ae71428d392`. Sessions 11-13 built the statistics;
this session makes them readable and closes the Phase 4 gate.

### Artifacts

Eight files under `reports/phase4/`, all regenerable and all byte-identical across two
runs (verified by sha256):

| Artifact | Content |
|---|---|
| `three_arms_<hash>_{train,validate}.svg` | Equity curves, 200-replication band, summary table, verdict in words |
| `drawdown_slice_<hash>_{train,validate}.svg` + `.csv` | Edge vs drawdown bucket with confidence bands |
| `equity_curves_<hash>_{train,validate}.csv` | Tidy per-day series behind the arm chart |

Static SVG, no plotting dependency. `uv add` locks `.venv` against any running job and
this project always has one — the live poller was running when these landed.

### ADR 015's central claim, answered

ADR 015 hypothesizes that edge is "positive and stable in the first two buckets and turns
negative past 35%," and calls that cut "potentially worth more than either indicator."

**Every one of the eleven rendered intervals crosses zero.** Train split, target 3%:

| Side | Signal | 0-10 edge | 10-20 edge |
|---|---|---|---|
| long | `bb_lower_touch` | +0.0152 | +0.0452 |
| long | `stoch_oversold` | −0.0138 | +0.0553 |
| long | `confluence_low` | +0.0140 | +0.0399 |
| short | `bb_upper_touch` | +0.0140 | −0.0118 |
| short | `stoch_overbought` | +0.0004 | — |
| short | `confluence_high` | −0.0175 | — |
| short | `bear_close_above_upper` | +0.0071 | — |

Widest interval: `stoch_oversold` 10-20 at [−0.0486, +0.1919]. Narrowest:
`bb_upper_touch` 0-10 at [−0.0084, +0.0404]. Not one excludes zero.

**Two findings, and the second is the one worth keeping.**

First, the hypothesis is untestable as stated. Its second half — "turns negative past
35%" — needs the 20-35 and 35+ buckets, and ADR 101 suppressed both permanently after
measuring them at `n_eff` far below the floor. The slice renders them as suppressed with
their `n_eff` visible rather than omitting them, because the measurement is the argument
for the cut and hiding it makes the cut look arbitrary.

Second, the long side runs **opposite** to "stable": all three long signals show a
*larger* point estimate in 10-20 than in 0-10 (+0.0452 vs +0.0152, +0.0553 vs −0.0138,
+0.0399 vs +0.0140). If that were real it would say deeper drawdowns are better for
longs, inverting the ADR's "trade shallow dips, stand down on deep ones." It is not real
at this sample: every one of those intervals spans zero, and `n_eff` in the 10-20 bucket
runs 39 to 73 against 147 to 719 in 0-10. The pattern is what a smaller sample looks like,
not a signal. Recorded because a reader looking only at point estimates would reach the
opposite conclusion from the right one.

### The three-arm chart states its own verdict

Rendered on the chart in words rather than left to be inferred from line positions:

> Verdict: signal (108.37% total return) did NOT clear the null's 97.5th percentile
> (199.05%) on train.

### Volatility-scaled reachability (DESIGN §6.12)

Reported alongside the fixed 2/3/5/10% ladder and deliberately outside the
Benjamini-Hochberg family — DESIGN §6.12 names fixed as the headline because it matches
how a limit order is placed, and adding four scaled targets per cell would take the family
from 56 tests to 112 for a diagnostic. Same treatment era and breadth already receive.

`sigma_5d = rv_20d * sqrt(5/252)`, derived through `core.baselines.horizon_drift_vol_array`
rather than restated, so a horizon change cannot leave a stale divisor behind. Measured on
validate, `bb_lower_touch` long 0-10: 0.5σ 61.2%, 1.0σ 38.2%, 1.5σ 22.6% over 2,920
observations — a monotone decay, which is the shape a correct scaled ladder has.

### Three defects found by running things rather than reading them

**`peak_ret_5d` was NULL on all 627,380 rows.** `cscan path peak-labels` had never run
against this config, so the scaled ladder returned `n_obs = 0` everywhere. A missing step
in the session chain, not a code defect. 284,080 rows populated in 36 seconds.

**A defect fifteen passing tests could not catch.** Neither
`load_events_for_reachability` nor `combined_reachability_table` called
`attach_scaled_targets`, so the end-to-end path raised a `KeyError` deep inside a groupby
while every unit test passed — each built its own frame with the columns already present.
`combined_reachability_table` now raises a message naming the missing call, and refuses to
attach the targets itself: it holds no `BaselineParams`, and sigma scaling is exactly where
a wrong horizon divisor hides.

**Two crashes waiting on suppressed cells.** In the drawdown slice, `r` was bound both by
a comprehension over `SliceRow` and by a `next(..., None)`, leaving the `None` case
unguarded on a path that indexes attributes; and the interval draw checked `edge is not
None` while reading `edge_ci_low` and `edge_ci_high`, which are independently nullable on a
suppressed cell. Both surfaced only when mypy ran at whole-repo scope.

### A verification error of my own

The curve export was first checked with `last / first - 1` and reported a failure. Wrong
formula: row *i* is equity at the *end* of day *i*, with the 1.0 base preceding row 1, so
`total_ret = last - 1`. Real deltas are 1e-16 against a 1e-9 requirement.

Worth recording because of *how* it misleads. `buy_hold` agrees under both formulas —
`simulate_buy_hold` sets `equity[1] = equity[0]` on day 0, since nothing is held yet — and
only `signal` diverges, because `simulate_portfolio` can earn a day-0 return. Check one
arm, see it agree, trust the other. The CSV header now states the convention.

---

## ADR 109 — the close-confirmed band is the same day's

Amended 2026-08-14, one day after ADR 108. `open > close AND close >= bb_upper[t]`: the
shift to `[t-1]` is dropped.

**A new `config_hash`: `541f84a384b07ba2`**, superseding `697f3ae71428d392`. Every number
in the Session 14 section above, and every `bear_close_above_upper` figure in the ADR 108
section, describes the **superseded** rule. Both hashes coexist under ADR 096's composite
key, and `bear_close_band_lag=1` (`3f9b74da68e4573e`) reconstructs ADR 108's population
from a config rather than from a database snapshot, which matters because `indicators`
carries no `config_hash` column and stores one generation only.

### How it was found

Not by a test. The user compared fired tickers against Yahoo Finance charts and reported
that NTRS, DELL, PANW, NTAP, CAH, and HPE did not match. Every test in the suite asserted
the shifted behaviour it had been written alongside, so the suite, the five-check harness,
and the Session 14 gate all passed while the rule disagreed with the world.

The mechanism: a gap-up day that fades still raises its own 20-day band, so the close
lands under the band as drawn while clearing the prior day's. Roughly 55% of the old
rule's fires were of this shape.

HPE on 2026-08-10 set the tolerance question. Open 55.40, close 54.68, same-day upper
54.6788 — a margin of 0.0012 after 4-decimal rounding. The user's call was that touching
counts, which `_breach`'s `>=` already gave. It fires.

### Measured, 2026-08-03 to 2026-08-14

| Stage | Old rule | New rule |
|---|---|---|
| `open > close` and close at or above the upper band | 151 | 74 |
| ...and `confluence_high` also fires | 109 | 53 |
| ...and ticker is `in_trade` | — | 13 |

Dropped 56, gained 0. The zero is a property of this window rather than of the rule: the
same-day band is stricter only while the band rises, and on a falling band the new rule
would fire where the old stayed silent.

Nine of nine user-checked tickers now agree with the charted band.

### Why the hash needed a field invented for it

Third occurrence of the same trap. `config_hash` hashes `dataclasses.asdict(Config)`, and
ADR 109 changed a *formula* in `core/indicators.py`. A formula is not a `Config` field, so
the hash held at `697f3ae71428d392` and a backtest would have overwritten the Session 14
measurements in place, silently. `IndicatorParams.bear_close_band_lag` exists for that
reason alone, after `stoch_source` and ADR 108's `enabled_signal_types` reached the same
failure by different routes.

The three pinned-hash guards in `test_cli_config_resolution.py` and
`test_events_backtest_config_agreement.py` caught the move in CI, which is what they are
for.

### Open

Two display sites read a value the signal no longer uses. `core/exits.py:161` reads
`k_full` with no `exit_stoch_source` field to follow a `stoch_source` swap, and the scan
CSV prints `t-1` band columns beside a same-day verdict, so `bb_pctb < 1` appears next to
firing rows. Neither affects detection.

---

## ADR 110 — the raw %K becomes the trigger

Flipped 2026-08-16. `stoch_source = "k_fast"`, `require_fast_agreement = True`.

**A new `config_hash`: `86e91448a65aa40b`**, superseding `1b97abf7e458d537`. No code changed: both are `SignalParams` fields that `core/signals.py` already honoured.

### The intermediate hashes, and why there are three

Three identities were minted in three days, and only the first two have measurements:

| Hash | Change | Measured? |
|---|---|---|
| `697f3ae71428d392` | ADR 108, close-confirmed signal | Session 14, full |
| `541f84a384b07ba2` | ADR 109, same-day band | **Detection only** — `cscan events` ran, no backtest |
| `1b97abf7e458d537` | `exit_stoch_source` field added | Full backtest, 627,668 events, 5/5 harness PASS |
| `86e91448a65aa40b` | ADR 110, this one | Chain running |

`541f84a384b07ba2` holds 157,168 events with **zero** returns, MFE, or peak labels. That is not a defect: `cscan events` detects signals and stops, and only `cscan backtest` computes outcomes. It is a detection-only generation and should not be read as a measured result.

`1b97abf7e458d537` is the usable k_full baseline. `exit_stoch_source` defaults to `k_full`, the value `core/exits.py` previously hardcoded, so its numbers are what `541f84a384b07ba2` would have produced had it been backtested.

### What the flip does, on the one sample measured so far

Bear-reversal rows, universe members, 2026-08-03 to 2026-08-14:

| Rule | Rows |
|---|---|
| `k_full >= 80` (old) | 13 |
| `k_fast >= 80`, no agreement | 15 |
| **`k_fast >= 80` and gap <= 5** | **7** |

The column swap *widens* the set. The agreement check does all the narrowing, and the rows it removes share a shape: APH gap 9.5, AVGO 10.5, DAL 10.3, HPE 9.3, UAL 8.0. `k_full` is `k_fast` smoothed, so a wide gap means %K is accelerating — the check is strictest exactly where momentum is sharpest.

That is a deliberate trade, not a correctness fix, and it is worth re-reading against outcomes once this hash has a backtest.

### An operational failure worth recording

The 2026-08-15 chain ran in the wrong order and lost most of a day:

1. `path backfill` ran **before** the backtest, spent 2h46m, and wrote 61,174,645 rows.
2. `path peak-labels` reported `rows_updated=0`.
3. The backtest then wrote its own events with fresh ids, orphaning all of step 1.

`path` keys on `event_id` with an FK to `events`, and `run_backtest` mints the event rows itself — 627,668 across four entry kinds, a strict superset of what `cscan events` writes. So the backtest must come first, and `cscan events` is redundant before one.

Compounding it, uncommitted `config.py` edits were in the working tree when that chain launched, so it resolved to a hash neither party intended. `runs.params` recorded the full resolved config, which is the only reason it was diagnosable after the fact.

The chain is now a script (`scripts/run_chain_86e91448.ps1`) with the ordering constraint written into a comment beside the steps.

---

## ADR 110 measured — the k_fast run, `86e91448a65aa40b`

Chain run 2026-08-16, 03:12 to 11:41 PT. Backtest, `path backfill`, `path peak-labels`, then the four `stats` commands.

| Step | Duration | Rows |
|---|---|---|
| backtest | **4h50m23s** | 630,592 events, 591/616 tickers |
| `path backfill` | 2h56m58s | 67,433,412 |
| `path peak-labels` | 51.9s | 285,997 |
| `stats rho` / `cells` / `benchmarks` / `self-validate` | ~10m | — |

Harness: **5/5 PASS** (`no_lookahead`, `entry_sanity`, `exit_sanity`, `return_identity`, `non_overlap`).

The 4h50m is wall clock. `runs` recorded 32m55s for the same job, because `cli.py::backtest` closes its `run_job` block before calling `run_harness` — the harness is untimed anywhere, and the only record of that figure was a log file.

### What the flip did to the population

Comparing like with like on `entry_kind = 'touch'`, against `1b97abf7e458d537` (the k_full baseline):

| Signal type | k_full | k_fast + agreement | Δ |
|---|---|---|---|
| `stoch_overbought` | 43,904 | 43,469 | −435 |
| **`confluence_high`** | **32,034** | **18,964** | **−13,070** |
| `stoch_oversold` | 22,545 | 23,711 | +1,166 |
| **`confluence_low`** | **19,701** | **10,346** | **−9,355** |
| `bb_lower_touch` | 18,924 | 28,279 | **+9,355** |
| `bb_upper_touch` | 17,268 | 30,338 | **+13,070** |
| `bear_close_above_upper` | 2,541 | 2,541 | 0 |

**The reconciliation is exact**, and that is the whole story. `confluence_high` loses 13,070 and `bb_upper_touch` gains exactly 13,070; `confluence_low` loses 9,355 and `bb_lower_touch` gains exactly 9,355. Nothing disappeared. The agreement check stripped confluence status and those bars fell to the next rank in `_SPECIFICITY`. Total events barely moved, 156,917 → 157,648.

The band conditions did not change at all, which is worth stating because the table above invites the opposite reading. `bb_upper_touch` appears in `signal_types_all` on **51,840** rows under both configs, and `bb_lower_touch` on **38,625** under both. Identical to the row. Only the `signal_type` *label* migrated.

`bear_close_above_upper` holding at exactly 2,541 is the control: it never consults %K, so any movement there would have meant something leaked.

### Benchmark arms

| Split | Era | Arm | Return | Null p50 | Null p97.5 | |
|---|---|---|---|---|---|---|
| train | pooled | buy_hold | **+383.66%** | | | |
| train | pooled | signal | +85.75% | +102.50% | +216.70% | below median |
| train | pooled | trim | +157.70% | | | |
| train | breadth_high | signal | +302.77% | +106.72% | +324.82% | above median |
| validate | pooled | buy_hold | −3.69% | | | |
| **validate** | **pooled** | **signal** | **+12.63%** | −12.45% | **+6.36%** | **above p97.5** |
| validate | breadth_high | signal | −6.45% | −8.45% | +23.61% | above median |

**The signal arm clears its own randomization null on validate for the first time.** Under `697f3ae71428d392` it was below the null on both splits.

**This should not be read as an edge.** It performs *better out-of-sample than in-sample*, which is backwards from what a real effect looks like — a genuine edge shows up strongest in training. Validate's pooled sample also just halved, and this is the pattern noise produces when a sample gets small. The train split, with four times the data, has the signal arm below the null's median and 298 points behind buy-and-hold.

### Cell grid

| Split | Cells | Suppressed | Mean `n_eff` | Min q | Significant after FDR |
|---|---|---|---|---|---|
| train | 224 | **100** | 93.3 | 0.8492 | **0** |
| validate | 224 | **168** | 20.8 | 0.7061 | **0** |

**Zero cells survive FDR on either split.** The minimum q-value is 0.849 on train and 0.706 on validate, against an α of 0.05.

Suppression rose exactly as the halved confluence sample predicted. Train went 88 → 100 suppressed against `697f3ae71428d392`; validate held at 168 because it was already near the floor, with mean `n_eff` slipping 22.0 → 20.8.

### Self-validation

`stats self-validate` **PASS**: null test 2/480 cells at q < 0.05 = 0.42% against a 5% threshold, recovery test gap 0.039 pp against a 1.0 pp tolerance, and the deliberately-broken variant (standard error on raw `n` instead of `n_eff`) caught at 11.67%. The machinery is sound, so the negative result above is a measurement rather than an artifact.

### The reading

Two measurements disagree in direction, and the disagreement is informative. The benchmark arm says validate cleared its null; the cell grid says nothing is significant anywhere. When an aggregate looks better than every one of its components, the aggregate is usually the artifact — the arm is a single number against 200 replications, while the grid corrects across 224 tests.

Three configs have now been measured end to end, and none has produced a cell that survives correction. ADR 033 fixed the kill criteria in advance for exactly this situation.

---

## `path` pruned to four generations, 2026-08-17

**54,941,515 rows removed in 4m40s**, leaving 12.5M. `events` was not touched.

### Why

`path backfill` is not config-scoped. It walks every event of every generation, so the last full run processed 5,568,263 events and took **2h56m** — roughly 80% of it rebuilding forward paths for configs nothing reads. Twenty-three generations had accumulated at ~3.1M path rows each.

### What was kept, and on what test

Only three hashes appear in `cell_stats`, `benchmarks`, or `rho_era`. A fourth was kept as a comparison baseline:

| Hash | Path rows | Kept because |
|---|---|---|
| `86e91448a65aa40b` | 3,135,524 | live (ADR 110) |
| `1b97abf7e458d537` | 3,127,486 | the k_full baseline ADR 110 is measured against |
| `697f3ae71428d392` | 3,127,236 | Session 14 published; all three stats tables |
| `1835688bf7d760ba` | 3,126,340 | Sessions 12/13 published; all three stats tables |

The other nineteen — seventeen sweep runs at 3,101,579 rows each, plus `3e598c59e7d71eae` and `541f84a384b07ba2` — had zero rows in every statistics table.

### Why `path` and not `events`

`path` is **derived**: every row is recomputable from `events` + `bars` by `cscan path backfill`, so this is reversible at the cost of recompute.

`events` is the primary record and regenerating one generation costs a five-hour backtest. It also carries three layers, not one — detection, entry resolution, and forward outcome labels — which is why generations cannot share rows and why ADR 096's composite key exists to let them coexist. All 23 remain, 13,479,752 rows, so every published number stays auditable and any two populations can still be compared at the event level.

That comparison is not hypothetical: proving the ADR 110 flip left the band conditions untouched required querying `signal_types_all` on both `1b97abf7e458d537` and `86e91448a65aa40b` and finding 51,840 rows on each side. Deleting either generation's events would have made that check impossible.

### The vacuum, and a trap worth recording

`DELETE` marks rows dead; it does not free them. Until `VACUUM` ran, all 54.9M deleted rows were still physically present and still scanned, so the prune delivered nothing on its own.

Two `VACUUM` attempts failed silently first:

```
ERROR: could not resize shared memory segment ... No space left on device
```

`VACUUM` parallelises index cleanup under `max_parallel_maintenance_workers`, which the `max_parallel_workers_per_gather` workaround in `CLAUDE.md` does not touch. `VACUUM (PARALLEL 0, ANALYZE) path;` succeeded.

**Both failures exited 0.** The error goes to stderr, so a filtered invocation swallows it and the command looks successful. They were caught only because `last_vacuum` was still NULL with 57,343,972 dead tuples. `CLAUDE.md` now says to verify maintenance against `pg_stat_user_tables` rather than the exit code.

After the vacuum: `n_dead_tup` 0, statistics refreshed. On-disk size stays 13 GB by design — plain `VACUUM` makes pages reusable rather than returning them to the OS, and `path` grows back into that space on the next backfill.

---

## Session 15 — the handler layer, 2026-08-18

**This session produced no measurements about the market, and the note says
so rather than being omitted.** An absent session reads as an incomplete
one. What it produced is a contract: seven functions between the database
and every future surface, each of which either returns a complete
statistical claim or refuses to return one.

Run: no backtest, no statistics job, no ingest. One migration.

### What was built

| | |
|---|---|
| `capitalscan/handlers/` | 10 modules: `types`, `validate`, `enums`, `errors`, `_db`, and one per tool |
| Tools | `screen_signals`, `get_stats`, `get_indicators`, `get_events`, `predict`, `explain_signal`, `get_universe` |
| Tests added | 209 unit, 11 integration |
| Migration | `d7f4b91c26ea` — `serving_config` and a rebuilt `v_positions` |
| ADRs | 114 (screener columns), 115 (`v_positions` reads a settings row) |
| Fast tier after | 1,644 → 1,853 passing, `core/` coverage 95.83% |

### The shape ADR 112 forced

Nothing here is pessimism about the product. It is what the measurement
says, expressed as types.

| Measured (ADR 112) | Consequence in the layer |
|---|---|
| 100 of 224 train cells suppress at `n_eff < 30` | `Suppressed` is the *common* return of `get_stats`, not an edge case, and it carries no probability field at all |
| 0 of 124 unsuppressed cells survive FDR | `CellStats.survives_fdr` is `False` on every cell that returns; the flag is computed once here rather than by three consumers |
| Minimum q-value 0.8492 train / 0.7061 validate | The q-value renders **alongside** the hit rate, never in place of it. A "not significant" label is less informative than the number |
| Every edge interval spans zero | `edge` is treated as a probability by the validator, so it cannot be returned without its interval |

ADR 114 follows from the first two rows. The screener's default is the event
feed; the statistical fields sit behind `with_stats=True`, and in that mode
`cell_stats` is **not queried at all** rather than queried and hidden.

### `v_positions`, rebuilt (ADR 095 → ADR 115)

Deferred out of Phase 4 by ADR 095 because Phase 4 never read the view.
Phase 5 does.

Five defects, four named by ADR 095 and one found while writing the parity
fixture:

| Was | Now |
|---|---|
| `k_full >= (80)::numeric` | `serving_config.exit_stoch_threshold` |
| `(CURRENT_DATE - entry_date) >= 5` | `serving_config.max_hold_days`, counted in sessions |
| Long threshold applied to shorts | `exit_stoch_threshold_short` when `side = 'short'` |
| `k_full` regardless of policy | The column `exit_stoch_source` names (ADR 110) |
| `exit_signal_mid_band` always published | NULL unless `exit_on_mid_band` |
| `days_held` in calendar days | `trading_days` count |

The last one is the one nobody had written down. `max_hold_days` counts
bars — `core/exits.py` walks `fwd_bars` and `holding_days` is `exit_idx +
1` — while the view counted calendar days. A Thursday entry read 4 calendar
days and 2 sessions on the following Monday, so `exit_signal_timeout` fired
a session early over every weekend and two early over a holiday weekend.
Nothing consumed the flag yet, so nothing was wrong in production; it would
have been wrong the day `/positions` was rebuilt.

ADR 115 reverses ADR 095's stated preference. ADR 095 preferred generating
the view's DDL from `ExitParams` in the migration; that still bakes `80`
into the database, `pg_dump` still writes it into `db/schema.sql`, and
`threshold_lint.KNOWN_EXCEPTIONS` would have had to exempt it forever. The
settings row leaves no literal in any checked-in SQL, so the exemption was
**deleted** on 2026-08-18 — which is what ADR 095's own note asked for.

### Measured while building

**`v_ticker_state` costs 26.5 s.** Materializing it once, 612 tickers,
`max_parallel_workers_per_gather = 0`, on the developer database
(4.5M+ `indicators` rows). With parallelism on it fails outright:
`could not resize shared memory segment ... No space left on device`.

Every `SELECT ... FROM v_positions` pays that, which is why
`test_v_positions_config.py` takes minutes locally and seconds in CI, where
the container is empty.

> **Corrected 2026-08-18, ADR 116.** The sentence that stood here said
> "nothing pushes the position's ticker down through the view's `DISTINCT
> ON`", generalising from one measurement of the *whole* view. Postgres
> does push a **constant** predicate down through `DISTINCT ON`: a
> single-ticker read measured **17 ms** all along. What it cannot push is a
> **correlated** one, which is why `v_positions` - joining on `p.ticker` -
> paid the full 24.5 s to return one row, and why the ticker page was never
> the query at risk.
>
> ADR 116 then rewrote the view as a loose index scan and the distinction
> stopped mattering: the whole view is **27 ms**, `v_positions` **23.5 ms**,
> a single ticker **1.4 ms**. The instruction to measure before building an
> interactive page stands; the number it was reacting to is gone.

**748 `order_intents` rows on the live research database.** Found while
writing the integration test. `test_positions.py` truncates `positions`
and `order_intents` around every test, and `TRUNCATE positions CASCADE`
would have taken all 748. That convention is safe on a CI container built
from migrations and unsafe on a developer database; the new module records
the ids it creates and deletes exactly those. **The existing module was not
changed** — that is a separate decision about whether those rows matter, and
it belongs to whoever knows what they are.

### `runs` now times the whole backtest

`cli.py::backtest` closed its `with ingest.run_job(...)` block before
calling `run_harness`, so `runs.finished_at - started_at` measured the write
phase alone. The 2026-08-13 full-universe run measured **4h55m by wall clock
and 32m55s in `runs`**, and a 2026-08-09 session read those durations as the
whole job and briefly "corrected" `CLAUDE.md` to ~36 minutes on the strength
of them.

The harness now runs inside the block. Two consequences, both intended: a
failing harness marks the run `failed` rather than `ok`, and ADR 059's sweep
gate reads `status = 'ok'` — so a sweep can no longer start on top of a run
whose harness failed. The harness outcome also lands in `runs.notes`.

Durations recorded before 2026-08-18 remain write-phase-only and should be
read that way.

### Defects found by running things

1. **`explain_signal` accepted `split='holdout'`** whenever `target_pct` was
   omitted, because the split was validated inside the conditional branch
   that looked up the cell. A refusal that depends on another argument is
   not a refusal. Found by `test_handlers_contract.py` on its first run,
   which is why that test parameterizes over `inspect.signature` rather than
   over a list of handlers someone expected to check.
2. **`v_positions.days_held`**, above.
3. **`test_threshold_lint.py` counted findings**, so it broke the moment a
   docstring quoted the defect it described. Replaced with two assertions
   that derive from the exception list in both directions: every entry still
   matches something, and every finding is on the list.

### What Session 16 inherits

Seven handlers with stable signatures, typed results that serialize
cleanly, the validator callable on its own (`handlers.validate.validate`),
and `handlers.enums` as the single source for MCP tool schemas. ADR 027
requires the MCP server to add no query logic; if it needs to, the handler
contract was wrong and the fix belongs here.

---

## Session 16 — the MCP server, 2026-08-18

Like Session 15, this session measures nothing about the market. It makes
the seven handlers reachable from a chat client, authenticated, rate
limited, and read-only at the connection level.

Run: no backtest, no statistics job, no ingest, no migration. One
dependency added.

### What was built

| | |
|---|---|
| `capitalscan/mcp/` | `tools`, `server`, `auth`, `ratelimit`, `serialize`, `errors` |
| Transport | Streamable HTTP at `/mcp`, plus an authenticated `/health` |
| CLI | `cscan mcp serve`, `cscan mcp tools`, `cscan db grant-readonly` |
| Tests added | 98 unit, 23 integration |
| Dependency | `mcp>=1.2.0`, resolved to 2.0.0 (brings `starlette`, `uvicorn`) |
| Docs | `docs/MCP_SETUP.md` |

### Verified end to end against the live database

2026-08-18, config `86e91448a65aa40b`, server on `127.0.0.1:8799`:

| Step | Result |
|---|---|
| `tools/list` with no token | **401**, `{"error": "unauthorized"}` |
| `initialize` | 200, `capitalscan 0.2.0` |
| `tools/list` | 200, all seven names |
| `tools/call get_stats` | 200, a real measured answer |
| `tools/call` with `split=holdout` | refused |

The `get_stats` call asked for `confluence_low`, 3%, bucket `0-10`, validate
split, and got back:

```json
{"kind": "suppressed",
 "cell_id": "confluence_low|long|0-10|all|next_open|validate|pooled|h5|t0.03",
 "reason": "n_eff 12.8 below min_n_eff 30",
 "n_events": 61, "n_eff": 13, "min_n_eff": 30,
 "meta": {"config_hash": "86e91448a65aa40b", "as_of": "2026-08-17",
          "staleness_days": 1, "run_id": "cell_stats_20260816T183226_2c0047c4",
          "split": "validate", "stale": false}}
```

**That is the expected answer, not a failure.** 61 events collapse to an
effective sample of 13 after the clustering correction, against a floor of
30. ADR 112 measured 100 of 224 train cells in that state, so a suppression
is the *common* return of `get_stats`, and the wire shape says so with a
`kind` tag rather than with nulls.

### The read-only role, proven rather than granted

`cscan db grant-readonly` provisions `capscan_ro`: `CONNECT`, `USAGE` on
`public`, `SELECT` on every table and view, and nothing else. Measured
directly on 2026-08-18:

```
SELECT count(*) FROM tickers;                      -> 712
INSERT INTO tickers (ticker, name) VALUES (...);   -> ERROR: permission denied
```

`test_mcp_readonly_role.py` runs INSERT, UPDATE, DELETE, CREATE TABLE, DROP
TABLE, `nextval`, and CREATE ROLE through the role's own connection and
asserts each refusal. Session 16's gate calls this one of the two items that
matter, and the reason it is a behavioural test rather than a grant-table
query is that those two come apart the first time an ownership or default
privilege gets in between.

**A detail worth recording.** DDL and DML are refused differently. An INSERT
without a grant says `permission denied for table ...`; a `DROP TABLE` says
`must be owner of table ...`, because no grant confers DROP in Postgres at
all. A test asserting only the first passed on four of five rows and failed
on the fifth for exactly the right reason.

### Defects found by running things

**1. The transport was mounted inside another Starlette app.** Its session
manager starts in the app's *lifespan*, and `Starlette.mount` never forwards
lifespan to a sub-app. The server authenticated correctly, accepted the
request, and failed every `initialize` with `RuntimeError: Task group is not
initialized. Make sure to use run().` — a message that reads like an SDK bug
rather than an assembly mistake. Fixed by wrapping the transport app
directly in plain ASGI middleware, which pass non-HTTP scopes straight
through.

No unit test in this repository would have caught it. That is why
`test_mcp_server_live.py` exists and why it uses `TestClient` as a context
manager.

**2. `Invalid Host header`.** The SDK compares `Host` against an allowlist
and answers `421 Misdirected Request` on a miss, a DNS-rebinding guard whose
default accepts `127.0.0.1` and `localhost`. It surfaced as a wall of 421s
in the integration tests, and it is the same setting a deployment behind a
domain has to pass. `build_app(allowed_hosts=[...])` now exposes it, and
`MCP_SETUP.md` §3 says so before anyone spends an afternoon on a routing
diagram.

### Holdout is refused twice, and the order matters

The generated schema types `split` as a two-member literal, so a request for
holdout fails validation *before* any handler runs. The handler's raise is
still there, still carrying ADR 019's reasoning, and still the only guard on
the web and chat surfaces, which have no schema in front of them.

The consequence is that an MCP client sees a pydantic validation error
rather than the ADR text. That is why the `get_stats` tool description says
in prose that holdout is refused and why: a model reading the schema learns
it is not an option, and a model reading the description learns the reason.

### What ADR 112 looks like on the wire

The server's `instructions` are sent to every client on connect and name the
result directly: 630,592 events, three signal definitions, no cell surviving
Benjamini-Hochberg correction on either split, minimum q-value 0.706, and
roughly 45% of cells reporting nothing.

That is Phase 5 gate item 8 — "ADR 112's result is visible on every surface
that reports a statistic" — discharged for this surface. A client that never
opens the documentation still receives it.

### What Session 17 inherits

Nothing structural: the routes call the same handlers, not the MCP server.
Two things worth carrying over anyway.

- **The union tag.** `{"kind": "suppressed"}` and `{"kind": "not_found"}`
  are how a client tells "we cannot say" from "it never happened". A web
  route rendering the same union needs the same distinction, and it is
  already decided.
- **`v_ticker_state` no longer costs 26.5 s** (ADR 116, same day). It is
  27 ms for the whole view and 1.4 ms for one ticker, and the Session 15
  note that framed this as a ticker-page problem was wrong about which
  query was slow.

---

## RESOLVED: the benchmark record and the database never disagreed, 2026-08-20

**Both numbers were correct; neither carried its `config_hash`.** Verified
against the stored `benchmarks` rows: `RESULTS.md`'s table reproduces
`1835688bf7d760ba` exactly (−10.10% / +3.02%), and the live
`86e91448a65aa40b` reproduces exactly too, twice, across a full universe
rebuild (2026-08-16 and 2026-08-20 agree to the cent).

So the arms are deterministic and nothing needed re-measuring. Every
benchmark figure in this document now names the config that produced it,
and the Session 13 table carries a resolved note pointing at the live
config's section. The heading below is kept as written so the
investigation is legible; read it as the account of a question that turned
out to have a clerical answer.

---

### The original entry, 2026-08-19

**Found by building `/research`.** The page renders the signal arm against
its randomization null and put validate at the **100th percentile**.
`RESULTS.md` §Session 13 records the opposite.

| | `RESULTS.md` | Database now |
|---|---|---|
| Signal, validate | −10.10% | **+12.63%** |
| Null 97.5th, validate | +3.02% | **+6.36%** |
| Signal, train | +108.37% | +85.75% |
| Null 97.5th, train | +205.77% | +216.70% |
| Verdict, validate | below | **above** |

They are different runs under different configs:

```
benchmarks_20260813T172347   1835688bf7d760ba   2026-08-13
benchmarks_20260814T095544   697f3ae71428d392   2026-08-14
benchmarks_20260816T183906   86e91448a65aa40b   2026-08-16   ← live
```

`RESULTS.md`'s table was written from the 08-13 run. The live config's
benchmark run is 08-16 and nothing updated the record.

**Why this is not a rendering decision.** The page reports what the
database holds, which is the only thing it can honestly do. Deciding which
number is right means understanding what moved between `1835688bf7d760ba`
and `86e91448a65aa40b` — the config changes in that window include ADR
108's seventh signal type and ADR 110's `require_fast_agreement`, either of
which changes the event population the arms trade.

**The page says so rather than picking one.** A `.conflict` banner renders
whenever the signal arm exceeds the null's 97.5th percentile, naming
`RESULTS.md` and saying the discrepancy is unresolved.

**What would settle it.** Re-run `cscan benchmarks` under the live config
and compare against the 08-16 rows. If they reproduce, `RESULTS.md`'s table
is stale and should be re-measured with the config hash recorded beside it.
If they do not, something is non-deterministic in the arms and that is a
much larger finding.

**Not to be resolved by editing `RESULTS.md` to match the database.** The
record is the account of what was measured; changing it to agree with a
later run erases the fact that the result moved.

**Related, and possibly the same cause.** ADR 112's kill criterion fired on
`cell_stats`, which the live config's rebuild did *not* move — the digest
is byte-identical before and after ADR 122. So the cells are stable across
the config change and the arms are not, which is itself worth understanding
before trusting either.

---

## Overnight 2026-08-19 — what using the pages found

Session 17 closed on its gate the night before. Everything here came from
*operating* the result rather than building more of it, which is the honest
explanation for five ADRs and seven defects in one night.

### The rebuild, and the verification that mattered

`cscan events` re-detected 2010-01-01 → 2026-08-18 under ADR 122's change.

| | |
|---|---|
| Duration | **178 min** |
| Rows written | **1,312,935** (8.4x the gated 157,168) |
| Bars in window | 2,386,193 across 625 tickers |
| Null-indicator bars skipped | 2,824 |

**Nothing measured moved**, which was the entire risk:

| Check | Before | After |
|---|---|---|
| `cell_stats` digest | `96af3a8dd09438c4c62cc162fdc0fdff` | identical |
| `cell_stats` live cells | 448 | 448 |
| `v_screen` | 40,819 | 40,819 |

1,154,851 out-of-trade events now exist and no statistic changed.

**It holds no locks while it works.** Measured mid-run: the connection was
`idle`, last query `ROLLBACK`, 8,658 seconds earlier. All the time is
Python on one core. The all-or-nothing failure mode is the real hazard —
every hit is held in memory until a single final upsert, so an interrupt
leaves zero rows. `scripts/rebuild_events_chunked.py` runs one year per
call for next time.

### ADR 122 — 481 of 622 tickers had no events at all

Building `/ticker/[sym]` made a ten-year-old behaviour visible. SMCI's
event history stops at **2010-03-24** under a chart showing price clearing
its upper band repeatedly through 2026.

| SMCI since 2024-01-01 | |
|---|---|
| Bars | 659 |
| Lower-band touches | 72 |
| Upper-band touches | 120 |
| Events stored | **0** |

It has never been in the trade universe: 0 of 66 quarterly snapshots.

**The 2010 block is an artifact of a fail-open default.** The earliest
`universe` snapshot is 2010-03-31 and `core.universe.in_trade` returns
`True` when no evaluation exists on or before the bar — the documented v1
simplification. Every bar before that date passed a filter with nothing to
evaluate. **187 tickers** have a dense block of 2010 Q1 events and nothing
after, and **17,919 events — 11.4% of the live config's `touch` rows across
512 tickers** — entered the training population through a vacuous check.

**That 11.4% is recorded and not acted on.** Closing the branch drops them
and moves every measured cell. The user's decision.

### The guarantee moved from one place to eighteen

Before ADR 122, `events` could not contain an out-of-trade row, so no
consumer needed a predicate and none had one. After it, **eighteen reads**
widen by roughly 4.4x without one — and nothing about the output looks
wrong. `cell_stats` returns more events per cell, `n_eff` rises, and every
number stays internally consistent.

`jobs/compute.py::scan` was the trap: its docstring named the skip as the
reason it accepted a `universe` argument without filtering on it, so
`cscan scan` would have started listing untradeable names silently.

`test_events_in_trade_filter.py` **immediately caught a false pass** in
itself: the file-level sweep matched `jobs/views.py` on `v_ticker_state`'s
projected `u.in_trade` column rather than on any predicate. The four
serving views are now asserted one at a time, including the two that must
*not* filter — adding it to `v_chart` is the obvious thing for someone to
do later and would undo the whole ADR.

### ADR 125 — CI caught what a developer machine cannot

Migrations imported DDL constants from `jobs/views.py`. The constant is the
*current* definition; a migration is a statement about one point in
history. ADR 122 added `events.in_trade`, and four migrations that run
before it began emitting `AND e.in_trade` against a table without the
column. Every from-scratch replay died:

```
ProgrammingError: column e.in_trade does not exist
```

**Invisible locally by construction.** A developer applies only the new
migrations, so the broken path is never taken. Only a replay from empty
hits it — CI, and any new deployment. It ran green locally and failed on
the first CI run.

Fixed in all seven, not the four that broke: `a3c8e15d40b7`,
`c4a7e91b53d8` and `d7f4b91c26ea` were the same defect waiting on the next
edit to `v_ticker_state`, `v_chart` or `v_positions`.

**Verified by replay, not by reading.** Scratch database, `alembic upgrade
head`, md5 over `pg_get_viewdef` for every view against production:
`fac6d6b13ea438277600a88f8d6dfc0e` both ways. Then `downgrade -4` and back
up, same digest. That comparison is the only check that proves the chain
reproduces the live schema.

### ADR 124 — 19 fires became 4 overnight

`v_screen_live` filtered `is_cluster_head IS NOT FALSE`: right intraday,
wrong the next morning. The poller cannot cluster (ADR 054's gap window
needs the whole session) so its rows carry NULL and all pass; `cscan
events` clusters overnight and repeats become `false`.

Measured Thursday 2026-08-06: **19 confluence fires, 4 heads, 15 repeats.**
A reader watching live saw 19 and came back to 4.

ADR 054 is untouched — clustering is a measurement device and `v_screen`,
`cell_stats` and the benchmarks keep it. A feed is not a sample.

### A poller CSV and the screener are not comparable across a config change

Looks like data loss and is not. The 2026-08-05 CSV records **30
confluences**; the live config finds **13** on the same bars.

ADR 110's `require_fast_agreement` refuses to call a bar a confluence when
raw and smoothed %K differ by more than `fast_agreement_tol` = 5. Nineteen
of the thirty were that wide:

| Ticker | %K fast | %K slow | Gap |
|---|---|---|---|
| NUE | 99.1 | 81.8 | 17.3 |
| CSCO | 98.9 | 83.3 | 15.6 |
| HLT | 2.9 | 18.3 | 15.4 |

Both records are honest. The CSV is a decision made at a moment under a
rule; the page recomputes under the current one. This is why `config_hash`
is part of the natural key rather than metadata.

### The first poll of the day is a backlog, not a burst

Asked whether signals cluster at the open. They do not; the *first tick*
does. 2026-08-13:

```
09:30:43   63 fires    ← first tick
09:36:18    9
09:41:21    1
…singles for the rest of the session
```

The first poll evaluates every ticker and catches everyone already outside
their band. The poller then debounces, so later ticks only catch new
crossings.

### Seven defects, each found by using a page

**Every chart date was one session early.** `v_chart.ts` is a `timestamptz`
at UTC midnight; `pg` builds a `Date` whose *local* getters read the
previous day. The event history beside it read a real `date` and was
correct — two panels one day apart, no error anywhere. Fixed by casting
`::date` in the queries, the only place the two cases stay distinguishable.

**`?range=__proto__` reached `LIMIT $2`.** `in` walks the prototype chain.

**The empty state named the wrong date.** `lastFire()` read the trailing
view, so the one line whose job is to say when something last happened
would have been the one line that was wrong.

**`next start` could not find the database.** `.env.local` is at the repo
root and Next only looks in its own directory.

**A 500 on every `/` render**, from passing a function as a prop to a
client component. `tsc` was happy — the prop type is a function and the
value is a function. `next build` was happy — it compiles, and the fault
only exists once a render serializes. The component tests use
`renderToStaticMarkup`, which never serializes.

**The guard for it took three attempts, and that is the finding.** The
first matched `<Name[^>]*`, and `[^>]*` stops at the `>` inside `=>`. The
second built the regex in a template literal where `\b` is a backspace
character. **Both passed against the exact code they were written to
catch.** The third is verified by re-injecting the bad line, watching it
fail, and restoring it. Two green suites in a row had already lied.

**Two accidental `cscan events` runs** fired from backticks in a
`git commit -m` string being command-substituted by bash. Harmless — 835
rows over a five-day window, upserted to the same values by the rebuild —
and it produced the first live proof of ADR 122: 218 out-of-trade events on
a single date. Every commit message since uses a heredoc.

### BUG, logged and deliberately not fixed: poller timestamps are 4 hours early

`poll.py::_now_et()` returns a **naive** datetime holding ET wall-clock,
written to `signal_reports.fired_at` and `quotes_live.ts` — both
`timestamptz` — on a database running `Etc/UTC`.

One moment, two tables:

```
runs.started_at          2026-08-13 13:30:41+00   (SQL wrote it)
signal_reports.fired_at  2026-08-13 09:30:43+00   (the poller did)
```

Two seconds apart in reality, four hours apart in storage.

The screener's "Fired" column renders `clock()` in ET over an already-ET
value, so it shows 05:30 for a 09:30 fire. The CSV is right only by
accident: `wait_and_poll.ps1` subtracts 3 hours, which happens to undo
ET→PT, so fixing the poller without fixing the script makes the CSV wrong.

Not fixed because `_now_et` also feeds session timing and a naive/aware
mismatch throws, with the poller starting at 06:30. Needs an ADR: it
changes what a stored timestamp means, and there are ~800 `quotes_live`
rows and a few hundred `signal_reports` to decide about.

---

## Session 17 — screener and ticker page, 2026-08-19

Both routes render against the live database. Gate items 1 through 12, with
the two that did not pass stated as such rather than rounded up.

### Ticker page load time, measured (gate 11)

`next start`, production build, warm process, `curl` wall clock. Five
tickers, three requests each.

| Route | Median | Range |
|---|---|---|
| `/ticker/[sym]?range=1y` | **43 ms** | 36-90 ms |
| `/ticker/[sym]?range=5y` | 82 ms | one sample |
| `/` (screener) | 256 ms | 256-549 ms |

The ticker page runs three queries: `v_ticker_state` at **0.5 ms**,
`v_chart` at **9.7 ms** for 275 bars with markers, and `v_events` for the
history. ADR 116's rewrite is why the first number is what it is; the
Session 15 note that framed the ticker page as the query at risk was wrong
about which query was slow, and the page is now the *fast* one.

**The screener is six times slower than the ticker page, and one query is
all of it.** `SELECT max(signal_date) FROM v_screen_live` measured **871 ms**.
The view carries four `LEFT JOIN LATERAL` subqueries and Postgres does not
drop an unused one, so `max()` evaluates all four over every row in the view
before aggregating. Rewriting it as `ORDER BY signal_date DESC LIMIT 1` —
same answer — took it to **265 ms**, which is the whole difference between
the 850 ms screener and the 256 ms one.

265 ms is still most of the screener's remaining cost and the cause is the
same laterals. **Not fixed here.** The fix is an index on
`events (config_hash, entry_kind, signal_date)` or a narrower view for the
date, and both are migrations with a decision attached rather than a query
rewrite. Recorded so the next person measuring the screener does not
rediscover it.

### ADR 120 — `v_chart` was returning 3.5 rows per bar

Building the chart was the first time anything read the view. Measured on
TSM's last 400 sessions: **963 rows for 275 trading days.**

| Cause | Measurement |
|---|---|
| No `config_hash` predicate on the events join | 22 configs, so a bar with an event joined 22 times |
| `entry_kind = 'next_open'` | Newest marker 2026-08-13 against events through 2026-08-18 |
| `AND e.is_cluster_head` | Drops every poller row, which carries NULL |
| A bar can carry two events | **116 dates** hold both a long and a short head |

The first three are ADR 119's three predicates, arriving one view later. The
fourth is only a chart's problem and is why the marker columns are now
arrays: a series library keyed on time silently keeps the last of a
duplicate key, so the 22x duplication would have rendered as a chart that
looked entirely correct and drew whichever config's marker sorted last.

After: **275 rows for 275 days**, markers current through 2026-08-18, and
ADBE 2016-06-22 is one row carrying `{bb_lower_touch, stoch_overbought}`
with sides `{long, short}`.

### Defects found by running things

Each of these was found with a green suite in front of it.

**Every chart date was one session early.** `v_chart.ts` is a `timestamptz`
stored at UTC midnight; `pg` builds a `Date` whose *local* getters read the
previous day anywhere west of Greenwich. Measured in Pacific:
`2026-08-18T00:00:00Z` came back as `2026-08-17`. So the chart drew every
bar and every marker one session left of where it belonged, while the event
history beside it read `signal_date` — a real `date` — and was correct. Two
panels one day apart, no error anywhere. The same bug reached the shipped
screener through `band_ts` and the ticker state through `as_of`. Fixed by
casting `::date` in all three queries, which is the only place the two cases
are distinguishable: a `Date` object does not remember which type produced
it.

**`parseRange` accepted `__proto__`.** `raw in RANGES` walks the prototype
chain, so `?range=__proto__` and `?range=toString` both passed validation
and handed an object or a function to `LIMIT $2`. `Object.hasOwn` now.
Caught by a test, not by review.

**The empty state pointed at the wrong view.** `lastFire()` read `v_screen`,
which filters `entry_kind = 'next_open'` and trails the last backtest. On a
quiet day it would have said "last fire 2026-08-13" while the feed's own
view held events from 2026-08-18 — the one line on the page whose job is to
say when something last happened would have been the one line that was
wrong. It reads `v_screen_live` now.

**`next start` could not find the database.** The repository keeps one
`.env.local`, at the root, and Next only looks inside its own directory.
Every page rendered the error state saying neither variable was set, which
looks like a broken query and is a missing path. `next.config.ts` reads the
root file now, without overwriting anything already in the environment.

### What is tested and what is not

145 tests in `web/`, up from 15.

- **63 boundary tests.** No `web/` module imports `sqlalchemy` or `db_io`
  (gate 2), none shells out or calls the MCP server, none names `holdout`
  or `split_key` (gate 9), none interpolates anything into SQL, and
  `TickerChart.tsx` is the only client component.
- **35 state tests.** The staleness banner at 1, 2 and 3 days (gate 7 — it
  is *above* 2, so exactly 2 is fresh), the empty state with and without a
  last fire (gate 6), a suppressed cell rendering its reason and no number
  (gate 4), a hit rate rendering with `n_eff`, the interval and `q=0.849`
  unrounded (gate 5), and determinism: six components render byte-identical
  twice and contain nothing that depends on today's date (gate 10).
- **12 live-database tests**, skipped when no connection string is present,
  which is how CI runs. One row per session on three tickers, every marker
  landing on a real signal date on three tickers (17.3's acceptance), the
  state's as-of matching the newest bar drawn, and both `%K` series carrying
  values that are not the same column read twice.

### The two gate items that did not pass

**"A delisted ticker renders its history and says it is delisted" cannot be
satisfied against this database.** All 96 inactive tickers either carry no
bars at all (87 of them) or carry three to four bars from 2026-08-12 with no
indicators (9 of them: UA, FB, FISV, HUBB, NKTR, PCLN, PCS, Q, CPWR). There
is no delisted ticker with history to render. `v_ticker_state` returns
nothing for every one of them, so the page shows its not-found state, and
that state deliberately does not claim the symbol is invalid — it cannot
tell an unknown symbol from a known one with no indicators.

Two things follow, neither this session's to fix. Those nine 2026-08 bars on
symbols delisted years ago are a data-quality question about symbol reuse.
And `tickers.delisted_on` is NULL on all 96 rows while `is_active` is false,
so the date a listing ended is not recorded anywhere.

**Both were fixed on 2026-08-20**, which this paragraph predates. `is_active`
and `delisted_on` are now derived from each ticker's `last_bar`: **18 names
carry a delisting date**, HUBB and Q were re-admitted as live listings, and
the 19 junk bars behind the symbol-reuse question were deleted with the fetch
guarded against writing more. 99 tickers are inactive as of that date. The
paragraph above is kept as the measurement that prompted it — ADR 129's note
that `delisted_on` is "written nowhere in the tree" was true when written and
is not now.

**Statistics on the ticker page are absent, not hidden.** ADR 114 puts the
screener's statistics one action away; the ticker page has no equivalent
because `cell_stats` has no ticker dimension (ADR 102, ADR 104). A per-cell
panel would report the same numbers the screener already shows for the same
signal type and bucket. Left out rather than duplicated.

---

## ADR 143 measured — the expanded universe, 2026-08-21

**ADR 112 holds a third time.** Zero cells survive FDR correction on either
split, now over a universe 43% larger than the one that produced the
original result.

| | measured | survive FDR | min q | avg n_eff |
|---|---|---|---|---|
| train, `bbc99a02ebdc999f` (378 tickers) | 48 | **0** | 0.7167 | 265 |
| train, `f66729c7eda212a4` (540 tickers) | 48 | **0** | **0.5727** | 265 |
| validate, `bbc99a02ebdc999f` | 28 | **0** | 0.7327 | 45 |
| validate, `f66729c7eda212a4` | 28 | **0** | 0.7402 | 41 |

Population: **540 tickers against 378**, and **195,911 in-trade `next_open`
events against 139,387**. `n_pairs` in the correlation estimate roughly
doubled, 31,599 to 55,916 in the 2024+ era.

**The minimum q-value fell from 0.7167 to 0.5727 on train and did not move
on validate.** That is the shape of noise, not of signal: a 43% larger
population moves the best-looking cell's q by a quarter on one split and
0.007 the wrong way on the other, while `fdr_alpha` is 0.05 -- still more
than eleven times away. Nothing crossed, nothing approached crossing.

**`n_eff` is flat at 265 on train and *fell* on validate, 45 to 41.** More
tickers did not buy more independent information. Pooled rho runs 0.34 to
0.46 across the eras, and adding names to the same market on the same days
adds correlated observations -- the validate split has fewer years for those
correlations to average out, which is why it went down rather than up.

**Grid is 16 cells / 64 tests**, up from 14 / 56 (ADR 144 added the long
side's close-confirmed type). Two of the sixteen cannot fire while
`bull_close_below_lower` is dormant; empty cells suppress and BH corrects
over measured cells, so 48 and 28 measured are unchanged from the previous
config.

**Provenance.** The compute phase ran as 38 resumable chunks in 61 minutes
(783,644 rows, 540 tickers, zero chunk failures). `--phase finalize`
corrected `cofire_count` on all 783,644 -- the chunked writes had stored
undercounts capped near the chunk size, 8 where 110 tickers actually
co-fired, which would have inflated `n_eff` roughly fourteenfold. Verified
afterwards: zero mismatched groups. The path backfill was killed twice by
external process termination and resumed both times without loss, being
idempotent on `fwd_window_days`.

---

## ADR 142 re-measured against ADR 112 — 2026-08-21

**ADR 112 holds.** Zero cells survive FDR correction on either split under
the widened fast/full agreement rule.

| | train | | validate | |
|---|---|---|---|---|
| | measured | survive | measured | survive |
| ADR 112 (`86e91448a65aa40b`) | 48 | **0** | 28 | **0** |
| ADR 142 (`bbc99a02ebdc999f`) | 48 | **0** | 28 | **0** |
| min q, train | 0.7604 | | 0.7167 | |
| min q, validate | 0.7061 | | 0.7327 | |

Both minima sit roughly fourteen times `fdr_alpha = 0.05`. The result is not
an artifact of ADR 044's tolerance: it now holds under two different
definitions of when the two %K columns agree.

**The benchmark arms agree.** `signal total_ret = +0.8067` against the
200-replication randomization null's 97.5th percentile of `+2.2684` — at or
below the null, 409 rows written.

**What the population did.** ADR 142 relabelled 10,172 short and 6,606 long
events from a bare band touch to a confluence, +59% and +71% on the two
confluence populations, with the row count unchanged (139,387 `next_open`
against 139,253 before — the rule renames events rather than creating them).
Average events per confluence cell went 471 -> 736.

**And effective sample size barely moved: `n_eff` 256 -> 265, +3.5%.**

That gap is the finding worth keeping. A 60% larger confluence population
bought 3.5% more independent information, because the new events co-fire on
the same days across the same names as the ones already there — exactly what
`rho_bar` is measured to account for. Pooled ρ for this config: 0.4516
(2010-2014), 0.3618 (2015-2019), 0.4707 (2020-2023), 0.2438 (2024+). Raw `n`
would have suggested the grid gained real power. It did not.

**Provenance.** The backtest under this hash was killed at 01:18 PT when the
`capitalscan-postgres` container exited 255 mid-harness; the write phase had
completed and crash recovery lost nothing (139,387 rows, all four entry
grains at identical counts). The validation harness did not run, which does
not bear on the numbers above — ADR 059's harness gates sweeping and
hand-inspection, while these come from `cell_stats` and `benchmarks`. The
`runs` row records the kill and the reason.

---

## ADR 116 — `v_ticker_state` rewritten, 2026-08-18

Not a session. A performance fix taken on its own because Sessions 17 and 18
are blocked on a stack decision and this one is stack-independent: ADR 076
makes views the shared contract, so a view's cost is a cost every consumer
pays regardless of who serves the routes.

### Measured

`max_parallel_workers_per_gather = 0`, developer database, 2,912,426 daily
indicator rows, 612 tickers.

| Query | Before | After | |
|---|---|---|---|
| `SELECT count(*) FROM v_ticker_state` | 23.8 s | **27 ms** | 880x |
| `SELECT * FROM v_positions WHERE id = 44` | 24.5 s | **23.5 ms** | 1040x |
| `SELECT * FROM v_ticker_state WHERE ticker = 'TSM'` | 17 ms | **1.4 ms** | 12x |

Index build: 4.7 s. Migration `a3c8e15d40b7`, reversible.

### The Session 15 claim this corrects

`RESULTS.md`'s Session 15 note said "nothing pushes the position's ticker
down through the view's `DISTINCT ON`", and repeated it into `BUILD.md`,
`TESTS.md`, `sessions/README.md`, Session 17's plan, and a test docstring.

It generalised from a single measurement of the **whole** view. Postgres
pushes a *constant* predicate down through `DISTINCT ON` perfectly well —
the single-ticker read was 17 ms all along. What it cannot push is a
*correlated* one, which is why `v_positions`, joining on `p.ticker`, paid
24.5 s to return one row. **The ticker page was never the query at risk**,
which is the opposite of what five documents said.

All six places are corrected in place rather than quietly edited, because
the wrong version had already been used to plan Session 17.

### Two rejected attempts, both measured first

**A LATERAL over the unchanged view.** `LEFT JOIN LATERAL (SELECT * FROM
v_ticker_state WHERE ticker = p.ticker)` produced a byte-identical plan at
22.7 s. A correlated subquery with no volatile function and no `LIMIT` gets
pulled up and the lateral flattened away. The `LIMIT` in the shipped
version prevents that *and* makes the scan stop early — it is load-bearing
twice.

**Joins inside a `DISTINCT ON`.** Correct, and only 1.7x faster at 13.7 s:
it kept the 2.9M-row join to `bars` and added an external merge sort of
74 MB to disk. This one nearly shipped on the strength of a 62x measurement
that had actually been taken against a *different* variant — one that
dropped the `bars` join entirely and would have changed behaviour. Measuring
the thing you are about to ship, rather than the thing you measured earlier,
is the lesson.

### Equivalence

`tests/integration/test_v_ticker_state_rewrite.py` rebuilds the pre-116 view
from `jobs/views.py::V_TICKER_STATE_DDL_PRE_116` under a second name and
diffs both directions with `EXCEPT` over whole rows: **zero differences
across all 612**. It also re-derives each row's expected `as_of` from
`indicators` and `bars` independently, so the suite does not merely confirm
that two views agree with each other.

The rewrite keeps an `EXISTS (bars ...)` filter that currently never
excludes anything — zero of 2,912,426 daily indicator rows lack a bar. It
is kept because the old view's inner join *would* have fallen through to
the next-newest row in that case, `indicators` has no foreign key to
`bars`, and a behaviour change justified by a measurement that happens to
hold today is how this class of defect arrives.

---

---

## ADR 145 re-measured against ADR 112 — 2026-08-21

**ADR 112 holds a fourth time.** Zero cells survive FDR correction on either
split, on a universe rebuilt with market cap priced on a single split basis.

| | train | | validate | |
|---|---|---|---|---|
| | scored | survive | scored | survive |
| ADR 112 (`86e91448a65aa40b`) | 48 | **0** | 28 | **0** |
| ADR 142 (`bbc99a02ebdc999f`) | 48 | **0** | 28 | **0** |
| ADR 145 (`f66729c7eda212a4`) | 48 | **0** | 28 | **0** |
| min q | 0.6030 | | 0.7154 | |

The scored count is identical across all three measurements, so the comparison
is like-for-like rather than a shifting denominator.

**What changed underneath it.** `crit_mcap` had been comparing a
split-adjusted close against an as-filed share count (ADR 145). Correcting it
moved `in_trade` from 5,736 to **6,325** quarters (+10.3%) and `events` from
783,762 to **865,984** (+10.5%), with the gains concentrated in mega-caps
regaining early history — AAPL from 2012 back to 2010, plus AMZN, BKNG, ORLY,
KLAC, NVDA. The best cell is `bb_lower_touch` in the 10-20% drawdown bucket at
p_hit 0.3644 against a 0.3182 baseline, and `n_events = 1213` collapses to
`n_eff = 120` under a pooled rho of 0.23-0.46.

**The validation harness passed against this config for the first time.**
All five checks — no-look-ahead, entry sanity, exit sanity, return identity,
non-overlap — in 3h58m35s over 865,984 events and 543 tickers. Session 19 had
shipped `f66729c7eda212a4` without ever running it, because the harness was
reachable only through the unresumable `--phase all`.

### The benchmark arms, and one result that disagrees

Train agrees with the cell grid. Validate does not, and the disagreement is
recorded here rather than smoothed over.

| arm, pooled | train | validate |
|---|---|---|
| buy_hold | 4.4656 | -0.0322 |
| dca_hybrid | 4.6554 | 0.0786 |
| **signal** | **2.7113** | **0.1318** |
| trim | 2.3075 | 0.0066 |
| dca_signal | 1.8098 | 0.1224 |
| random null, mean | 1.5959 | -0.1110 |
| random null, 97.5th pct | 2.7489 | 0.0224 |

**On train** the signal arm returns 2.7113 against a null 97.5th percentile of
2.7489 — below it, and roughly 60% of buy-and-hold over the same window. That
is the session 13 finding, unchanged.

**On validate the signal arm sits at the 100th percentile of the null**, above
all 200 replications, and beats buy-and-hold. Four things bound what that can
be taken to mean:

1. **The same split disagrees with itself.** `breadth_high` on validate puts
   the signal arm at the **40.5th** percentile with a return of -0.1492. ADR
   099's breadth split reverses the sign, which is the behaviour the /research
   page already renders both rows for.
2. **Validate is 2022-01-03 to 2023-12-29** and buy-and-hold returned
   **-0.0322** across it. A strategy holding cash for most of a declining
   window outperforms on exposure, not on prediction. The null controls entry
   *count* per ticker-year, not time in market.
3. **It was not the pre-registered hypothesis.** The cell grid was, and it
   finds nothing at q <= 0.05. Reading an extreme value out of four arm-by-era
   comparisons after the fact is the multiple-comparisons problem ADR 112 exists
   to apply, not an exemption from it.
4. **The cell grid on the same split still finds nothing** — 0 of 28, min q
   0.7154. If entry timing carried information on validate, the conditional hit
   rates are where it should have appeared first.

Recorded as an open question, not as a result. Confirming it would require
pre-registering the arm comparison and spending the holdout, which is one-shot
(2024-01-02 onward) and not to be spent on a hypothesis generated by looking.

## Phase 5 gate — closed 2026-08-20

Nine items, checked at the end of Session 18. The phase covers the
`handlers/` layer, the seven tools, the MCP server, and the four routes.

| # | Criterion | Evidence |
|---|---|---|
| 1 | Seven handlers, one contract, three consumers agreeing by construction | The web routes, the MCP tools, and the chat loop all reach the same seven handlers. `test_mcp_schemas.py` asserts the tool registry and `handlers.SEVEN_TOOLS` have identical keys |
| 2 | No probability leaves the handler layer without `n_eff`, an interval, and a q-value | `handlers/validate.py`, plus a structural test on the return annotations. A suppressed cell carries a reason and no rate — the union can express the difference, the view's nulled columns cannot |
| 3 | MCP server authenticated, rate limited, read-only, adding no query logic | Session 16 gate, all 10 items. No `mcp/` module imports `sqlalchemy` or `db_io`. Re-verified live 2026-08-19: seven tools, two splits, holdout refused at the handler, `predict` returning `not_found` |
| 4 | `/`, `/ticker/[sym]`, `/research`, `/chat` all render against the live database | All four verified 2026-08-19. `/chat` answered a real question end to end through MCP, showing every tool call and its raw payload |
| 5 | No route, tool, or chat surface reads holdout | `test_holdout_firewall.py`, 18 passing. Three independent guards: nothing on these routes can express it, `SplitArg` has two members, `handlers/enums.py` raises |
| 6 | `v_positions` reads its thresholds from config | ADR 115, `serving_config`. `test_v_positions_config.py` fails when the stored row and the live config disagree, and names `cscan db sync-config` |
| 7 | The chat layer performs no arithmetic and cannot query outside the seven tools | ADR 130. No SQL, no `pg`, no `JSON.parse` on the MCP boundary; tool results reach the model byte-identical |
| 8 | ADR 112's result is visible on every surface that reports a statistic | `/chat` states it above the composer; `/research` **computes** it from the rows on the page rather than restating it; `mcp/server.py::INSTRUCTIONS` carries it; the system prompt loader refuses to start without it |
| 9 | `test_holdout_firewall.py` and `test_schema_drift.py` both pass, the latter having run | Holdout: 18 passing. Schema drift is an integration test and runs in CI's slow tier against a container migrated from empty — a stronger check than a local run, since it replays the whole chain |

**Item 8 is what makes the phase honest rather than merely complete**, and
`/research` is the reason it holds under change: `KillBanner` derives the
surviving-cell count and the minimum q from the same query that draws the
grid, so the prose cannot drift from the table beside it.

### The measured result at the close

After ADR 129 (`in_trade` fails closed) removed 11.9% of the training
population and ADR 135 corrected the universe's recency:

| split | cells | suppressed | survives FDR | min q |
|---|---|---|---|---|
| train | 56 | 8 | **0** | 0.7604 |
| validate | 56 | 28 | **0** | 0.7061 |

448 cells across the full grid, 248 suppressed, **zero surviving**. Every
reported cell's confidence interval contains its baseline — 48 of 48 on
train, 28 of 28 on validate. ADR 112 was re-established rather than
assumed to survive the smaller sample.

`cell_stats` digest at the close: `7ad6eb4cda94d7e5cad85a54b49c9dc2`.

### What Phase 5 does not claim

The pooled signal arm clears its randomization null on validate (+12.63%
against +6.36%) and this is **not** an edge. It sits far below the null on
train (+81.68% against +216.70%), the breadth_high split disagrees in sign
on validate (−6.45% against +23.61%), and one uncorrected arm comparison on
one split is not the FDR-corrected cell grid — which is where an edge would
have to appear, and does not.


## Deployed, 2026-08-20

`capitalscan.vercel.app`, built from `main` on every push. Neon Postgres
16.15 behind it, migrated to the same head as research (`d2f6b48e1a07`) —
ADR 053's "same migrations applied to both" is now literally true rather
than aspirational.

**921,533 rows synced**, every table verified row-for-row against the
local subset, 0 mismatches. 410MB, 80% of Neon's 512MB free tier.

| route | serves |
|---|---|
| `/` | the screener, three years of dates |
| `/ticker/[sym]` | chart and history, ending at the last closed bar |
| `/research` | the cell grid, computing ADR 112's result from the synced rows |
| `/chat` | renders and states the finding; **cannot answer** |

`/chat` reaches MCP on `127.0.0.1`, which on Vercel is Vercel's own
loopback. That is ADR 118's boundary, not a defect.

**What the deployed site deliberately does not have**: a live price or
today's candle. `bars_live` is not synced (ADR 137), because a nightly
copy would show a price frozen at the last sync and label it live — the
failure ADRs 131 and 134 fixed, and worse remotely because nobody there
can see whether the poller is running. The deployed chart ends at
yesterday's close.

**It is as current as the last `cscan nightly`, not as current as the
poller.** The poller writes locally; the nightly chain syncs. Those are
different clocks and the deployed site tracks the slower one.

Authentication is ADR 138, currently disabled by request.


## Holdout

**Evaluated once. Published whatever it says.**

*(Empty until the end. Do not look.)*

---

## Kill criteria status

**Criterion 1 fired 2026-08-16.** Recorded here with the same detail ADR 033 asks for.

| Criterion | Status | Evidence |
|---|---|---|
| No cell beats baseline at sufficient `n_eff` after FDR | **FIRED** | Three configs, three signal definitions, zero surviving cells. Minimum q 0.706 to 0.849 against α 0.05 |
| Validation edge under half of training edge | **Not applicable** | Presupposes a positive training edge. There is none to halve |
| Holdout edge negative | **Not evaluated** | Holdout untouched. See "The holdout is a one-shot resource" below |

### Criterion 1, the measurement

ADR 033's wording: *"No cell beats its per-ticker-year baseline by the power-adjusted threshold at sufficient `n_eff` after FDR correction: the two-indicator hypothesis is dead as a standalone claim."*

Three configurations have now been measured end to end. Each used a different signal definition. None produced a cell surviving FDR correction.

| Config | Session | Tests | Min q | Survive FDR |
|---|---|---|---|---|
| `1835688bf7d760ba` | 12, 13 | 48 | 0.769 | 0 |
| `697f3ae71428d392` | 14 | 56 | 0.790 | 0 |
| `86e91448a65aa40b` | ADR 110 | 224 train / 224 validate | 0.849 / 0.706 | 0 / 0 |

The ADR 110 run is the one that fires the criterion, because it is the first to satisfy every clause simultaneously.

| Clause | Train | Validate |
|---|---|---|
| Cells enumerated | 224 | 224 |
| Cells at sufficient `n_eff` (unsuppressed) | 124 | 56 |
| Mean `n_eff` | 93.3 | 20.8 |
| Minimum q-value | 0.8492 | 0.7061 |
| Cells surviving FDR at α 0.05 | **0** | **0** |

124 cells on train at a mean `n_eff` of 93.3 is "sufficient `n_eff`" by any reading of the clause. The minimum q-value is off the threshold by a factor of seventeen. This is not a near miss.

Three arms point the same way on the same run:

| Split | Arm | Return | Null p50 | Null p97.5 |
|---|---|---|---|---|
| train | buy_hold | **+383.66%** | | |
| train | signal | +85.75% | +102.50% | +216.70% |
| validate | buy_hold | −3.69% | | |
| validate | signal | +12.63% | −12.45% | +6.36% |

Validate's signal arm clears its own null for the first time across three configs. It is not read as an edge: it performs better out-of-sample than in-sample, which is backwards from what a real effect looks like, on a sample that just halved. The train split, with four times the data, has the signal 298 points behind buy-and-hold and below the null's median.

Session 13 additionally found short-term tax removes the strategy's entire pre-tax return, and Session 14 found every drawdown-slice interval crosses zero, retiring ADR 015's central claim at this sample.

### Why this is a measurement and not an artifact

`stats self-validate` passes on the same run.

| Check | Result | Threshold |
|---|---|---|
| Null test on driftless synthetic data | 2 of 480 cells at q < 0.05 = 0.42% | 5% |
| Recovery test against the analytical baseline | gap 0.039 pp | 1.0 pp |
| Deliberately broken variant (SE on raw `n`) | caught at 11.67% | must fail |

The third row is the load-bearing one. The pipeline detects a deliberately introduced bug of exactly the kind that would manufacture false significance, and finds nothing in real data. A layer that can catch its own sabotage and still reports zero is reporting a fact about the data.

The backtest harness passes 5/5 on every run: `no_lookahead`, `entry_sanity`, `exit_sanity`, `return_identity`, `non_overlap`.

### Criterion 2, and why "not applicable" rather than blank

*"Validation edge under half the training edge → overfit. Cut features, widen cells."*

Both splits exist and both were measured, so this is evaluable rather than pending. It cannot fire because it presupposes a training edge to be half of. The train signal arm sits 298 points behind buy-and-hold and below its null's median, and zero train cells survive correction.

You cannot overfit to an effect you never found. Recorded as not applicable rather than left blank, because a blank reads as "not yet looked at."

### The holdout is a one-shot resource

Holdout is untouched and stays that way pending a direction decision.

Era 2024+ is the holdout split, and it is the same date range whatever signal is tested. Spending it to confirm a hypothesis that train and validate already retired buys very little and costs the firewall for whatever replaces it. Per ADR 019 and ADR 033, holdout is evaluated exactly once.

If the decision is to close out the two-indicator hypothesis permanently, run it and publish. If the decision is to pivot the input signal per ADR 033, leave it sealed.

### What this does not retire

Criterion 1 kills **the two-indicator hypothesis as a standalone returns predictor**. It is a statement about `p_hit` against a per-ticker-year baseline, and about the benchmark arms built on the same events. Three things sit outside that claim and are untouched by it.

**Detection is not prediction.** The detector fires correctly, deterministically, and with verified lookahead handling on 630,592 events. That a `confluence_low` does not predict a 5-day return better than the ticker's own base rate is a different claim from "the event is not worth seeing." Attention-directing use is untested rather than disproven — and untested is exactly what it remains, because nothing measured so far speaks to it. Any such use is a human judgment on top of an event feed, not a validated edge, and it should be described that way.

**Phase 6's model surface is related but not identical.** The cell grid tests fourteen fixed cells; the model per ADR 093 predicts eleven heads over continuous features, including interactions no fixed grid can express, and terminal-return quantiles as well as reachability. That is a genuinely different hypothesis class, and criterion 1 does not test it directly.

ADR 093 anticipated this exact question and wrote its own answer into its status rationale: *"Provisional rather than Pinned: ... ADR 033's kill criteria sit between here and there. If Phase 4 finds no cell survives FDR correction, there may be no model to expand."* Fourteen cells at a mean `n_eff` of 93 showing nothing is weak prior support for a model conditioning on more features with less effective data per condition. Not impossible; not encouraging. Any Phase 6 decision should cite this measurement rather than route around it.

**The engine.** Per ADR 033: the event-study engine with correct look-ahead handling and MFE/MAE tracking, the tool-restricted chat layer, the calibration methodology, and the ingest and scheduling pipeline all survive a null result. Four alternative signal families are testable in the same framework with no rewrite: volatility term structure, earnings drift, cross-sectional momentum residuals, volume-price divergence.

### Recorded

Three configs, three signal definitions, 630,592 events, 328 cells across two splits, zero surviving FDR correction, and a self-validation suite that catches a planted bug on the same run.

ADR 033 was written before any code existed, for this outcome specifically: *"Built for that outcome, a null result stops being a failure. A project reporting 'I tested this rigorously and found no edge, here is the infrastructure proving it' reads better than a suspiciously profitable backtest."*

The measurement worked.
---

## ADR 112 re-measured on the corrected universe — 2026-08-22

**ADR 112 holds a fifth time.** Zero cells survive FDR correction on either
split, measured on the universe rebuilt after the ADR 145 share-basis fix,
the ADR share-count corrections, and the `McapPlausibility` ceiling.

| | train | | validate | |
|---|---|---|---|---|
| | scored | survive | scored | survive |
| ADR 112 (`86e91448a65aa40b`) | 48 | **0** | 28 | **0** |
| ADR 142 (`bbc99a02ebdc999f`) | 48 | **0** | 28 | **0** |
| ADR 145 (`f66729c7eda212a4`) | 48 | **0** | 28 | **0** |
| this rebuild (`f66729c7eda212a4`) | 48 | **0** | 28 | **0** |
| min q | 0.6400 | | 0.7317 | |

**The scored denominator is 48/28 in all four**, so this is like-for-like and
not a shifting grid. The minimum q moved from 0.6030 to 0.6400 on train and
0.7154 to 0.7317 on validate — further from significance, not closer.

**Population.** 51,837 universe rows, **6,299 in_trade** quarters across 543
tickers; 862,326 priced events. Against the ADR 145 measurement that is
6,325 -> 6,299 in_trade (-26) and 865,984 -> 862,326 events, the loss being
the seven tickers that lost membership when the ADR ratio and mcap ceiling
corrections landed (NTES, ONC, HTHT, GRMN, PKG, AAP, SIMO).

The best train cell is still `bb_lower_touch` in the 0-10% drawdown bucket:
`n_events = 4866` collapsing to `n_eff = 599`, p_hit 0.1981 against a 0.1759
baseline, q = 0.6400.

### The harness, and what it cost this time

All five checks passed in **48m21s** over 864,133 events and 858 tickers —
against 3h58m35s for the same five checks on the previous rebuild. The
difference is the parallel harness (ticker slices spooled to parquet), not a
smaller job: this run scored 864,133 events against 865,984.

### `entry_price` means one thing again

The out-of-trade events in this config now number 1,807 across 634 tickers,
**every one unpriced**, all written by a single `cscan events` run. Before
the single-writer fix, 755 out-of-trade events carried an `entry_price` and
1,324 `path` rows were built from them.

That restores the stated reason behind the four-module allowlist in
`test_events_in_trade_filter.py` — `entry_price IS NOT NULL` means "the
backtest priced this" — rather than leaving it true only by accident.

### The benchmark arms

Pooled era, latest run per split. `benchmarks` accumulates generations under
one `config_hash` — three train runs sit there now — so every figure below is
scoped to a single `run_id`. The serving layer does the same, correctly, via
`LATEST_RUN` in `web/lib/research.ts`, ordering on `computed_at` rather than
on the timestamp embedded in the id.

| arm, pooled | train | validate |
|---|---|---|
| dca_hybrid | 4.6041 | 0.0800 |
| buy_hold | 4.4159 | -0.0309 |
| dca_lump | 4.4159 | -0.0309 |
| dca_fixed | 4.3840 | 0.1043 |
| **signal** | **2.5301** | **0.1385** |
| trim | 2.2891 | 0.0082 |
| dca_signal | 1.7784 | 0.1244 |
| random null, mean | 1.5051 | -0.1250 |
| random null, 97.5th pct | 2.6597 | 0.0097 |

**Train agrees with the cell grid.** The signal arm returns 2.5301 against a
null 97.5th percentile of 2.6597 — below it, and 57% of buy-and-hold over the
same window. That is the session 13 finding, unchanged across four rebuilds
(2.7113 against 2.7489 last time).

**Validate still disagrees, and it is still recorded rather than smoothed.**
The signal arm is the best of all eight arms at 0.1385, above the null's
97.5th percentile of 0.0097, while buy-and-hold is negative at -0.0309. The
same shape as the previous rebuild (0.1318 against 0.0224).

This does not overturn anything. The cell grid is where the claim lives, and
zero cells survive FDR on validate with a minimum q of 0.7317. A single
pooled arm beating a null on the smaller split, with no cell-level
significance under it, is what an underpowered favourable draw looks like.
The holdout remains unspent.

### Still outstanding after this rebuild

Five universe rows remain above $5T, one of them `in_trade` (AAP
2011-12-31, $5.04T). All five are the x1,000 share-scale class that
`McapPlausibility`'s $6T ceiling cannot reach without threatening a real
mega-cap: AAPL sits at $4.25T. ADR 146 removes the cause at ingest, and
`scripts/adr146_clear_scale_errors.sql` clears the 33 rows already stored —
deliberately deferred to the next rebuild, since propagating it here would
mean redoing the compute and harness phases this run has already finished.

---

## ADR 112 on the fully corrected universe — 2026-08-22 (stage 7)

**ADR 112 holds a sixth time.** This is the first measurement with every
known market-cap defect fixed: the split basis (ADR 145), the ADR share
counts, the `McapPlausibility` ceiling, and the x1,000 scale class (ADR 146).

| | train | | validate | |
|---|---|---|---|---|
| | scored | survive | scored | survive |
| ADR 112 (`86e91448a65aa40b`) | 48 | **0** | 28 | **0** |
| ADR 142 (`bbc99a02ebdc999f`) | 48 | **0** | 28 | **0** |
| ADR 145 (`f66729c7eda212a4`) | 48 | **0** | 28 | **0** |
| stage 6 rebuild | 48 | **0** | 28 | **0** |
| stage 7, ADR 146 | 48 | **0** | 28 | **0** |
| min q | 0.6729 | | 0.7317 | |

The scored denominator is 48/28 in all five. Minimum q on train has moved
0.6030 -> 0.6400 -> 0.6729 across the three corrections, each time further
from significance rather than closer.

**Population.** 51,837 universe rows, **6,295 in_trade** across 540 tickers;
863,489 events across 857 tickers. Against stage 6 that is -4 in_trade
quarters and -644 events, which is exactly the x1,000 correction: AAP, ALK
and ENSG lose the quarters whose market caps were wrong by three orders of
magnitude.

**Market cap after the fix.** Zero universe rows above $5T, against five
before (one `in_trade`). The maximum is now **$4.84T**, which is AAPL and is
real. Every corrupt row fell by exactly 1000x — AAP 2011-12-31 $5,044B ->
$5.3B, SWKS 2012-03-31 $5,210B -> $5.2B, MAA 2014-06-30 $5,479B -> $5.5B,
WWD 2020-09-30 $5,001B -> $5.0B, ALK 2011 $2,453B -> $2.5B.

**Harness:** all five checks PASS on 863,489 events / 857 tickers.

### The benchmark arms

| arm, pooled | train | validate |
|---|---|---|
| dca_hybrid | 4.5634 | 0.0800 |
| buy_hold | 4.3767 | -0.0309 |
| dca_lump | 4.3767 | -0.0309 |
| dca_fixed | 4.3449 | 0.1043 |
| **signal** | **2.4920** | **0.1385** |
| trim | 2.2707 | 0.0082 |
| dca_signal | 1.7717 | 0.1243 |
| random null, mean | 1.5354 | -0.1250 |
| random null, 97.5th pct | 2.6777 | 0.0097 |

Train is unchanged in substance: the signal arm returns 2.4920 against a
null 97.5th percentile of 2.6777, below it, and 57% of buy-and-hold.
Validate still disagrees, still recorded: signal is best of eight arms at
0.1385 while buy-and-hold is negative, with zero cells surviving FDR beneath
it. The holdout remains unspent.

### The stale-event sweep nearly corrupted this

The sweep predicate `run_id < 'backtest_compute_<today>'`, which the session
20 notes prescribe, **matched nothing** — an earlier compute had run the same
day, and `'backtest_compute_20260822T093630_...'` sorts greater than
`'backtest_compute_20260822'`. The sweep printed `DELETE 0` and the
verification query reused the same predicate, so it printed `0 stale` and
confirmed its own error.

644 events across AAP, ALK and ENSG reached the harness, which **passed**:
those three tickers were absent from the new run entirely, so there was no
competing cluster head for `_check_non_overlap` to catch. Found by grouping
`events` by `run_id` directly. Corrected form and the rule it produced —
verify with a different predicate than you deleted with — are in the session
20 notes and `scripts/adr146_clear_scale_errors.sql`.

---

## ADR 112 on the watch-universe rebuild — 2026-08-24 (stage 9)

**ADR 112 holds a seventh time.** Zero cells survive FDR correction on
either split. This is the first measurement taken after ADR 148's sector
backfill, which is what made it necessary: populating 254 sectors changed
which median `crit_rel_return` compares against, and `in_trade` moved
6,295 -> 6,641.

| | train | | validate | |
|---|---|---|---|---|
| | scored | survive | scored | survive |
| ADR 112 (`86e91448a65aa40b`) | 48 | **0** | 28 | **0** |
| ADR 142 (`bbc99a02ebdc999f`) | 48 | **0** | 28 | **0** |
| ADR 145 (`f66729c7eda212a4`) | 48 | **0** | 28 | **0** |
| stage 6 | 48 | **0** | 28 | **0** |
| stage 7 (ADR 146) | 48 | **0** | 28 | **0** |
| stage 9 (ADR 148/149) | 48 | **0** | 28 | **0** |
| min q | 0.6964 | | 0.7350 | |

**The interesting number is not the zero.** Minimum q on train has walked
**0.6030 -> 0.6400 -> 0.6729 -> 0.6964** across four successive data
corrections -- the split basis, the ADR share counts, the x1,000 scale
class, and the sector taxonomy. Every one of those fixes was in the
direction that would plausibly *reveal* an edge if one were there, and the
measurement moved further from significance each time.

The scored denominator is 48/28 in all six, so the comparison is
like-for-like rather than a shifting grid.

**Population.** 910,178 in-trade events across 544 tickers, plus 197,688
watch events across 447 (ADR 149), fully computed and read by no statistic.
6,641 `in_trade` and 1,436 `in_watch` universe quarters.

### The benchmark arms

| arm, pooled | train | validate |
|---|---|---|
| dca_hybrid | 4.4034 | 0.0816 |
| buy_hold | 4.2450 | -0.0273 |
| dca_fixed | 4.1649 | 0.1027 |
| **signal** | **2.5264** | **0.1263** |
| trim | 2.2325 | 0.0101 |
| dca_signal | 1.6962 | 0.1233 |
| random null, mean | 1.4877 | -0.1266 |
| random null, 97.5th pct | 2.8338 | 0.0154 |

Train is unchanged in substance: the signal arm returns 2.5264 against a
null 97.5th percentile of 2.8338 -- below it, and 60% of buy-and-hold.
Validate still disagrees and is still recorded rather than smoothed: signal
is best of eight arms at 0.1263 while buy-and-hold is negative, with zero
cells surviving FDR beneath it.

### Three defects the harness caught, none of them the obvious one

The harness failed twice before passing, and every failure was real.

**Provisional poller rows were never superseded (ADR 150).** ADR 140 made
the poller's row yield to the nightly, but that only happens when the two
share a key -- and the writers routinely pick a different *primary*
`signal_type` from the same fired set. 31 rows survived with an observed
`entry_price`, no exit and no cluster tagging. Only 8 were the "settled bar
disagrees" case the first diagnosis assumed; 23 were already carried inside
a backtest row's `signal_types_all`.

**`run_events` erased the backtest's `entry_price`.** It wrote
`entry_price=None` *and* owned the column, so every nightly nulled a price
the backtest computed while `gross_ret` -- which it does not own -- survived.
Two rows became a return with no entry. Ruling C4 settles it: a writer names
only what it computes.

**`run_events` tagged clusters from a five-day window (ADR 151).** Ruling C5
assigns those columns to the backtest exclusively. DKNG, EXR, NTNX and STZ
each carried heads on 08-17 and 08-24, exactly 5 trading bars apart, because
the nightly could not see the earlier head.

**And one the harness could not catch, found by querying the result.** The
first ADR 149 run wrote 245k watched events as `in_trade=true`:
`_EVENT_COLUMNS` had no slot for the flags, so
`pd.DataFrame(rows, columns=...)` dropped them and the column fell back to
its `NOT NULL DEFAULT true`. Nothing raised, and the harness would have
passed a population containing the watch universe mislabelled as tradeable.
The guard is now general rather than covering `path_metrics` alone.


---

## 2026-08-25 — Phase 6 opens: the training matrix

No model fitted. What was measured, all against `f66729c7eda212a4`:

**The frame** (`research/features.py`, ADR 147 partition applied):

| split | rows | tickers | dropped ETF | dropped no-label | sector levels |
|---|---|---|---|---|---|
| train | 125,714 | 401 | 741 | 8,893 | 11 |
| validate | 26,788 | 305 | 67 | 1,330 | 11 |

**Labels already existed.** ADR 113's four are columns on `events`, written
by `peak_labels.py` and the backtest:

| split | R₅ | R₁₀ | M₅ | M₁₀ | total |
|---|---|---|---|---|---|
| train | 135,348 | 135,348 | 126,455 | 126,455 | 135,348 |
| validate | 28,185 | 28,185 | 26,855 | 26,855 | 28,185 |
| holdout | 63,426 | 62,952 | 59,279 | 58,823 | 64,010 |

Label means, as a sanity check that the two families differ as they must
($M_h \ge R_h$ by construction):

    train      fwd_ret_5d  0.0028   peak_ret_5d  0.0272
               fwd_ret_10d 0.0053   peak_ret_10d 0.0393
    validate   fwd_ret_5d -0.0004   peak_ret_5d  0.0340
               fwd_ret_10d -0.0007  peak_ret_10d 0.0497

**Two columns are empty and were nearly shipped as features:**

    events.sector      0 of 227,543 populated
    events.mcap_usd    0 of 227,543 populated

Values live in `tickers.sector` (11 GICS levels) and `universe.mcap_usd`
(47,181 values, quarterly from 2010-03-31). Both now read from those, the
market cap through a lateral bounded by `as_of <= signal_date`.

## 2026-08-25 — ETFs enter the trade universe (ADR 154)

Ingested SPY, VOO and IBIT; QQQ was already present.

| | daily bars | from | mcap | in_trade |
|---|---|---|---|---|
| SPY | 5,510 | 2004-09-29 | $685B | yes |
| QQQ | 5,282 | 2005-08-24 | $289B | yes |
| VOO | 4,013 | 2010-09-09 | NULL | yes, by exemption |
| IBIT | 656 | 2024-01-11 | NULL | yes, by exemption |

Each start date matches the fund's own inception. `cscan shares` resolves
SPY (99 rows) and neither VOO nor IBIT, which is what ADR 154 exists to
stop mattering: before it, VOO failed `crit_mcap` alone while passing
SMA200, its slope and relative return.

Pollable population afterwards: **184 trade, 28 watch**.

Only 2026Q2 is evaluated for the three new tickers. The other 65 quarters
are ~2.6 hours at 2m23s each and are in `BACKLOG.md`.


## 2026-08-25 — the bar for ADR 113 check 5

The unconditional baseline's **validation** pinball loss, fitted on train
labels only, weighted `1/|cluster|`. 125,714 train / 26,788 validate rows.
A model must come in **below** these to be worth promoting.

| head | τ=0.05 | τ=0.25 | τ=0.50 | τ=0.75 | τ=0.95 |
|---|---|---|---|---|---|
| `fwd_ret_5d` | 0.00573 | 0.01457 | 0.01693 | 0.01376 | 0.00541 |
| `fwd_ret_10d` | 0.00806 | 0.02110 | 0.02437 | 0.01945 | 0.00736 |
| `peak_ret_5d` | 0.00301 | 0.01033 | 0.01529 | 0.01488 | 0.00648 |
| `peak_ret_10d` | 0.00358 | 0.01314 | 0.01985 | 0.01922 | 0.00811 |

Shape worth noting before any model exists: loss peaks at the median and
falls toward both tails, which is what pinball does when the label
distribution is tight — there is simply less to be wrong about at τ=0.05.
The 10-day heads cost more than the 5-day ones throughout, which is the
wider distribution and not a difficulty ranking.

**A per-ticker-year baseline was also measured and is not constructible.**
Train covers 2010–2021 and validate 2022–2023 with **zero** ticker-year
overlap, so every lookup falls back to the global value and the two agree
to five decimals. Recorded as an open item against ADR 113's wording.


## 2026-08-25 — first fit: twenty heads, purged walk-forward CV

**This is not ADR 113's check 5.** It is cross-validation *inside* train
(2010–2021, seven folds). The validate split (2022–2023) has not been
touched and the holdout has not been looked at. Check 5 is a separate
measurement against held-out data and is not reported here.

125,714 rows, 4,527 sessions, 20 heads, **281 seconds** total.

| head | folds won | mean impr% | worst% | best% |
|---|---|---|---|---|
| `terminal_h5_q05` | 7/7 | 4.86 | 1.26 | 11.18 |
| `terminal_h5_q25` | 7/7 | 0.93 | 0.19 | 2.16 |
| `terminal_h5_q50` | 6/7 | 1.00 | −0.12 | 1.93 |
| `terminal_h5_q75` | 7/7 | 4.00 | 1.47 | 5.38 |
| `terminal_h5_q95` | 7/7 | 12.61 | 8.20 | 21.89 |
| `terminal_h10_q05` | 7/7 | 4.65 | 1.19 | 11.43 |
| `terminal_h10_q25` | 6/7 | 0.73 | −0.01 | 2.89 |
| `terminal_h10_q50` | 6/7 | 0.55 | −0.23 | 1.41 |
| `terminal_h10_q75` | 7/7 | 3.38 | 0.14 | 7.08 |
| `terminal_h10_q95` | 7/7 | 11.85 | 6.69 | 24.58 |
| `peak_h5_q05` | 6/7 | 1.03 | −1.05 | 2.13 |
| `peak_h5_q25` | 7/7 | 3.36 | 0.01 | 5.01 |
| `peak_h5_q50` | 7/7 | 7.91 | 4.28 | 13.50 |
| `peak_h5_q75` | 7/7 | 12.22 | 6.82 | 22.56 |
| `peak_h5_q95` | 7/7 | 16.83 | 9.36 | 31.95 |
| `peak_h10_q05` | 6/7 | 1.13 | −0.59 | 3.03 |
| `peak_h10_q25` | 7/7 | 4.07 | 1.51 | 6.10 |
| `peak_h10_q50` | 7/7 | 7.95 | 4.75 | 14.34 |
| `peak_h10_q75` | 7/7 | 11.48 | 6.05 | 21.54 |
| `peak_h10_q95` | 7/7 | 15.33 | 6.52 | 29.10 |

**15 of 20 heads beat the baseline on every fold.**

### Three reasons not to read that as an edge yet

**Relative improvement flatters the tails structurally.** At τ=0.95 the
baseline loss is ~0.005, so 12% of it is ~0.0006. The heads with the
largest percentages are the ones where the absolute number is smallest.
Ranking heads by this column ranks them mostly by τ.

**The peak family improves far more than the terminal family, and that has
a mundane explanation.** $M_h$ is a maximum: bounded below, and driven by
dispersion rather than direction. Dispersion is genuinely predictable from
`rv_pct_252d`, `bb_width_pct` and `vix_close`, all of which are in the
feature set. A model that forecasts volatility well will score well on
every peak head without knowing anything about which way price goes.

DESIGN §7.2 anticipated exactly this as the model's addition over the cell
grid — *"a signal that does not change P(R_5 ≥ 3%) can still narrow or
widen the spread of R_5, and the grid is blind to that by construction"* —
so finding it is the design working, not a surprise. It is also not a
directional claim.

**The directional heads are the weakest ones.** `terminal_h5_q50` and
`terminal_h10_q50` carry the "which way does it go" question and improve
0.55–1.00%, losing a fold each. `terminal_h10_q50`'s worst fold is −0.23%.
That is consistent with ADR 112 rather than against it.

**One diagnostic worth keeping:** `terminal_h5_q50` fold 2017 stopped at
`best_iteration = 1`. Early stopping fired immediately, meaning no tree
after the first improved the fold loss. That is the model saying it found
nothing, legibly.

### What has to happen before any of this counts

Check 5 on the validate split, per head, against the bar recorded above.
Then coverage — DESIGN §7.6 checks quantile heads by whether the realised
fraction below $\hat{Q}_{0.25}$ is 25%, which a head can fail while
posting a good pinball loss.


## 2026-08-25 — ADR 113 check 5, on the validate split

**The holdout was not read.** Rounds come from the median `best_iteration`
across the seven CV folds inside train; validate is touched once per head,
for scoring. Using validate for early stopping and then scoring on it is
the leak this structure exists to prevent.

train 125,714 → validate 26,788. 20 heads, 308 seconds.

| head | rounds | model | baseline | impr% | |
|---|---|---|---|---|---|
| `terminal_h5_q05` | 86 | 0.00546 | 0.00573 | 4.72 | PASS |
| `terminal_h5_q25` | 63 | 0.01439 | 0.01457 | 1.23 | PASS |
| **`terminal_h5_q50`** | 53 | 0.01694 | 0.01693 | **−0.09** | **FAIL** |
| `terminal_h5_q75` | 104 | 0.01340 | 0.01376 | 2.66 | PASS |
| `terminal_h5_q95` | 146 | 0.00477 | 0.00541 | 11.81 | PASS |
| `terminal_h10_q05` | 83 | 0.00779 | 0.00806 | 3.32 | PASS |
| `terminal_h10_q25` | 41 | 0.02097 | 0.02110 | 0.62 | PASS |
| **`terminal_h10_q50`** | 22 | 0.02449 | 0.02437 | **−0.49** | **FAIL** |
| `terminal_h10_q75` | 84 | 0.01908 | 0.01945 | 1.87 | PASS |
| `terminal_h10_q95` | 115 | 0.00664 | 0.00736 | 9.77 | PASS |
| `peak_h5_q05` | 66 | 0.00301 | 0.00301 | 0.01 | PASS |
| `peak_h5_q25` | 94 | 0.00987 | 0.01033 | 4.43 | PASS |
| `peak_h5_q50` | 205 | 0.01361 | 0.01529 | 11.01 | PASS |
| `peak_h5_q75` | 220 | 0.01238 | 0.01488 | 16.82 | PASS |
| `peak_h5_q95` | 176 | 0.00512 | 0.00648 | 21.10 | PASS |
| `peak_h10_q05` | 41 | 0.00357 | 0.00358 | 0.26 | PASS |
| `peak_h10_q25` | 110 | 0.01242 | 0.01314 | 5.49 | PASS |
| `peak_h10_q50` | 179 | 0.01754 | 0.01985 | 11.62 | PASS |
| `peak_h10_q75` | 171 | 0.01596 | 0.01922 | 16.94 | PASS |
| `peak_h10_q95` | 235 | 0.00672 | 0.00811 | 17.07 | PASS |

**18/20 pass. The two failures are the two directional heads.**

### The pattern is the result

Every peak head passes. Every terminal *tail* head passes. The only
failures are `terminal_h5_q50` and `terminal_h10_q50` — the two heads that
carry the "which way does it go" question and nothing else.

$M_h$ is a maximum, so it is driven by dispersion. The terminal tails are
also dispersion: $\hat{Q}_{0.05}$ and $\hat{Q}_{0.95}$ of $R_h$ describe
how wide the distribution is, not where it is centred. The median is the
only head in the fan that is a statement about *location*.

So the model has learned to forecast **spread** and has learned nothing
about **direction**. `rv_pct_252d`, `bb_width_pct`, `vix_close` and
`atr`-derived features are in the feature set and volatility is genuinely
autocorrelated, so this is the expected thing to be learnable. DESIGN §7.2
named it in advance as the model's addition over the cell grid.

### What this does and does not decide

**ADR 113's kill criterion does not fire.** Its wording is "no better than
the unconditional baseline in pinball loss **at any horizon**", and 18
heads beat it. By the letter of the pre-registered rule, Phase 6 continues.

**It is not a directional edge, and should not be reported as one.** The
two-indicator hypothesis ADR 112 tested was about direction, and both heads
expressing it failed out-of-sample. Nothing here contradicts ADR 112; the
model found a different thing than the one the project set out to find.

**Two passes are ties.** `peak_h5_q05` at +0.01% and `peak_h10_q05` at
+0.26% are not distinguishable from the baseline in any practical sense.
Counting them as passes is following the rule as written, and the rule has
no tolerance band. Worth adding one before this number is quoted anywhere.

### Before this is load-bearing

Coverage (DESIGN §7.6): the realised fraction below $\hat{Q}_{0.25}$ should
be 25%. **A head can post a winning pinball loss and still be badly
calibrated**, and the product is the probability, so coverage decides
whether any of this is servable. Not yet measured.


## 2026-08-25 — coverage (DESIGN §7.6), and it fails

Same protocol as check 5: rounds from CV inside train, validate scored
once, holdout untouched. DESIGN §7.4's post-fit sort applied, so crossing
quantiles are not being mistaken for miscalibration.

Realised fraction of validate labels at or below $\hat{Q}_	au$:

| | τ=0.05 | τ=0.25 | τ=0.50 | τ=0.75 | τ=0.95 |
|---|---|---|---|---|---|
| **target** | 0.05 | 0.25 | 0.50 | 0.75 | 0.95 |
| `terminal_h5` | 0.087 | 0.328 | 0.553 | 0.760 | 0.935 |
| `terminal_h10` | 0.090 | 0.335 | 0.544 | 0.761 | 0.937 |
| `peak_h5` | 0.077 | 0.297 | 0.527 | 0.738 | 0.934 |
| `peak_h10` | 0.073 | 0.285 | 0.503 | 0.724 | 0.925 |

### The fan is too narrow, at both ends at once

8.7% of outcomes fall below $\hat{Q}_{0.05}$ where 5% should, and only
93.5% fall below $\hat{Q}_{0.95}$ where 95% should. Both errors point the
same way: **more mass lands outside the interval than the interval
claims**. A 90% band built from these two heads covers about 85%.

The lower half is worse than the upper. Every head over-covers at τ=0.05
and τ=0.25 by 45–75% in relative terms, while τ=0.75 and τ=0.95 are within
a few points. The fitted fan sits too high and is too tight below.

### Why this matters more than check 5

DESIGN §7.6 is explicit: *"The product **is** the probability, so
calibration is the metric."* Pinball loss rewards being close on average;
coverage asks whether the number means what it says. **A head can win the
first and fail the second**, and eighteen of these did exactly that.

Nothing here is servable as a probability. A user told "5% chance of a move
below X" who sees it 8.7% of the time has been given a wrong number, and
invariant 8 requires every probability to travel with `n_eff` and a
confidence interval precisely because that claim has to hold.

### What it does not overturn

Check 5's pattern still stands and is unaffected by calibration: the two
directional heads fail and the dispersion heads pass. Coverage says the
dispersion the model found is systematically **understated**, not that it
is absent.

### The open question this creates

DESIGN §7.6 says quantile heads are *"checked by coverage rather than
recalibrated"*, and isotonic regression is specified for binary heads only
— which ADR 113 retired. So the design has no repair path for exactly the
failure that occurred, because the heads it wrote the repair for no longer
exist.

Three options, none taken here:

1. **Conformal calibration on validate.** Widen each fan by the empirical
   residual quantile. Standard, distribution-free, and honest about being
   a post-hoc correction.
2. **Extend isotonic to quantile heads**, contradicting §7.6 as written.
3. **Ship nothing until coverage is met by the fit itself.** Strictest, and
   the one most in keeping with §7.6's reasoning.

This belongs to whoever set the design, not to the session that measured
the miss.


## 2026-08-25 — the coverage miss is a regime shift, not a model defect

Two measurements, in order.

**ADR 155 option A: relax regularisation.** Ruled out.

| variant | mean abs coverage error | sum loss |
|---|---|---|
| baseline (§7.5) | 0.0386 | 0.05497 |
| `lambda_l2=1.0` | 0.0384 | 0.05497 |
| `lambda_l2=0`, 31 leaves, depth 6 | 0.0394 | 0.05501 |
| `lambda_l2=0`, 63 leaves, depth 8 | 0.0399 | 0.05513 |

Coverage does not improve. Both it and loss degrade slightly, and `peak_h5`
degrades monotonically (0.0258 → 0.0285).

**The featureless control.** A train-fitted unconditional quantile — a
constant — reproduces the miss:

| τ | fitted model | constant |
|---|---|---|
| 0.05 | 0.087 | 0.087 |
| 0.25 | 0.328 | 0.333 |
| 0.50 | 0.553 | 0.536 |
| 0.75 | 0.760 | 0.730 |
| 0.95 | 0.935 | 0.926 |

**The labels moved.**

    fwd_ret_5d      train      validate      shift
      q05         -0.05790    -0.07603     -0.01812
      q25         -0.01491    -0.02448     -0.00957
      q50          0.00388     0.00078     -0.00310
      q75          0.02200     0.02420     +0.00220
      q95          0.05915     0.07049     +0.01133
      mean         0.00282    -0.00044     -0.00325
      sd           0.04156     0.04713     +13%

    peak_ret_5d     train      validate      shift
      q95          0.08772     0.10675     +0.01903
      mean         0.02724     0.03401     +0.00678

Train 2010–2021, validate 2022–2023. Validate is 13% more volatile with
both tails pushed outward, and the median return crosses from positive to
approximately zero.

**Reading.** A fan fitted on the calmer era is too narrow in the more
violent one by construction. `vix_close`, `rv_pct_252d` and `bb_width_pct`
are all features, so the model sees contemporaneous volatility and still
under-disperses: it learned the map from those to future dispersion during
a low-volatility decade and the map did not transfer.

This does not weaken the check 5 result. The baseline suffers the identical
shift, so the comparison between them is unaffected. It does mean the
**absolute** calibration of every head belongs to the training era rather
than to the market.

**Nothing was persisted.** `capitalscan/models/` is empty and `predictions`
holds 0 rows. Roughly 640 boosters were fitted across these runs and all
were discarded; `fit_head` returns fold results and no model by design
(ADR 067).


## 2026-08-25 — three coverage diagnostics, and a correction

The previous entry concluded the coverage miss was *entirely* a regime
shift. **That was too strong.** Measured on `terminal_h5`:

### 1. Coverage inside train (fold-validation years 2015–2021)

| fold | q05 | q25 | q50 | q75 | q95 | n |
|---|---|---|---|---|---|---|
| 2015 | 0.067 | 0.276 | 0.536 | 0.781 | 0.945 | 7,920 |
| 2016 | 0.058 | 0.244 | 0.492 | 0.738 | 0.940 | 8,872 |
| 2017 | 0.033 | 0.181 | 0.386 | 0.656 | 0.939 | 12,643 |
| 2018 | 0.101 | 0.310 | 0.535 | 0.745 | 0.943 | 12,694 |
| 2019 | 0.038 | 0.195 | 0.422 | 0.703 | 0.937 | 12,629 |
| 2020 | 0.146 | 0.333 | 0.514 | 0.694 | 0.881 | 14,400 |
| 2021 | 0.051 | 0.247 | 0.485 | 0.735 | 0.946 | 22,881 |
| **mean** | 0.070 | 0.255 | 0.481 | 0.722 | 0.933 | |

**Mean absolute error 0.018 in-fold against 0.039 on validate.** Roughly
half the miss is present *inside the training era*, with the same outward
signature. There is a real fit-induced under-dispersion floor and the era
doubles it.

Per-fold spread is wide: q05 runs 0.033 (2017) to 0.146 (2020). Coverage is
regime-dependent within train as well.

### 2. Per validate year

| | q05 | q25 | q50 | q75 | q95 | mean abs err |
|---|---|---|---|---|---|---|
| 2022 | 0.119 | 0.381 | 0.598 | 0.788 | 0.949 | 0.047 |
| **2023** | 0.056 | 0.277 | 0.511 | 0.734 | 0.921 | **0.018** |

**2023 is as well calibrated as a normal in-train year.** The failure is
almost entirely 2022. A shock, not a permanent shift.

### 3. Coverage by VIX quartile (validate)

| bucket | VIX | q05 | q25 | q50 | q75 | q95 |
|---|---|---|---|---|---|---|
| Q1 low | 12.1–16.0 | 0.048 | 0.268 | 0.493 | 0.697 | 0.901 |
| Q2 | 16.1–19.8 | 0.109 | 0.366 | 0.588 | 0.794 | 0.948 |
| Q3 | 19.8–25.7 | 0.110 | 0.383 | 0.613 | 0.803 | 0.956 |
| Q4 high | 25.8–36.5 | 0.080 | 0.294 | 0.518 | 0.747 | 0.935 |

Train VIX range is **9.1–82.7**, so validate's 12.1–36.5 is entirely inside
the model's experience. **"Unprecedented volatility" is not the
explanation.** Coverage is best at low VIX and worst in the middle, not
monotone in it.

### Revised reading

Three separate things, not one:

1. **A stable ~0.018 miscalibration intrinsic to the fit**, present in every
   year including 2023. Consistent, therefore correctable.
2. **A 2022-specific excess** roughly doubling it, in a mid-VIX grinding
   drawdown the feature set does not describe.
3. **No out-of-range volatility anywhere.** The earlier +13% standard
   deviation shift is real but is not the model meeting conditions it had
   never seen.

This changes the repair. Calibration fitted on a *normal* period would fix
(1) and leave (2) as honest error. Fitting it on 2022 would overcorrect and
produce bands too wide for a year resembling 2023.

---

## 2026-08-28 — Arm 2 (a flat base counts): no cell survives FDR

`config_hash = 185bba9a239c18f4`, `sma200_slope_min = -0.01` against arm 1's
`0.0`. Everything else identical.

### The result

**Zero cells survive FDR at q ≤ 0.10, on either split** — the same answer
arm 1 gave. `n_eff` barely moves:

| arm | split | cells | avg n_eff | max n_eff | survive FDR |
|---|---|---|---|---|---|
| arm1 `a38d3ca6b58295e8` | train | 256 | 128.1 | 927.0 | **0** |
| arm1 | validate | 256 | 16.6 | 122.0 | **0** |
| arm2 `185bba9a239c18f4` | train | 256 | 126.8 | 917.0 | **0** |
| arm2 | validate | 256 | 16.5 | 123.0 | **0** |

**The arm did what it was supposed to do at the universe level** and it
made no statistical difference:

| | arm1 | arm2 |
|---|---|---|
| `in_trade` at 2026-06-30 | 246 | **253** (+7) |
| `in_watch` at 2026-06-30 | 45 | 48 |
| `in_trade` ticker-quarters, all history | 8,163 | **8,431** (+268, +3.3%) |
| events | 1,384,963 | 1,266,516 |

The event counts are **not** comparable: arm 1's accumulated from weeks of
nightly and poller runs across 1,336 tickers, while arm 2's come only from
its own backtest over 674. Universe membership is the honest comparison and
it moved in the expected direction.

**Reading.** ADR 112 found nothing surviving FDR on the current population;
widening the population by 3.3% does not change that. This is a null result
about the *criterion*, not about statistical power — `n_eff` is essentially
identical, so the extra names contributed events without concentrating them
anywhere.

### Benchmarks: read the job's own verdict, not a derived percentile

`cscan stats benchmarks` printed, for arm 2:

    train    high-breadth subset: 5191 events -- signal arm is AT OR BELOW
                                                 the null's 97.5th percentile
    validate high-breadth subset: 1370 events -- signal arm is ABOVE
                                                 the null's 97.5th percentile

Validate clearing while train does not is the opposite of the usual
direction and is the one interesting number in this arm.

**A derived percentile disagrees, and the job is right.** Averaging
`annualized_ret` over all 800 null replications and asking where the signal
arm falls gives 82.5 (train) and 87.5 (validate) for arm 2 — neither
clearing 97.5:

| | signal ann_ret | null p97.5 | percentile |
|---|---|---|---|
| arm1 train | 0.1111 | 0.1378 | 86.1 |
| arm1 validate | 0.0086 | 0.1020 | 78.4 |
| arm2 train | 0.1095 | 0.1440 | 82.5 |
| arm2 validate | 0.0338 | 0.1056 | 87.5 |

Those figures are over the **whole** population; the job's verdict is over
the **high-breadth subset** it names on the line above. They are answers to
different questions and the table is kept only so nobody re-derives it and
believes they have contradicted the job.

What both agree on: the signal arm beats the null's *mean* on every split
of both arms, and arm 2's validate is the strongest of the four (0.0338
against a null mean of -0.0269, Sharpe 0.127 against -0.214).

**This does not overturn the FDR result.** Zero cells survive at q <= 0.10.
The benchmark is a portfolio-level test over a subset; the cell grid is the
primary statistic and it is null.

### Phase wall clocks

| phase | wall clock | note |
|---|---|---|
| universe (66 quarters) | 17:00 | 29 runs; 7 quarters re-run after an incident |
| `backtest compute` (61 chunks) | 1:49:46 | 108.0s/chunk avg, 1,443,768 rows |
| `backtest finalize` | 3:24 | 1,266,516 rows |
| `backtest harness` | **9:06** | passed; see below |
| `path backfill` | 22:18 | 549,477 events, 6,026,249 path rows |
| `path peak-labels` | 1:01 | 443,496 rows |
| `rho` | 0:46 | |
| `cell_stats` (both splits) | 2:21 | |
| `benchmarks` (both splits) | 26:00 | |

**Two steps were missing from the arm runner of the day
(`scripts/rebuild_arms_2_3.sh`, deleted 2026-08-29 once `scripts/exit_sweep.py`
superseded it; the lesson carried over and that runner has both steps).** `backtest --phase compute` writes `fwd_ret_*d` but never
`fwd_window_days` or `peak_ret_*d` — those are nightly steps, so arm 1 had
them by accident of having been through nightlies and an arm built from
scratch does not. `cell_stats` then selects on `min_fwd_window_for(cfg)`,
selects nothing, and dies in `build_baselines` on `int(years.min())` with
"cannot convert float NaN to integer", naming neither the missing column
nor the missing step. Arm 2 hit exactly this: 722,104 train events, all
with `fwd_ret_5d`, none with `fwd_window_days`.

### The harness got 8x faster, measured

| run | checks | bar rows | ms/bar |
|---|---|---|---|
| arm 1, before | 4,472s | 4,880,717 | 0.916 |
| arm 2, after | **546s** | 2,628,123 | **0.208** |

**4.4x per bar row**, from `scan_candidates` (56.8s → 5.1s on a fixed
29,509-row sample, 11.25x, identical 16,218 events out). Confirmed on a
clean re-run — an earlier run overlapped the edits and could not be
attributed, so it was repeated on stable code before this number was
recorded.

---

## 2026-08-28 — the poller moved to the Pi, and research went quiet during a session

ADR 158, deployed and observed live rather than reasoned about.

### The observation that proves it

At 06:51, with the Pi polling and a full ablation arm running on the
workstation:

| store | `quotes_live` newest |
|---|---|
| **research** | 06:45:42 — frozen |
| **serving** | **06:51:21** — advancing |

Research's live tables stop dead at 06:45:42 and serving keeps moving.
**Research has no live writer during market hours**, which is the whole
change. The workstation ran arm 3 through the same window.

### What 06:45:42 is, and why it is not the Pi

A **second poller started on the workstation at 06:45:37**, 37 seconds
after the Pi's, and opened a `poll` run against research. No scheduled task
— a manually launched chain. Two pollers double-write: the Windows one
writes research *and* pushes to serving, the Pi one writes serving
natively.

The arm-3 gate caught it and refused to start, which is what a gate is for.
Killed at 06:50, its run row closed with the reason. The 06:45:42 rows are
the five seconds it ran.

**This is the failure mode to expect from the migration**, and it is silent
from the frontend: both pollers write serving, so the site looks fine while
two processes fight over the same rows. `CLAUDE.md` now says not to start
`wait_and_poll.ps1`, and it is the first thing to check if live data ever
looks doubled.

### Verified before enabling

Three of these would have failed quietly:

- **`pull_live_records` is idempotent.** Run twice against live data: 3
  runs, 698 reports, 3 sessions, and research's totals unmoved the second
  time (1,656 / 18 / 34).
- **The staleness guard passes on a real one-day lag** — watermark
  2026-08-27 against a last trading day of 2026-08-28. That is the
  legitimate case: nightly syncs after the close, so serving holds the
  previous day during a session.
- **The Pi was holding arm 3's config.** `cscan poll` resolves config, so
  its poller would have written events under `fda16796c6e82ee4` while the
  site reads `serving_config` at `a38d3ca6b58295e8` — and shown nothing.
  Reverted before the timer was enabled.
- The Pi's clock is `America/Los_Angeles`, matching the workstation to the
  second, so `06:45` in the wrapper is 06:45 PT.
- Yahoo is reachable from the Pi: a bare `curl` returns 429, but the real
  `fetch_quotes` path returns live rows (AAPL 318.06, MSFT 507.80 at
  06:36). `yfinance` establishes its own session; the 429 was a red
  herring.

### Pi vs workstation, measured

Same commands, same data, differing only in machine:

| step | workstation | Pi | ratio |
|---|---|---|---|
| `stats rho` | 0:40 | 3:28 | 5.2x |
| `cell_stats` train | 1:03 | 5:02 | 4.8x |
| `cell_stats` validate | 0:19 | 1:59 | 6.3x |
| `universe`, one quarter | ~24s | ~99s | 4.1x |

**~5x slower**, on 4 Cortex-A72 cores against 8 x86, with 1.3 GB free
against 32. That is why compute and the harness stay on the workstation:
they want 8 workers at ~820 MB each, which the Pi cannot give without
swapping to its SD card. The poller is the opposite shape — I/O bound and
rate-limited — so the 5x barely touches it.

---

## 2026-08-28 — Arm 3's universe: +36% trade, watch universe intact

`config_hash = fda16796c6e82ee4`. `required_criteria` names
`crit_rel_return_history` instead of `crit_rel_return` — ADR 014's 757-bar
history gate **without** its sector-median test. Compute is still running;
this records the universe pass, which is what the correction was about.

| | in_trade @ 2026-06-30 | in_watch | in_trade ticker-quarters, all history |
|---|---|---|---|
| arm1 baseline | 246 | 45 | 8,163 |
| arm2 flat base | 253 | 48 | 8,431 |
| **arm3 no median test** | **334** | **46** | **11,891** |

### Both halves of the correction are confirmed

**+88 names, +36%.** `REBUILD_ARMS.md` predicted +35% (184 → 248) for the
variant that dropped `crit_rel_return` *entirely*. Dropping only the
sector-median test gets +36%, so **the median comparison was carrying
essentially all of that filtering** — the history requirement was excluding
almost nobody by itself. That is the measurement the original spec could
not have produced, because it changed both halves at once.

**`in_watch` is 46 against arm1's 45 — intact.** This is why the arm was
respecified (user's correction, 2026-08-28). The original variant would
have taken the watch universe 45 → 36 as all nine `history`-route names
graduated to `in_trade` at once, since nothing would have been left to hold
them. Keeping the 757-bar gate keeps ADR 149's route firing, and the
measured 46 says it still is.

The +1 over arm1 is not noise to explain away: a wider trade universe
changes which names are *excluded* from it, and `watch_reason` returns
`None` for anything already qualifying to trade.

### Why this could not have been a config flag

`config_hash` is `sha256(asdict(Config))`, so adding a config *field* moves
the hash at its default value. Implemented that way first and measured: the
default went `a38d3ca6b58295e8` -> `be4e4702241ce90c`, which orphans a
built and harness-passed generation, leaves `serving_config` pinned to a
hash the code no longer produces, and has nightly writing events the site
cannot see.

Naming a second criterion instead means arm 3 changes the **value** of the
existing `required_criteria` tuple: the hash moves for the arm and for
nobody else. `test_rel_return_median_flag.py` pins that the default is
still `a38d3ca6b58295e8`.

### Run context

**This arm ran on the workstation while the Pi polled a live session** —
the first time that has been possible (ADR 158). Research had no live
writer; the poller's ticks and the arm's 61 compute chunks did not contend.

---

## 2026-08-28 — Arm 3 complete, and the three-arm ablation closes null

`config_hash = fda16796c6e82ee4`. Harness **passed** (10:53, 1,890,488
events), so ADR 059's gate is satisfied and these numbers are readable.

### All three arms: zero cells survive FDR

| arm | in_trade @ 2026-06-30 | avg n_eff | max n_eff | survive FDR | min q |
|---|---|---|---|---|---|
| arm1 baseline | 246 | 128.1 | 927 | **0** | 0.6209 |
| arm2 flat base | 253 | 126.8 | 917 | **0** | 0.6902 |
| arm3 no median | **334** | **143.0** | **1026** | **0** | 0.7140 |

*(train split; validate is the same answer at 16.6 / 16.5 / 18.5 n_eff.)*

**This is not a power problem.** Arm 3 carries the most statistical power
of the three — `n_eff` up 12% on the baseline, max up 11%, from a universe
36% wider at the snapshot and 46% wider across all history — and its best
q-value is **worse** than the baseline's. Every min q is 6-7x the 0.10
threshold; nothing is near surviving.

ADR 112 found nothing on the baseline population. Three different
relaxations of the universe definition do not change that, and the one that
admitted the most names moved it the wrong way.

### The widest universe performed worst out of sample

| arm | validate signal | validate null | signal Sharpe | null Sharpe |
|---|---|---|---|---|
| arm1 | +0.86% | −3.05% | −0.078 | −0.239 |
| arm2 | +3.38% | −2.69% | +0.127 | −0.214 |
| **arm3** | **−1.48%** | −1.98% | **−0.264** | −0.190 |

Arm 3 is the only arm whose validate signal return is negative, and the
only one whose signal Sharpe is **worse than its own null**. Ordering the
arms by universe width gives arm3 > arm2 > arm1; ordering by validate
return gives arm2 > arm1 > arm3.

**Read this as a caution, not a finding.** Three arms is not a sample, the
validate split's `n_eff` is ~18 against train's ~143, and all three
intervals are wide. What it does rule out is the hypothesis that motivated
the ablation — that the universe filters were excluding tradeable names and
suppressing an edge. Removing the sector-median comparison admitted 88 more
names and made out-of-sample results worse, not better.

Train tells the opposite story in all three arms (signal beats null on both
return and Sharpe), which is what an in-sample fit does and is why the
split exists.

### Phase wall clocks

| phase | wall clock | output |
|---|---|---|
| universe (66 quarters) | 25:22 | 78,204 rows |
| `backtest compute` (61 chunks) | 1:53:45 | 1,890,488 rows |
| `backtest finalize` | 5:14 | 1,890,488 rows |
| `backtest harness` | **10:53** | passed |
| `path backfill` | 24:58 | 8,981,053 path rows |
| `path peak-labels` | 2:13 | 710,710 rows |
| `rho` + `cell_stats` x2 | 2:01 | 512 cells |
| `benchmarks` x2 | 31:41 | 818 rows |
| **total** | **~3h36m** | |

**Run entirely while the Pi polled a live session** (ADR 158). The poller
completed 47 ticks through the same window with no contention on research,
which is the first time an arm and a session have overlapped.

### The harness speedup, across all three arms

| arm | events | harness checks |
|---|---|---|
| arm1 (pre-`scan_candidates`) | 1,384,963 | 4,472s |
| arm2 | 1,266,516 | 546s |
| arm3 | **1,890,488** | **654s** |

Arm 3 processes 49% more events than arm 2 for 20% more check time, and
under a sixth of arm 1's despite carrying 36% more events.

### Why the relaxed universe did worse: a cost threshold, not stock quality

Prompted by the obvious objection to the section above — the backtest is
side-symmetric, so if arm 3 simply admitted names that fell, the short side
should have collected it. That objection is correct, and it rules out the
first explanation.

Classifying every arm-3 event by whether its ticker passed the sector-median
test **at that event's own signal date** (validate split):

| | events | gross | net | cost drag | ATR/price |
|---|---|---|---|---|---|
| passed median | 72,690 | **0.00097** | +0.00034 | 0.00063 | 0.0269 |
| failed median | 23,875 | **0.00042** | −0.00021 | 0.00063 | 0.0241 |

**Cost drag is identical to the basis point: 6.3 bp for both.** So costs do
not discriminate. Volatility does not either — the failing names are
slightly *less* volatile. What differs is the **gross** edge, 4.2 bp against
9.7 bp, less than half, before anything is deducted. Mean MFE is smaller
too (0.02284 against 0.02501), so the trades simply travel less far.

Both groups clear zero gross. Only one clears costs:

    passed:  9.7 gross - 6.3 cost = +3.4 bp net
    failed:  4.2 gross - 6.3 cost = -2.1 bp net

**The sector-median filter was selecting for edge-larger-than-fee, not for
company quality.** That reading survives the symmetry objection: the short
side shows the same halved gross edge rather than an inverted one, so it is
not a directional-drift story. Broken out by side on validate, both sides
are worse for the failing group (long −0.00034, short −0.00010) while both
are better for the passing group (long +0.00006, short +0.00062).

**Two things stop this from being a finding.** The passing group's +3.4 bp
still produced zero FDR survivors, so this explains a difference between two
populations that both fail. And with `mean_cofire` near 46 these event
counts overstate independent evidence by roughly 10-20x, which is why no
p-value is quoted here — a significance test on raw counts is the exact
error `n_eff` exists to prevent (invariant 8).

**Method note.** The first cut of this analysis was wrong and is recorded so
the mistake is not repeated: it grouped tickers by whether they *ever*
failed the median across 66 quarters, which made the comparison group
"names that never failed once in sixteen years" — a survivor élite that
would look better regardless. The 485-vs-98 ticker split was the tell. The
table above is point-in-time, joining each event to the universe evaluation
in force at its signal date.

---

## 2026-08-28 — Exit sweep, first arm: a tighter stop is worse

`t4_fix2` = target 4%, **fixed 2% stop** instead of the default ATR k=1.5.
Config `f7b31c5443d30948`. Harness passed (661s, all five checks). It is the
only arm in the grid that changes exactly one knob from the live config, so
it answers "does a tighter stop help" on its own.

| | validate `net_ret` | stopped out | hit target |
|---|---|---|---|
| baseline, 4% + ATR 1.5 | **+0.00040** | 33.0% | 31.7% |
| `t4_fix2`, 4% + fixed 2% | **−0.00030** | **53.9%** | 26.3% |

**It flips a small positive into a small negative**, and the mechanism is
visible in the exit mix rather than inferred: the stop fires on 54% of
trades instead of 33%. That is what the pre-sweep MAE analysis predicted —
44.8% of *timeout* trades dip past −2% while the position is open, so a 2%
stop converts a population of harmless near-breakeven trades into realised
2% losses. The extra winners it protects do not pay for that.

Train agrees in direction: −0.00050 → −0.00076.

### The sweep's original selection metric was wrong, and this arm proved it

The runner was written to select on `q_value` from `cell_stats`. That cannot
work for an exit-policy sweep. `research/cell_stats.py::hit_flags` computes
`p_hit` from `fwd_ret_{horizon}d` — the raw forward return over a fixed
window — which is a property of the market and carries no dependence on
`ExitParams` at all.

The first arm demonstrates it exactly:

    avg(fwd_ret_5d)   baseline -0.00019   t4_fix2 -0.00019   identical
    avg(net_ret)      baseline +0.00040   t4_fix2 -0.00030   moved
    min q (validate)  baseline  0.6772    t4_fix2  0.6772    identical

Eleven arms would have been compared on a number blind to the only variable
being swept, and all eleven would have "tied".

**The responsive metrics are `events.net_ret`, the `exit_reason` mix, and
`benchmarks`** — the last being signal against its own null *under that exit
policy*, which is the rigorous form of this comparison and is worth running
on finalists rather than on all eleven at ~32 min each. `cell_stats` is
still computed per arm at ~2 min because `n_eff` and `rho` are worth having,
but it is not the selection metric.

**Nothing about the FDR result changes.** Zero cells survive in every arm,
for the same reason: `cell_stats` is measuring the same forward returns each
time. The exit sweep is a different question — how much of a fixed market
move the policy captures — and it has to be read off returns.

### 2026-08-28 — The target axis: raising it above 4% helps, and the splits disagree on where it peaks

Three of the four ATR-stop arms are in. The stop is unchanged across them, so
this is the target axis alone.

| target (ATR k=1.5) | validate net | validate gross | hit target | timeout | hold |
|---|---|---|---|---|---|
| **4% — the live config** | +4.0 bp | 10.3 | 31.7% | 33.1% | 3.35 |
| 5% | +4.4 bp | 10.7 | 23.1% | 39.8% | 3.58 |
| 7% | **+7.0 bp** | **13.3** | 12.4% | 47.5% | 3.81 |

**On validate the curve rises monotonically and 7% is 75% better than the
live config.** It is a *gross* improvement, 10.3 to 13.3, so the policy is
capturing more of the move rather than merely paying less cost. The stop-out
rate barely moves (33.0 / 33.5 / 33.9), which is the control working: the ATR
stop is doing the same job in all three, and only the target changed.

That is the mechanism the pre-sweep path analysis predicted. **74.6% of
trades that exit at 4% go on to touch 5%**, and the 4% target was banking a
certain small win instead of them.

**Train does not agree on the peak, and train is the selection split:**

    4%   -5.0 bp
    5%   -4.1 bp   <- best
    7%   -4.3 bp

5% and 7% are 0.2 bp apart on train, which is noise, and both beat 4%. So the
claim the data supports is narrower than the validate column suggests:
**raising the target above 4% helps on both splits; where it peaks is not
resolved.** Taking 7% because validate prefers it is the exact cherry-pick
the split exists to prevent.

`t6_atr15` fills the gap at 6% and is queued. If the train curve really is
flat from 5% to 7%, the choice has to be made on a reason rather than on a
ranking — a longer hold is more exposure per trade, and 47.5% of trades
reaching the 5-day timeout at 7% means the policy increasingly depends on
`max_hold_days` rather than on the target itself.

### 2026-08-28 — Six arms: the stop dominates, and adaptiveness is worth more than width

Ranked on `train`, which is the selection split:

| arm | train | validate | stopped out |
|---|---|---|---|
| 5% + ATR k=1.5 | **−4.1** | +4.4 | 33.5% |
| 7% + ATR k=1.5 | −4.3 | **+7.0** | 33.9% |
| 4% + ATR k=1.5 *(live)* | −5.0 | +4.0 | 33.0% |
| 6% + fixed 3% | −6.2 | +0.9 | 41.8% |
| 5% + fixed 2% | −6.8 | −2.6 | 55.2% |
| 4% + fixed 2% | −7.6 | −3.0 | 53.9% |

**Every ATR arm beats every fixed-stop arm, on both splits.** Perfect
separation with no overlap, and the ordering tracks the stop-out rate
exactly: 33% for ATR, 42% for fixed 3%, 54% for fixed 2%. At 3-4 bp this is
the largest effect in the grid.

**What the live stop actually is.** `stop_mode="atr"`, `stop_atr_k=1.5` —
adaptive, not a fixed percentage. Measured across validate events:

    median 3.60%   mean 4.09%   p10 2.35%   p90 6.34%

**So the fixed 3% arm sits close to the ATR median and still loses to it**
(−6.2 against −4.1). That is the sharper finding: it is not merely that the
ATR stop is wider on average, it is that **the adaptiveness itself pays.** A
flat 3% is too tight on a volatile name and too loose on a calm one, while
1.5 x ATR widens exactly where the noise is.

**Raising the target replicates across stop regimes.** 5% beats 4% under ATR
(−4.1 against −5.0) *and* under fixed 2% (−6.8 against −7.6). An effect that
appears independently in two different stop regimes is much harder to
dismiss as noise than a single comparison, which is all the earlier entry
had.

**Implied next test, and it is small.** `stop_atr_k` carries
`# swept 1.0-2.5 (ADR 008)` in `core/config.py` and that sweep has never
run. If "looser is better" holds, **k=2.0 keeps the adaptiveness and just
widens it** — three arms at targets 5/6/7, not another eleven.

### 2026-08-29 — The target effect replicates across stop regimes

The fixed-2% row finished, and it has the same shape as the ATR row at a
different level:

| target | ATR k=1.5 train / validate | fixed 2% train / validate |
|---|---|---|
| 4% | −5.0 / +4.0 | −7.6 / −3.0 |
| 5% | **−4.1** / +4.4 | **−6.8** / −2.6 |
| 7% | −4.3 / **+7.0** | **−6.8** / **−0.2** |

In both regimes: **4% is the worst target on train, 5% and 7% tie ahead of
it, and validate rises monotonically 4 → 5 → 7.** The two stop settings
produce very different populations — 33% stopped out against 55% — so an
effect that survives both is not an artifact of one exit path.

**The level is set by the stop, the slope by the target.** Every fixed-2% arm
is negative at every target; every ATR arm is positive. Raising the target
lifts a row by roughly 3 bp on validate; changing the stop moves between rows
by 7 bp.

**What is still unresolved is where the target peaks.** Train puts 5% and 7%
within 0.2 bp of each other in both rows, which is noise. Validate prefers 7%
in both rows, but validate is not the selection split and preferring it there
is the cherry-pick the split exists to prevent. `t6_atr15` and `t6_fix2` fill
the 6% column and are the last evidence that can settle it.

### 2026-08-29 — The complete ATR target curve: 4% is too tight, and above that it is flat

| target (ATR k=1.5) | train | validate | gross | hit target | timeout | hold |
|---|---|---|---|---|---|---|
| **4% — the live config** | **−5.0** | +4.0 | 10.3 | 31.7% | 33.1% | 3.35 |
| **5%** | **−4.1** | +4.4 | 10.7 | 23.1% | 39.8% | 3.58 |
| 6% | −4.2 | +6.1 | 12.4 | 17.0% | 44.3% | 3.72 |
| 7% | −4.3 | +7.0 | 13.3 | 12.4% | 47.5% | 3.81 |

**Train shows a step, not a slope.** Moving off 4% is worth 0.9 bp; after
that 5%, 6% and 7% sit within **0.2 bp** of each other, which is noise. The
supported claim is *"4% is too tight"*, not *"higher is better"*. Validate
does rise monotonically to +7.0, and that is precisely the reading the split
exists to stop us acting on.

**Selection, on train as pre-committed: 5%.** It wins the train column, and
validate independently agrees it beats the incumbent (+4.4 against +4.0).
Gross rises with it (10.7 against 10.3), so it is capture rather than cost.

**A mechanism argument points the same way.** At 7% only 12.4% of trades
reach the target while **47.5% exit on the 5-day timeout** — the policy has
quietly become "hold five days and take what is there", which is
`max_hold_days` doing the work rather than `target_pct`. That is a different
parameter than the one swept, and it is not evidence for a 7% target.

**What this does not say.** Nothing here has passed a significance test; the
whole grid lives inside a few basis points; and `cell_stats` FDR cannot speak
to exit policy at all (see the entry above). The claim is a ranking on one
metric, not a demonstration of edge.

### 2026-08-29 — Train and validate disagree the same way in all three rows

With the fixed-3% row finished, the target axis can be read in all three stop
regimes at once:

| stop | train, 5 → 6 → 7% | validate, 5 → 6 → 7% |
|---|---|---|
| ATR k=1.5 | −4.1 / −4.2 / −4.3 | +4.4 / +6.1 / +7.0 |
| fixed 2% | −6.8 / — / −6.8 | −2.6 / — / −0.2 |
| fixed 3% | −6.2 / −6.2 / −6.3 | −1.2 / +0.9 / +1.8 |

**Train is flat above 5% in every row. Validate rises in every row.** Three
independent stop regimes, producing populations that stop out at 33%, 42% and
55%, and the two splits disagree the same way in all of them.

**That consistency is the point.** Random noise does not disagree in the same
direction three times. Either the validate period genuinely rewards letting
winners run, or something systematic distinguishes the splits — a regime
difference, or an interaction with `max_hold_days` that shows up in one era
and not the other.

**This sweep cannot resolve it**, and it should not be resolved by preferring
the split that gives the nicer answer. Two things would:

- **Split validate by era** and see whether the rise is concentrated in one
  period. `events.era` already exists and `cell_stats` is computed per era, so
  this costs a query rather than a rebuild.
- **Sweep `max_hold_days`.** At a 7% target 47.5% of trades exit on the 5-day
  timeout, so the target and the holding window are entangled; the rise on
  validate may belong to the window rather than the target.

Selection is unchanged and stays on train: **5% target, ATR stop unchanged.**
The train column cannot separate 5/6/7, and 5% is both its nominal winner and
the smallest change from the live configuration.

### 2026-08-29 — The flat train curve was an averaging artifact, and 2020-2021 is a hole

`events.era` splits the target curve by period, and the picture changes
completely. Validate net return, basis points, ATR stop throughout:

| period | 4% | 5% | 6% | 7% | shape |
|---|---|---|---|---|---|
| 2010-2014 (train) | +3.3 | +5.8 | +6.3 | **+6.9** | rises |
| 2015-2019 (train) | −0.4 | 0.0 | +0.6 | **+0.7** | rises |
| **2020-2021 (train)** | **−17.7** | −17.2 | −18.9 | **−19.6** | **falls** |
| 2022-2023 (validate) | +4.0 | +4.4 | +6.1 | **+7.0** | rises |

**Raising the target helps in three periods out of four.** The train
aggregate looked flat only because it averages three rising periods against
2020-2021, where the ordering inverts. The flatness was a property of the
average, not of the strategy, and the earlier entry reporting it as "train is
flat above 5%" should be read with this.

**The train/validate disagreement was never a disagreement.** Validate is
entirely 2022-2023 (193,772 events); train spans 2010-2014, 2015-2019 and
2020-2021. Validate behaves like the two older eras. **2020-2021 is the
outlier**, not the held-out split.

**2020-2021 is a −17.7 bp hole and that is the larger finding here.** No
other period is positive by even half that magnitude. Whatever the exit
policy, this strategy lost heavily through the COVID crash and the recovery
that followed, and a sweep of exit parameters cannot fix a period where every
arm loses 17 to 20 basis points. That deserves its own investigation.

**Selection is unchanged: 5% target, ATR stop.** Not because the train
average said so — that average is now known to be a blend — but because it is
the conservative common choice: it improves on 4% in all four periods, it is
the smallest change from the live configuration, and choosing 7% would be
selecting the value that is best in the three periods that already work and
worst in the one that does not.

**What this opens.** The era breakdown was one query against data that already
existed, and it changed the reading of a 24-hour sweep. Any future parameter
result should be checked per era before it is believed in aggregate.

---

## 2026-08-29 — Exit sweep complete: 11 arms, and the answer is the target, not the stop

Eleven full backtests over the same universe, the same signals and the same
dates. Only `ExitParams` changed, so every difference is the exit policy.
Ranked on `train`, the selection split:

| arm | train | validate | stopped out |
|---|---|---|---|
| **5% + ATR k=1.5** | **−4.1** | +4.4 | 33.5% |
| 6% + ATR | −4.2 | +6.1 | 33.8% |
| 7% + ATR | −4.3 | +7.0 | 33.9% |
| 4% + ATR — **the live config** | −5.0 | +4.0 | 33.0% |
| 5% + fixed 3% | −6.2 | −1.2 | 41.3% |
| 6% + fixed 3% | −6.2 | +0.9 | 41.8% |
| 7% + fixed 3% | −6.3 | +1.8 | 42.1% |
| 5% + fixed 2% | −6.8 | −2.6 | 55.2% |
| 6% + fixed 2% | −6.8 | −0.9 | 55.9% |
| 7% + fixed 2% | −6.8 | −0.2 | 56.4% |
| 4% + fixed 2% | −7.6 | −3.0 | 53.9% |

**The grid separates into three tiers by stop, with no overlap at all.** Every
ATR arm (−4.1 to −5.0) beats every fixed-3% arm (−6.2 to −6.3), which beats
every fixed-2% arm (−6.8 to −7.6). The ordering tracks the stop-out rate
exactly: 33%, 42%, 55%. **The stop is worth 2-3 bp; the target is worth
about 1 bp.**

### The decision

**Change `target_pct` from 0.04 to 0.05. Leave the stop alone.**

- 5% + ATR wins the train column outright.
- Validate independently agrees it beats the incumbent (+4.4 against +4.0).
- It improves on 4% in **all four eras** (see the era entry above), which 6%
  and 7% do not.
- It is the smallest change from the live configuration.

**Not 7%, despite validate liking it most.** Validate is not the selection
split, and 7% is best precisely in the periods that already work and worst in
2020-2021, the one that does not. At a 7% target 47.5% of trades exit on the
5-day timeout rather than the target, so `max_hold_days` would be doing the
work — a parameter this sweep never varied.

### What the sweep cannot say

- **No significance test has been passed.** The whole grid lives inside 11
  basis points, and `cell_stats` FDR is blind to exit policy by construction
  (`hit_flags` reads `fwd_ret_5d`, a fixed-window market fact).
- **Eleven arms is eleven comparisons.** Selecting on train and confirming
  once on validate is the protection used here; it is not a p-value.
- **`stop_atr_k` was never swept.** It carries `# swept 1.0-2.5 (ADR 008)` in
  `core/config.py` and never has been. Given that looser stops won every
  comparison here, **k=2.0 is the obvious next test** — three arms, not
  eleven.

### The finding nobody asked for

**2020-2021 loses ~18 bp per trade under every one of the eleven arms.** No
exit policy in this grid comes close to fixing it, and it is five times larger
than any effect the sweep set out to measure. That belongs in its own
investigation, and it matters more than the target.

---

## Exit sweep — reference tables (2026-08-29)

One place to find the numbers, since the discussion above is spread over
several entries. Everything is mean net return per event in **basis points**
(1 bp = 0.01%). Live report: the artifact published 2026-08-29.

### The grid, ranked on train

`train` is the selection split. `validate` is held out and shown, not chosen
on.

| arm | target | stop | train | validate | stopped out |
|---|---|---|---|---|---|
| **t5_atr15 — SELECTED** | 5% | ATR k=1.5 | **−4.1** | +4.4 | 33.5% |
| t6_atr15 | 6% | ATR k=1.5 | −4.2 | +6.1 | 33.8% |
| t7_atr15 | 7% | ATR k=1.5 | −4.3 | +7.0 | 33.9% |
| t4_atr15 — **live config** | 4% | ATR k=1.5 | −5.0 | +4.0 | 33.0% |
| t5_fix3 | 5% | fixed 3% | −6.2 | −1.2 | 41.3% |
| t6_fix3 | 6% | fixed 3% | −6.2 | +0.9 | 41.8% |
| t7_fix3 | 7% | fixed 3% | −6.3 | +1.8 | 42.1% |
| t5_fix2 | 5% | fixed 2% | −6.8 | −2.6 | 55.2% |
| t6_fix2 | 6% | fixed 2% | −6.8 | −0.9 | 55.9% |
| t7_fix2 | 7% | fixed 2% | −6.8 | −0.2 | 56.4% |
| t4_fix2 | 4% | fixed 2% | −7.6 | −3.0 | 53.9% |

Three tiers by stop, **no overlap**: ATR (−4.1 to −5.0), fixed 3% (−6.2 to
−6.3), fixed 2% (−6.8 to −7.6), ordered exactly by stop-out rate.
`t4_fix3` was dropped from the grid and never run.

### The target curve by era, ATR stop

The table that changed the reading. Net return, basis points:

| period | 4% | 5% | 6% | 7% | shape |
|---|---|---|---|---|---|
| 2010-2014 (train) | +3.3 | +5.8 | +6.3 | +6.9 | rises |
| 2015-2019 (train) | −0.4 | 0.0 | +0.6 | +0.7 | rises |
| **2020-2021 (train)** | **−17.7** | −17.2 | −18.9 | −19.6 | **falls** |
| 2022-2023 (validate) | +4.0 | +4.4 | +6.1 | +7.0 | rises |

### What is NOT true, stated plainly

- **The selected arm still loses money on train.** −4.1 bp is the *least
  negative* of eleven; every arm is negative on that split. The sweep
  improved a losing configuration, it did not produce a winning one.
- **Zero cells survive FDR, in every arm.** 0 of 256, min q ≈ 0.64 on train
  and 0.6772 on validate, against a 0.10 threshold — and **identical across
  arms**, because `cell_stats` derives `p_hit` from `fwd_ret_5d`, a
  fixed-window market fact with no dependence on exit policy. The exit sweep
  and the FDR question are measuring different things; neither answers the
  other.
- **Nothing here passed a significance test.** The whole grid lives inside 11
  basis points and the ranking is a ranking, not evidence of edge.
- **The stop was never widened.** `stop_atr_k` stayed at 1.5 throughout; it
  carries `# swept 1.0-2.5 (ADR 008)` in `core/config.py` and still has never
  been swept. Looser stops won every comparison here, so **k=2.0 is the
  obvious next test.**
- **2020-2021 loses ~18 bp per trade under all eleven arms.** Larger than
  anything the sweep set out to measure, and untouched by any exit setting.

### The stop-width ladder (second sweep, added below)

The grid above holds `stop_atr_k` at 1.5 in every arm. A second sweep varied
it, and it is the dominant parameter — **9 bp from tightest to none against
about 1 bp for the target**. All at a 5% target:

| stop | mean stop | train | validate | stopped | worst trade |
|---|---|---|---|---|---|
| none (control) | — | **+2.2** | +11.0 | 0.0% | −50.34% |
| **ATR k=2.0 — SHIPPED** | 5.46% | **−2.8** | +7.5 | 21.4% | −39.55% |
| ATR k=1.5 (was live) | 4.09% | −4.1 | +4.4 | 33.5% | −39.55% |
| fixed 3% | 3.00% | −6.2 | −1.2 | 41.3% | — |
| fixed 2% | 2.00% | −6.8 | −2.6 | 55.2% | — |

### The decision, as applied 2026-08-29

`ExitParams.target_pct` 0.04 → **0.05** and `stop_atr_k` 1.5 → **2.0**.
`stop_mode` and `max_hold_days` unchanged. New hash **`0523841076f47293`**,
which the sweep had already built as `t5_atr20`, so no rebuild was needed —
config edit, `cscan db sync-config`, and a full `cscan sync`.

Not the stopless control, despite it winning on mean in every era: it costs
11 points of worst case and leaves 72.6% of trades exiting on the five-day
timeout, making `max_hold_days` the real policy. That parameter is swept
separately (third sweep, `h3` / `h10`).

**The live report artifact was retired 2026-08-29** once every number it
carried was recorded here. `scripts/sweep_report.py` regenerates it from the
database if it is ever wanted again.

---

## 2026-08-29 — Second sweep: the stop width matters more than the target

The first sweep never varied `stop_atr_k`; it sat at 1.5 in all eleven arms.
That default is a 4.09% mean stop. k=2.0 is 5.46%.

| | k=1.5 train | k=2.0 train | k=1.5 val | k=2.0 val |
|---|---|---|---|---|
| 5% target | −4.1 | **−2.8** | +4.4 | **+7.5** |
| 7% target | −4.3 | **−3.0** | +7.0 | **+10.2** |

**+1.3 bp on train at both targets — identical to a tenth of a basis
point** — and +3.1 / +3.2 on validate. Stop-out rate falls from ~33.5% to
~21.4% in both. An effect that reproduces to that precision across two
targets is the most solid result either sweep has produced.

**It is larger than the target effect.** Against the live configuration
(4% + k=1.5, train −5.0):

    target 4% -> 5%      +0.9 bp
    stop k=1.5 -> 2.0    +1.3 bp
    both                 +2.2 bp   (train −5.0 -> −2.8)

**The mechanism is avoided losses, not extra winners.** Target hits are flat
(23.1% against 23.5%); stop-outs fall twelve points and become timeouts. The
wider stop is not catching more upside, it is **not killing trades that would
have recovered** — which is exactly what the pre-sweep MAE analysis predicted
and what the first sweep's fixed-2% disaster showed from the other side.

**Yesterday's selection is superseded.** 5% + k=1.5 was chosen because `k`
had never been varied. Now that it has, **5% + k=2.0 dominates it on both
splits**, and 7% + k=2.0 is better still on validate while marginally worse
on train.

**`t5_nostop` is the open question.** Every comparison in both sweeps has
said looser is better, monotonically. If removing the stop entirely beats
k=2.0, the finding is not "widen the stop" but "the stop is costing money",
which is a materially different claim and would change what a return model
should be built on top of.

---

## 2026-08-29 — The control arm wins: the stop is costing money

`t5_nostop` (`stop_mode="none"`, the control `core/exits.py:45` names as one
per ADR 008) completed last. All five stop settings at a 5% target:

| stop | train | validate | stopped out | hit target | timed out |
|---|---|---|---|---|---|
| **none** | **+2.2** | **+11.0** | 0.0% | 23.7% | 72.6% |
| ATR k=2.0 | −2.8 | +7.5 | 21.4% | 23.5% | 51.5% |
| ATR k=1.5 — **live** | −4.1 | +4.4 | 33.5% | 23.1% | 39.8% |
| fixed 3% | −6.2 | −1.2 | 41.3% | 21.0% | 34.3% |
| fixed 2% | −6.8 | −2.6 | 55.2% | 19.2% | 22.3% |

**Monotonic across all five settings, on both splits, with no turnover.**
Every loosening helps, and **no stop is the only configuration in fourteen
arms that is positive on train.**

**The mechanism is not ambiguous.** Target hits move only 19.2% → 23.7%
across the entire range while stop-outs go 55% → 0% and become timeouts. The
stop was never protecting winners from giving back gains — it was converting
trades that would have recovered into realised losses. That is the same
mechanism the pre-sweep MAE analysis predicted: 44.8% of *timeout* trades dip
below −2% while open, so any stop inside that band harvests noise.

**Scale: the stop is worth ~9 bp from tightest to none. The target was worth
~1 bp.** It is the dominant exit parameter by an order of magnitude, and it
had never been varied before 2026-08-29.

### This should not be shipped as "remove the stop"

The result is clean but the conclusion is uncomfortable, and three things
argue against acting on it directly:

- **72.6% of trades now exit on the 5-day timeout.** With no stop the policy
  is barely an exit policy: it is "hold five days, take 5% if you get it".
  `max_hold_days` is doing the work, and it has never been swept. The honest
  next test is `max_hold_days` at 3 / 5 / 10, not shipping a stopless config.
- **No stop means unbounded per-trade loss, and the tail was measured after
  the fact.** Validate, `net_ret`:

  | stop | mean | p1 | p0.1 | worst | sd |
  |---|---|---|---|---|---|
  | k=1.5 | +4.4 bp | −9.11% | −15.33% | **−39.55%** | 4.01% |
  | k=2.0 | +7.5 bp | −10.35% | −18.81% | **−39.55%** | 4.20% |
  | none | +11.0 bp | −12.61% | **−22.62%** | **−50.34%** | 4.40% |

  Going stopless buys 6.6 bp of mean and costs 11 points of worst case. **k=2.0's
  worst trade is identical to k=1.5's** — the same trade gapped through both
  stops, so widening 1.5 → 2.0 gains 3.1 bp of mean at no cost in the extreme
  tail. That is a materially better trade than removing the stop.
- **2020-2021 still loses ~18 bp under every arm** and is the period where a
  stop would matter most. The era breakdown for the no-stop arm has not been
  run.

**Recommended, and deliberately conservative: 5% target + ATR k=2.0.** It
captures most of the available gain (train −5.0 → −2.8 from live), keeps a
real stop, and does not rest on a limit case whose tail behaviour is
unmeasured.

### 2026-08-29 — The no-stop arm by era: it wins everywhere, including the crash

The open question after the second sweep was whether the stopless arm's win
was concentrated in the calm periods, with 2020-2021 — where every arm loses
~18 bp — being where a stop finally earns its keep. It is not.

| period | k=1.5 | k=2.0 | no stop |
|---|---|---|---|
| 2010-2014 (train) | +5.8 | +7.3 | **+14.0** |
| 2015-2019 (train) | 0.0 | +2.2 | **+8.7** |
| **2020-2021 (train)** | −17.2 | −17.3 | **−15.6** |
| 2022-2023 (validate) | +4.4 | +7.5 | **+11.0** |

**No stop is best in all four periods, including the crash era**, where it is
*less bad* rather than worse. The monotonic ordering holds inside every era,
not only in aggregate.

**This removes the main argument for keeping a stop.** The case made when the
result first landed was that 2020-2021 is where a stop should matter and its
era split had not been run. It has now, and the stop does not help there
either.

**What still argues against shipping stopless**, and it is now the whole
case:

- **Tail.** Worst trade −50.34% against −39.55%; p0.1 −22.62% against
  −18.81%. The sweep optimised means.
- **72.6% of exits become the 5-day timeout**, so the policy becomes
  `max_hold_days` rather than an exit rule. That parameter has never been
  swept, and until it is, "the stop is costing money" and "five days is the
  wrong window" are not separable.

The shipped config stays 5% + k=2.0. The `max_hold_days` sweep is what
decides whether stopless is genuinely right or an artifact of an untested
holding window.

### 2026-08-29 — The cluster-head filter discards 74% of fires, and the discarded ones do better

`cell_stats` filters `is_cluster_head`, keeping only the first fire of a
cluster. Measured on the shipped config:

| split | all fires | kept | discarded | % dropped |
|---|---|---|---|---|
| train | 791,524 | 205,184 | 586,340 | **74.1%** |
| validate | 193,628 | 49,912 | 143,716 | **74.2%** |

And the discarded ones are the better trades:

| split | first fire (counted) | repeat fires (dropped) | gap |
|---|---|---|---|
| train | **−7.9 bp** | **−1.0 bp** | 6.9 |
| validate | **+0.8 bp** | **+9.7 bp** | 8.9 |

**Not a different kind of trade.** The exit mix is nearly identical — stop
21.7% against 22.6%, target 16.5% against 15.5% — so this is the same
behaviour producing better returns, not a population that exits differently.

**The mechanism is the one the original design intended.** A repeat fire
means the signal persisted or deepened while a position was already open: a
second lower-band touch after a further drop is a better entry price for the
same setup. Averaging down is working, and the filter throws it away.

**The gap is larger than anything the exit sweep found.** The whole
target × stop grid spans 11 bp; this is 7-9 bp from a filter nobody was
treating as a parameter. Every number in both sweeps is measured on the
*worse* quarter of fires.

**ADR 151's statistical objection still stands, and the two are not in
conflict.** Dropping the filter takes train `n` from 78k to 311k and narrows
every interval by roughly 2x, while the added observations are serially
dependent by construction — the direction that manufactures significance.
But that is an argument about `cell_stats`, not about returns.

**The resolution is that they are different uses of the same table:**

- **Backtest returns should count every fire.** They are real trades with
  real money attached, and excluding three quarters of them understates the
  strategy by 7-9 bp.
- **`cell_stats` should keep the filter** until a serial `n_eff` exists,
  matching what `rho` already does cross-sectionally (ADR 098).

**What is not yet known** is whether the repeat fires are *independently*
profitable or whether they only look good because the first fire of a losing
cluster is the one that gets counted. Splitting by position within cluster
(2nd, 3rd, 4th fire) would separate those, and is one query.

### 2026-08-29 — Cluster size separates trades by 300 bp, and it is not the exit

Following the cluster-head finding above, two further breakdowns. The first
**corrects** the reading in that entry.

**By position within the cluster** (train / validate, bp):

    1st   -5.5 / +6.4      3rd   -7.0 / -0.4
    2nd  -10.1 / +7.5      4th   -4.7 / +2.9      5th+  +2.6 / +11.6

The 2nd fire is the *worst* on train, not the best. So the "each successive
entry gets a better price, averaging down is working" reading in the previous
entry is wrong — positions 2-4 are no better than the first, and the whole
advantage sits in the 5th-or-later bucket.

**By cluster size**, which is what actually separates them:

| cluster size | train | validate |
|---|---|---|
| 1 (singleton) | **+170.9** | **+240.6** |
| 2-4 | **+164.8** | +212.3 |
| 5-9 | +38.0 | +50.0 |
| **10+** | **−119.3** | **−133.6** |

**Monotonic on both splits, and the spread is ~300 bp** — roughly *thirty
times* the 11 bp the entire target × stop grid spans.

**Read it the right way round.** Inside a long cluster the late fires look
good relative to the early ones, which is what produced the misreading above.
But the cluster as a whole is a disaster. A signal that fires ten or more
times is a stock in sustained decline being caught repeatedly on the way
down.

**Not a confound.** Side, era and drawdown bucket are flat across the four
buckets (53-62% short, 30-33% in the 2020s era, 72-76% in the 0-10 dd
bucket). The effect is cluster size itself.

**What this implies is bigger than the exit sweep.** Two nights of backtests
moved returns by 11 bp by changing when to sell. Cluster size separates the
same trades by 300 bp and is not a parameter at all — it is a property of the
signal, knowable at entry only in part (the *first* fire cannot know how long
its cluster will run, though the 2nd through 5th increasingly can).

**The 300 bp is partly hindsight, and that caps how usable it is.**
Cluster *size* is known only after the cluster ends. What a live decision has
is cluster *position*, and position alone does not reproduce the effect —
it runs the other way and is nearly flat:

| | by position (knowable at fire time) | by size (known only after) |
|---|---|---|
| first / singleton | −5.5 / +6.4 | **+170.9 / +240.6** |
| last / 10+ | +2.6 / +30.1 | **−119.3 / −133.6** |

At fire time you know you are the third fire; you do not know whether the
cluster stops at three (good) or runs to fifteen (bad), and those are
indistinguishable as they happen. So the separation is real as a description
of what happened and **is not directly tradeable**.

**This is a model feature, not a rule.** "Skip fires after the 9th" is
tempting, would be fitted to this sample, and the position table above shows
it would not even work — late fires are the *better* ones by position. What
belongs in the Phase 6 feature set is cluster position, running cluster
length, and time since the cluster's first fire: all knowable at decision
time, and the model can learn whatever relationship exists between them and
eventual cluster length rather than being handed a rule derived from
hindsight. `events.cluster_id` already exists, so these are window functions
rather than a rebuild.

### 2026-08-30 — Shortening the holding window is worse, on both splits

`max_hold_days` 5 → 3, everything else at the shipped config:

| | train | validate | stopped | hit target | timed out | mean hold |
|---|---|---|---|---|---|---|
| **hold 3** | −3.9 | +1.3 | 13.4% | 16.1% | **68.4%** | 2.67 |
| **hold 5 — shipped** | **−2.8** | **+7.5** | 21.4% | 23.5% | 51.5% | 3.95 |

**The first parameter change in three sweeps to fail cleanly on both splits.**

**Mechanism: a third of the winners never arrive.** Target hits fall from
23.5% to 16.1% while timeouts rise to 68.4%. Trades that would have reached
the target on day four or five are closed at whatever price is showing
instead.

**This corrects a worry from the second sweep.** The argument against the
stopless arm was partly that 72.6% of its exits were timeouts, making
`max_hold_days` the real policy. That framing treated a high timeout rate as
inherently suspect. It is not — hold 3 has 68.4% timeouts and is *worse*, so
the rate is a symptom of where the other exits sit, not a defect in itself.
The stopless arm's timeout rate is not, on its own, a reason to reject it.

`h10` decides whether 5 is a peak or whether the same pattern as the target
and the stop holds: every exit in this strategy cutting trades short.

### 2026-08-30 — The holding window: train peaks at 5, validate at 10, and the era split says why

`max_hold_days` 3 / 5 / 10, everything else at the shipped config:

| | train | validate | stopped | hit target | timed out | mean hold |
|---|---|---|---|---|---|---|
| hold 3 | −3.9 | +1.3 | 13.4% | 16.1% | 68.4% | 2.67 |
| **hold 5 — shipped** | **−2.8** | +7.5 | 21.4% | 23.5% | 51.5% | 3.95 |
| hold 10 | −3.0 | **+17.7** | 33.7% | **34.5%** | 23.9% | 5.90 |

**+17.7 bp is the largest number in seventeen arms**, and it is on the split
we do not select from. Train puts 5 and 10 within 0.2 bp — noise — so the
shipped config stands.

**By era, and this is the explanation rather than a shrug:**

| era | hold 3 | hold 5 | hold 10 |
|---|---|---|---|
| 2010-2014 | +5.7 | **+7.3** | +4.8 |
| 2015-2019 | +0.3 | +2.2 | **+2.6** |
| **2020-2023** | −9.1 | −6.6 | **−1.9** |

**The best window moves with the regime**: 5 days in the 2010s, 10 in the
2020s. Train is 71% pre-2020 so it picks 5; validate is entirely 2022-2023 so
it picks 10. Neither split is wrong and neither is noise — they are measuring
different market regimes, and this is the third time tonight the two have
disagreed in exactly this way.

**Longer holds help most where the strategy bleeds.** 2020-2023 improves from
−9.1 to −1.9, recovering 7.2 bp of the era that costs the most in every other
result tonight.

**What this changes.** The exit sweeps have been treating one number as
correct for sixteen years of data. The era tables say the right target, the
right stop width and now the right holding window all drift with the regime.
A single static exit policy is leaving money in every era except the one it
happens to fit — which is a stronger argument for the Phase 6 model than any
individual parameter result, because a model can condition on regime and a
constant cannot.

**Not shipped**: `max_hold_days` stays 5. Changing it on validate's
preference is the cherry-pick the split exists to prevent, and the honest
version of "10 is better now" is a regime-conditional policy, not a new
constant.

---

## 2026-08-31 — Storage reclamation: 12 sweep arms archived and deleted

The research database was 48 GB, `capitalscan-data` 55.6 GB. `events` and
`path` held 44 GB across 23 `config_hash` generations. See ADR 159 for the
decision and the rule.

**Done in one no-writer window** (Sunday, market closed, poller idle, no
nightly, `pg_stat_activity` clean):

| action | detail |
|---|---|
| dropped backup tables | `events_pre_adr145`, `universe_pre_adr145`, `universe_pre_adr_fix`, `run_ids_pre_adr145` |
| dropped unused indexes | `events_feed_latest`, `events_feed_watch`, `events_cluster` (`idx_scan = 0`; recreatable from their migrations) |
| archived 12 sweep arms | `reports/archive/sweep_arms_2026-08-31/` — `events` 16,546,848 rows, `path` 78,679,504, `cell_stats` 6,144, `rho_era` 48; 2.7 GB gzip, `gzip -t` clean, line counts match pre-delete `count(*)` |
| deleted 12 sweep arms | `events` (cascaded `path`), `cell_stats`, `rho_era`; `runs` rows kept |
| `VACUUM (FULL, ANALYZE)` | `events`, `path`, `indicators` — twice, once after the drops and once after the deletes |

**Result: database 48 GB -> 19 GB, volume 55.6 GB -> 24.2 GB.** `events`
7.9 GB, `path` 7.1 GB. Bloat recovered by the first VACUUM was ~7 GB on its
own.

**Deleted hashes** (all exit / holding-window sweep grid arms, 2026-08-28
to 08-30):

    753813ea1b7bb09f  a56d05a752a217ee  f2a56c9e8aa5c810  fcc6df7649798127
    6e7b11fc1c6ee599  0fdd15e962436b72  f7b31c5443d30948  0bdf21eba0ff2e34
    8dcdc265a0509005  ccfde27281981436  49fc87114751f32a  c74e355184fea7bb

The reference tables in the 2026-08-29 and 2026-08-30 entries above still
cite these. The numbers stand; the event populations behind them are now in
the archive, not in Postgres. Reload with `\copy ... FROM` in FK order to
audit one.

**11 generations kept resident**: live `0523841076f47293`; the ADR configs
of record (`1835688bf7d760ba`, `86e91448a65aa40b`, `697f3ae71428d392`,
`f66729c7eda212a4`, `bbc99a02ebdc999f`); prior serving `a38d3ca6b58295e8`;
`fda16796c6e82ee4` and `185bba9a239c18f4` (full Phase 4); and
`d750336f30551cab` + `f7d7bcd52ec48c22`, the two 2026-08-30 holding-window
arms, held until that sweep is closed out.

**Open item.** The archive is a single 2.7 GB copy on the workstation and
is in `.gitignore`. It needs its own backup, or the reproducibility
guarantee for those 12 arms is only as good as one disk. WAL was left at
`max_wal_size = 4GB` deliberately.
