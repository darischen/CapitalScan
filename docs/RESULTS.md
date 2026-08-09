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
