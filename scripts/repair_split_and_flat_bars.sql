-- Repair for the 2026-08-27 split/filler-bar findings.
--
-- Three separate corruptions, one script, run in this order because each
-- later step reads what the earlier one wrote.
--
--   1. Fabricated bars: zero volume AND a close equal to the prior close.
--      29,242 rows over 152 tickers. Not observations -- no trades, and a
--      price copied from yesterday. `flat_zero_volume_bar` rejects these on
--      ingest from now on; this removes the ones already stored.
--
--   2. IESC: a 2-for-1 split on 2026-08-24 the vendor never back-adjusted.
--      A re-fetch does NOT fix this -- Yahoo still serves the unadjusted
--      series -- so the ratio is applied here.
--
--   3. AVB is deliberately NOT handled here. Yahoo *has* adjusted it, so
--      the correct repair is a re-fetch, which has to bust the parquet
--      cache and therefore cannot be done in SQL.
--
-- Every delete is logged to `bar_rejects` first: invariant 4 says a dropped
-- row is recorded with a reason, and this is append-only audit (ADR: the
-- table is a trail, not a worklist).

\set ON_ERROR_STOP on
BEGIN;

-- ---------------------------------------------------------------- step 1
CREATE TEMP TABLE _filler ON COMMIT DROP AS
WITH f AS (
  SELECT ticker, ts, close, volume,
         lag(close) OVER (PARTITION BY ticker ORDER BY ts) AS prev_close
    FROM bars WHERE interval = '1d')
SELECT ticker, ts, close, prev_close, volume
  FROM f
 WHERE (volume = 0 OR volume IS NULL)
   AND close = prev_close;

SELECT count(*) AS filler_bars_to_delete,
       count(DISTINCT ticker) AS tickers FROM _filler;

INSERT INTO bar_rejects (ticker, ts, rule, severity, payload, created_at)
SELECT ticker, ts, 'flat_zero_volume_bar', 'reject',
       jsonb_build_object('close', close, 'prior_close', prev_close,
                          'volume', volume, 'purged_on', '2026-08-27',
                          'note', 'stored before the rule existed; removed by '
                                  'scripts/repair_split_and_flat_bars.sql'),
       now()
  FROM _filler;

DELETE FROM bars b USING _filler f
 WHERE b.ticker = f.ticker AND b.ts = f.ts AND b.interval = '1d';

-- ---------------------------------------------------------------- step 2
-- Strictly BEFORE the ex-date: the ex-date bar already trades on the new
-- share count. Volume is MULTIPLIED -- a 2-for-1 doubles the share count,
-- so pre-split volume in old shares is twice as many new ones.
UPDATE bars
   SET open      = round(open      / 2.0, 4),
       high      = round(high      / 2.0, 4),
       low       = round(low       / 2.0, 4),
       close     = round(close     / 2.0, 4),
       adj_close = round(adj_close / 2.0, 4),
       volume    = round(volume    * 2.0)
 WHERE ticker = 'IESC' AND interval = '1d' AND ts < DATE '2026-08-24';

-- Proof the adjustment landed: the jump across the ex-date must now be ~1,
-- not ~2. Fails loudly inside the transaction if it did not.
DO $$
DECLARE before_c numeric; after_c numeric; jump numeric;
BEGIN
  SELECT close INTO before_c FROM bars
   WHERE ticker='IESC' AND interval='1d' AND ts < DATE '2026-08-24'
   ORDER BY ts DESC LIMIT 1;
  SELECT close INTO after_c FROM bars
   WHERE ticker='IESC' AND interval='1d' AND ts >= DATE '2026-08-24'
   ORDER BY ts ASC LIMIT 1;
  jump := before_c / nullif(after_c, 0);
  RAISE NOTICE 'IESC across ex-date: % -> %  jump %', before_c, after_c, round(jump,4);
  IF jump IS NULL OR jump < 0.92 OR jump > 1.08 THEN
    RAISE EXCEPTION 'IESC still discontinuous after adjustment (jump %)', jump;
  END IF;
END $$;

COMMIT;
