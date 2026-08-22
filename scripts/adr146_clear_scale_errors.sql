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
--   2. cscan universe --all                       ~11 min
--   3. cscan backtest --phase compute --chunk-size 20   ~40 min
--        NB: vary --chunk-size from the previous run. `_chunk_already_done`
--        keys on (config_hash, chunk, of), so reusing a size skips every
--        chunk and the run reports success having done nothing.
--   4. cscan backtest --phase finalize            ~5 min
--   5. the stale-event sweep -- session 20 notes, MANDATORY: `events`
--      upserts and never deletes, so quarters that lose `in_trade` leave
--      rows behind that make two runs each claim a cluster head, which
--      `_check_non_overlap` then fails on.
--   6. cscan backtest --phase harness             ~48 min
--   7. cscan path backfill                        ~15 min
--   8. stats rho, cells x2, benchmarks x2
--
-- Expected membership delta: small. 6 `in_trade` quarters passed
-- `crit_mcap` on a x1,000 number and should now fail it; a further ~14
-- universe rows between $5T and $6T (SWKS, AAP, MAA, WWD, ALK, REG, FTNT)
-- sit under the `McapPlausibility` ceiling and resolve to real values.

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
