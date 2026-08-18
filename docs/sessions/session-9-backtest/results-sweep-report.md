# Session 9 close-out: sweep + ADR 059 hand-inspection recorded in RESULTS.md

Docs-only change. `docs/RESULTS.md` updated (no other files, no DB writes). HEAD at start: `d33aaa5`.

## What was added

1. **Phase 1 addendum, "Data quality updates — 2026-08-03"** (inserted after the Backfill
   record, before Indicator verification): BNY hourly prune, the double-adjustment fix,
   measured residual hourly gaps, and the `git_sha='unknown'` run inventory.
2. **Phase 3, "ADR 059 hand-inspection"**: gate condition recorded as met, dated
   2026-08-03, attributed to the user, with a caveat that this record does not itself
   constitute independent verification.
3. **Phase 3, "Exit config sweep"**: full sweep measurement — config/row counts,
   default-config identity, wall-clock time, config-axis breakdown, priced/exited/
   ambiguous per config, exit_reason distribution, mean net_ret per config.
4. **Phase 3, "Phase 3 gate — sweep closes the last outstanding item"**: short note
   tying the sweep completion to BUILD §9.10, without editing the existing gate table.

Nothing in the existing file was edited; all additions are new subsections, consistent
with the append-only rule.

## Queries behind every figure

All run via `PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost
-U capscan -d capitalscan -c "SET max_parallel_workers_per_gather=0; <query>"`.

**Config and row counts**
```sql
SELECT count(DISTINCT config_hash) AS n_configs, count(*) AS total_rows FROM events;
-- 18 | 4430088

SELECT config_hash, count(*) AS rows FROM events GROUP BY config_hash ORDER BY rows DESC;
-- all 18 rows = 246116, no variance
```

**Default config membership**
```sql
SELECT count(DISTINCT config_hash) FROM events WHERE config_hash = '3e598c59e7d71eae';
-- 1
```

**Default config run_id no longer present**
```sql
SELECT run_id, count(*) FROM events WHERE config_hash='3e598c59e7d71eae' GROUP BY run_id;
-- only backtest_sweep_20260803T021428_10b5860b, 246116 rows

SELECT count(*) FROM events WHERE run_id='backtest_20260802T183304_6b1c5b52';
-- 0
```
Finding not in the brief: the original ADR 059 default run's rows are gone from
`events` — superseded by the sweep's own pass over `config_hash='3e598c59e7d71eae'`.
The "Default config run" entry in RESULTS.md still cites the old run_id; that entry
was not edited (append-only rule), but the new sweep entry notes the supersession so a
later reader querying by that run_id isn't confused by zero results.

**Wall-clock time**
```sql
SELECT run_id, job, git_sha, status, started_at, finished_at, rows_written
FROM runs WHERE run_id IN (SELECT DISTINCT run_id FROM events) ORDER BY started_at;
-- 18 rows, job='backtest_sweep', all status='ok', git_sha=aacee77d...798028f2
-- 2026-08-03 00:58:22 .. 06:58:24

SELECT min(started_at), max(finished_at), max(finished_at)-min(started_at) AS wall_clock,
       count(*), avg(finished_at-started_at)
FROM runs WHERE run_id IN (SELECT DISTINCT run_id FROM events);
-- wall_clock = 06:00:01.49, n_runs=18, avg_per_config=00:20:00.05
```
This is far from DESIGN §5.9's "~4 minutes total" sweep estimate. Flagged as a
discrepancy, not corrected in DESIGN.md (out of scope — docs-only, RESULTS.md only).

**Config-axis decomposition (via `runs.params` jsonb)**
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
-- 18 | 18, clean 1:1
```
Confirmed: `atr` × {1.0,1.5,2.0,2.5} × {0.03,0.04,0.05} = 12; `fixed` and `none` each
hold `stop_atr_k=1.5` and vary only `target_pct` = 3+3. Total 18, matching DESIGN §5.9.

**Priced / exited / ambiguous per config**
```sql
SELECT config_hash,
       count(*) FILTER (WHERE entry_price IS NOT NULL) AS priced,
       count(*) FILTER (WHERE exit_date IS NOT NULL) AS exited,
       count(*) FILTER (WHERE ambiguous) AS ambiguous
