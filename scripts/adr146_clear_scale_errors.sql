-- ADR 146 -- remove the 33 stored x1,000 share-scale filings.
--
-- RUN THIS ONLY AT THE START OF A UNIVERSE REBUILD, never on its own.
--
-- Deleting these rows changes `shares_outstanding` without changing the
-- `universe` and `events` already computed from it, which is a worse state
-- than either end: the market caps on file stop matching the inputs that
-- produced them, and nothing downstream reports a mismatch. The correct
-- sequence is below, and every step of it is required.
--
-- `jobs.ingest` already rejects this class at the source (ADR 146), so this
-- is a one-off cleanup of rows admitted before that guard existed.
-- `run_shares` never re-offers a rejected accession, so they will not
-- return.
--
--   1. this script
--   2. universe rebuild                          ~15 min
--        There is NO --all flag. `cscan universe` takes --quarter and
--        evaluates one quarter per call, so loop 2010Q1..<latest>:
--          for y in $(seq 2010 2026); do for q in 1 2 3 4; do
--            uv run cscan universe --quarter "${y}Q${q}"; done; done
--   3. cscan backtest --phase compute --chunk-size <fresh>  ~50 min
--        NB: vary --chunk-size from every previous run. `_chunk_already_done`
--        keys on (config_hash, chunk, of), so reusing a size skips every
--        chunk and the run reports success having done nothing.
--        Burned for f66729c7eda212a4 so far: 25, 20, 15, 22
--        (partitions of=38, 47, 62, 43). Check before choosing:
--          SELECT DISTINCT params->>'of' FROM runs
--           WHERE job='backtest_compute' AND status='ok'
--             AND params->>'config_hash' = :chash;
--   4. cscan backtest --phase finalize            ~5 min
--   5. the stale-event sweep -- MANDATORY: `events` upserts and never
--      deletes, so quarters that lose `in_trade` leave rows behind.
--
--      **Do NOT use the date-only predicate the session 20 notes give**
--      (`run_id < 'backtest_compute_<today>'`). It silently matches
--      nothing whenever an earlier compute ran the *same day*, because
--      'backtest_compute_20260822T093630_...' sorts GREATER than
--      'backtest_compute_20260822'. Hit for real on 2026-08-22: the
--      sweep printed DELETE 0, the verification query reused the same
--      predicate and printed 0 stale, and 644 events across AAP, ALK and
--      ENSG survived into the harness.
--
--      Compare against the first run_id of the current compute instead --
--      every chunk of that run sorts at or above it:
--
--        DELETE FROM events
--         WHERE config_hash = :chash
--           AND run_id LIKE 'backtest_compute_%'
--           AND run_id < :first_run_id_of_this_compute;
--
--      The LIKE guard is load-bearing: without it the delete also takes
--      the `events_*` rows that `cscan events` writes, which are a
--      different population (ADR 122, unpriced, out-of-trade included).
--
--      Verify against the catalog with a DIFFERENT predicate than the one
--      you deleted with, or the check is circular.
--   6. cscan backtest --phase harness             ~48 min
--   7. cscan path backfill                        ~15 min
--   8. stats rho, cells x2, benchmarks x2
--
-- MEASURED 2026-08-22, running exactly this sequence:
--   33 filings deleted, as predicted.
--   universe rows above $5T: 5 -> 0. Max mcap now $4.84T (AAPL, real).
--   in_trade 6,299 -> 6,295.
--   Every corrupt value fell by exactly 1000x: AAP 2011-12-31
--   $5,044B -> $5.3B, SWKS 2012-03-31 $5,210B -> $5.2B, MAA 2014-06-30
--   $5,479B -> $5.5B, WWD 2020-09-30 $5,001B -> $5.0B, ALK 2011
--   $2,453B -> $2.5B.
--   Stale sweep removed 644 events / 2,728 path rows across AAP, ALK
--   and ENSG -- exactly the tickers whose corrected quarters lost
--   membership.

BEGIN;

-- Backup first. Same convention as `universe_pre_adr145`.
CREATE TABLE IF NOT EXISTS shares_scale_errors_pre_adr146 AS
SELECT * FROM shares_outstanding WHERE false;

WITH ranked AS (
    SELECT ticker, filed_on, shares,
           row_number() OVER (PARTITION BY ticker ORDER BY filed_on) AS rn,
           count(*)     OVER (PARTITION BY ticker)                   AS n
      FROM shares_outstanding
),
-- Median of the four nearest filings per side, self excluded. Mirrors
-- `core.universe.scale_error_indices`; keep the two in step if the
-- window, the ratio or the tolerance ever move.
windowed AS (
    SELECT a.ticker, a.filed_on, a.shares, a.n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY b.shares)::numeric AS local_med
      FROM ranked a
      JOIN ranked b
        ON b.ticker = a.ticker
       AND b.rn BETWEEN a.rn - 4 AND a.rn + 4
       AND b.rn <> a.rn
     GROUP BY a.ticker, a.filed_on, a.shares, a.n
),
flagged AS (
    SELECT ticker, filed_on
      FROM windowed
     WHERE n >= 8                                  -- excludes PSKY
       AND local_med > 0
       AND shares / local_med > 50                 -- anomalous...
       AND (shares / 1000) / local_med BETWEEN 0.2 AND 5.0   -- ...and x1000 explains it
)
INSERT INTO shares_scale_errors_pre_adr146
SELECT s.* FROM shares_outstanding s JOIN flagged f USING (ticker, filed_on);

DELETE FROM shares_outstanding s
 USING shares_scale_errors_pre_adr146 b
 WHERE s.ticker = b.ticker AND s.filed_on = b.filed_on;

-- Expect 33. Anything else means the detector and this query have drifted
-- apart, or ingest has admitted new rows of this class -- stop and look.
SELECT count(*) AS backed_up_and_deleted FROM shares_scale_errors_pre_adr146;

COMMIT;