FROM events GROUP BY config_hash ORDER BY config_hash;
```
`priced`=110954 and `exited`=110894 uniform across all 18 configs. `ambiguous` ranges
0 (the three `none` configs) to 318 (`fixed`, k=1.5, target=0.03); default config shows
28, matching 15+0+13 from the split-level table already in RESULTS.md.

**Exit reason distribution and mean net_ret** — joined `cfg` CTE above to `events`,
grouped by (stop_mode, stop_atr_k, target_pct[, exit_reason]). Full 18-row mean-net_ret
table and 87-row exit_reason table are in RESULTS.md's "Exit config sweep" section
verbatim; not reproduced twice here.

**`git_sha='unknown'` inventory**
```sql
SELECT count(*) FROM runs WHERE git_sha = 'unknown';
-- 27

SELECT run_id, job, status FROM runs WHERE git_sha = 'unknown';
-- lists ingest/indicator/universe/backtest runs from 2026-08-01 through
-- backtest_20260802T183304_6b1c5b52 (the original default run)
```

**BNY prune**
```sql
SELECT count(*) FROM bars_bny_hourly_bad_20260803;                        -- 2888
SELECT interval, count(*) FROM bars WHERE ticker='BNY' GROUP BY interval; -- 1d: 5233 (no 1h row)
SELECT max(high)/min(low) AS daily_range_ratio FROM bars WHERE ticker='BNY' AND interval='1d';
-- 10.6068652849740933
```

**Residual hourly gaps**
```sql
WITH active_hourly AS (
  SELECT ticker, min(ts::date) AS lo, max(ts::date) AS hi
  FROM bars WHERE interval='1h' GROUP BY ticker
  HAVING max(ts::date) > '2026-07-01'
),
daily_in_range AS (
  SELECT b.ticker, d.ts::date AS d
  FROM bars d JOIN active_hourly b ON b.ticker=d.ticker
  WHERE d.interval='1d' AND d.ts::date BETWEEN b.lo AND b.hi
),
hourly_days AS (SELECT DISTINCT ticker, ts::date AS d FROM bars WHERE interval='1h')
SELECT dr.ticker, count(*) AS missing_days
FROM daily_in_range dr LEFT JOIN hourly_days h USING (ticker, d)
WHERE h.d IS NULL GROUP BY dr.ticker ORDER BY missing_days DESC;
-- INFO: 32, NFX: 7. DD: not present (0 missing days).
```

**Downstream hourly-priced impact**
```sql
SELECT ticker, count(*) AS n_hourly_events,
       count(*) FILTER (WHERE entry_price IS NOT NULL) AS n_hourly_priced
FROM events
WHERE ticker IN ('BNY','DD','ANET','IBKR') AND entry_kind IN ('touch_5m','touch_30m')
GROUP BY ticker ORDER BY ticker;
-- BNY 1188/0, DD 2412/0, ANET 3708/2196, IBKR 4320/2124
```

## Corrections after coordinator review (2026-08-03, second pass)

The coordinator independently re-ran three figures and caught two real errors (the third,
uniform row counts / wall clock / run supersession, was confirmed correct). Both errors
are now fixed in `docs/RESULTS.md`. What went wrong in each case:

**1. Hourly-event row counts were exactly half the true value.**

Original (wrong) query:
```sql
SELECT ticker, count(*) AS n_hourly_events,
       count(*) FILTER (WHERE entry_price IS NOT NULL) AS n_hourly_priced
FROM events
WHERE ticker IN ('BNY','DD','ANET','IBKR') AND entry_kind IN ('touch_5m','touch_30m')
GROUP BY ticker ORDER BY ticker;
-- BNY 1188/0, DD 2412/0, ANET 3708/2196, IBKR 4320/2124
```
Corrected query (coordinator's, reproduced and confirmed):
```sql
SELECT ticker, count(*) AS all_configs,
       count(*) FILTER (WHERE entry_kind IN ('touch_5m','touch_30m')
                        AND entry_price IS NOT NULL) AS hourly_priced
FROM events WHERE ticker IN ('BNY','DD','ANET','IBKR') GROUP BY 1;
-- BNY 2376/0, DD 4824/0, ANET 7416/2196, IBKR 8640/2124
```
The `WHERE ... entry_kind IN (...)` clause on the original query filtered the row-count
column, not just the priced column. Each ticker has four entry kinds (`next_open`,
`touch`, `touch_5m`, `touch_30m`) at equal per-ticker counts, so restricting to the two
hourly kinds happened to land on exactly half the true total-events-per-ticker figure —
a halving error that looked plausible enough not to trigger suspicion on its own. The
priced column was correct in both passes (it was always meant to filter to the hourly
kinds); only the denominator was wrong. Fixed in RESULTS.md with the corrected table and
an explicit note of the error and its cause.

**2. "Residual hourly gaps" used the wrong metric entirely.**

Original (wrong) approach: counted trading days present in `bars` daily but absent from
`bars` hourly, restricted to tickers whose hourly range extends into 2026-07. Returned
only INFO (32) and NFX (7), with DD at zero — the opposite of the real finding.

Corrected approach (coordinator's, reproduced and confirmed, then run without a head
limit to get the full list):
```sql
WITH h AS (SELECT ticker, ts::date d, max(high) hhi FROM bars
           WHERE interval='1h' GROUP BY 1,2)
SELECT h.ticker, count(*) bad FROM h
JOIN bars b ON b.ticker=h.ticker AND b.ts::date=h.d AND b.interval='1d'
WHERE h.hhi/b.high > 1.5 OR h.hhi/b.high < 0.667
GROUP BY 1 ORDER BY 2 DESC;
-- DD 22, PANW 7, SBNY 2, then 14 more tickers at 1 each (CVNA, ANET, CRWD, TSCO,
-- NOW, BKNG, FAST, AMCR, ETR, TPL, NFLX, KLAC, ORLY, IBKR) = 17 tickers, 45 bad days
```
The defect this session's fix addressed is corrupted *values* on days that are present
in both tables, not missing days — a day-gap query can never find it, because the
corrupted rows aren't gaps, they're wrong numbers sitting where correct numbers should
be. INFO and NFX do not appear under the correct query and were never traced to a real
cause; per the coordinator's instruction, they are dropped from RESULTS.md rather than
left unexplained in the permanent record, and the day-gap query itself is documented as
a withdrawn false start so a future reader doesn't repeat it. The 17-ticker list matches
the group already named (partially) under "Known open data-quality items" in the
Backfill record, confirming this is the same known defect, unresolved by the refetch —
not a new one.

## What in the brief did not survive verification — revised after coordinator review

The first pass of this report wrongly concluded the brief's gap and event-count figures
were wrong. They were not — **my first-pass queries were wrong**, in the two ways
detailed in "Corrections after coordinator review" above. Restating accurately:

- **"DD 22 days" and "exactly 1 day each on 14 tickers"**: the brief was **correct**.
  The value-mismatch query (`hourly max(high) / daily high` outside `[0.667, 1.5]`)
  gives DD exactly 22 bad days and 14 other tickers at exactly 1 each (CVNA, ANET,
  CRWD, TSCO, NOW, BKNG, FAST, AMCR, ETR, TPL, NFLX, KLAC, ORLY, IBKR). My first-pass
  day-*gap* query was measuring a different thing than the defect described (missing
  days vs. corrupted values on present days) and its INFO/NFX results are dropped as
  unexplained, per the coordinator's instruction. The brief's list was itself
  incomplete — PANW (7) and SBNY (2) are real and are now included in RESULTS.md.
- **"0 hourly-priced entries... across 2,376 and 4,824 events" for BNY/DD**: the brief
  was **correct** on every number. My first-pass query filtered the row-count column to
  `entry_kind IN ('touch_5m','touch_30m')` when it should have counted all entry kinds
  for the ticker, producing exactly half the true total (1,188/2,412 instead of
  2,376/4,824). Fixed in RESULTS.md.
- **"1 day of 498" for ANET/IBKR**: still not independently reproduced or traced to a
  query. Not restated as a number in RESULTS.md; the qualitative claim (ANET/IBKR are
  the only two affected tickers with nonzero priced hourly entries) is confirmed.
- Everything else in the brief (18 configs, 4,430,088 rows, uniform per-config counts,
  default config membership, BNY prune counts, daily range ratio, ANET/NFLX split
  multiples, 27 `git_sha='unknown'` runs) verified exactly as stated, and was
  re-verified again in this second pass with no new discrepancies.

**Lesson for next time:** two of three real errors here came from being too clever with
a `WHERE`/`FILTER` clause — narrowing a row-count column beyond what the reported label
actually meant, and picking the wrong proxy metric for a described defect instead of
querying for the defect's actual symptom (wrong values, not absent rows). Both produced
numbers that were internally consistent and plausible-looking, which is exactly why they
weren't caught without an independent second query.

## Not investigated further (out of scope for this docs pass)

- Why the sweep took 6 hours against DESIGN §5.9's ~4-minute estimate (per-config time
  matches the single-threaded validation-harness cost, suggesting the harness runs once
  per sweep cell rather than once over the union — plausible but not traced into code,
  since this task is docs-only).
- Why the original default run's `events` rows were replaced by the sweep's rewrite of
  the same `config_hash` rather than coexisting under distinct run_ids (a schema/write-
  path question, not investigated — code was not read as part of this docs-only task).
