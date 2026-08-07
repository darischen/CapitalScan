# Market-cap outlier diagnosis (universe.mcap_usd)

Read-only diagnosis. No files edited, nothing committed, no `cscan` or `pytest` run.

## 1. Affected rows

```sql
SELECT count(*) AS total_universe_rows, count(*) FILTER (WHERE mcap_usd > 5e12) AS bad_rows
FROM universe;
--  37863 total | 43 bad
```

43 `universe` rows across **24 distinct tickers**, scattered from 2010-06-30 through 2026-03-31. Not a
recurring single ticker and not clustered on one calendar date — each bad row traces back to a
different filing, for a different company, on a different day. Full list (`mcap_usd/1e9`, i.e. billions):

```
ORCL 2012-09-30  151,607,501.8      YUM  2016-12-31   23,242,459.0
AJG  2020-09-30       20,215,297.0  EIX  2020-03-31   19,865,214.4
CB   2010-09-30       19,732,513.9  AJG  2020-06-30   18,486,151.3
AEP  2011-06-30       18,153,883.2  EXC  2010-06-30   17,890,254.7
CB   2010-06-30       17,431,679.8  AEP  2010-09-30   17,370,003.5
AEP  2010-12-31       17,280,340.2  STX  2015-06-30   15,075,622.7
CLX  2013-03-31       11,594,070.8  AA   2019-06-30    4,343,367.4
WHR  2011-09-30        3,813,057.6  AA   2019-09-30    3,724,135.9
ON   2013-06-30        3,642,151.8  ON   2012-12-31    3,160,469.3
GT   2010-12-31        2,878,523.4  ON   2012-09-30    2,815,434.7
AMD  2012-09-30        2,384,460.7  WST  2010-09-30      570,843.2
QCOM 2011-06-30           94,812.7  QCOM 2011-12-31       91,949.8
CCL  2021-03-31           24,748.2  PKG  2026-03-31       18,932.9
PKG  2024-06-30           16,393.5  GRMN 2018-09-30       13,875.3
GRMN 2018-12-31           12,542.3  PKG  2023-06-30       11,885.4
GRMN 2016-09-30           10,010.6  GRMN 2016-12-31        9,604.8
HBAN 2015-06-30            9,144.5  CNP  2012-06-30        8,832.3
GRMN 2016-06-30            8,826.6  CNP  2011-12-31        8,557.0
AAP  2012-03-31            6,458.9  CNX  2011-09-30        6,411.2
HBAN 2011-03-31            5,732.6  MAA  2014-06-30        5,479.4
SWKS 2012-03-31            5,209.7  AAP  2011-12-31        5,044.2
AAP  2012-06-30            5,014.8
```

## 2. Root cause: bad values in `shares_outstanding.shares`, not a units mismatch in the multiply

For every bad `universe` row, `shares_outstanding` (source `sec_xbrl` in every case) has a single
filing whose `shares` value is the *correct* figure with 3 or 6 extra trailing zeros appended —
i.e. exactly ×1,000 or ×1,000,000 too large, bracketed on both sides by plausible values from
adjacent quarters:

```sql
-- ORCL: real Oracle share count is ~4.8B and declining through 2012-2013
 ORCL | 2012-06-26 |       4882506000   -- correct
 ORCL | 2012-09-24 | 4819056000000000   -- ×1e6 too large
 ORCL | 2012-12-21 |       4734297000   -- correct

-- AEP: three separate filings, all ×1e6
 AEP | 2010-07-30 | 479437027000000
 AEP | 2010-11-01 | 480276270000000
 AEP | 2011-05-03 | 481790955000000

-- QCOM: ×1e3 (a different scale factor from ORCL/AEP)
 QCOM | 2011-04-20 | 1669532005000
 QCOM | 2011-11-02 | 1680984426000

-- AAP, GRMN, HBAN, CNP, CNX, MAA, SWKS, PKG, CCL: all ×1e3
 AAP  | 2011-08-24 | 76637258000   (real ~76,637,258)
 GRMN | 2016-04-27 | 208077418000  (real ~208,077,418)
```

Confirmed directly against the cached raw SEC companyfacts parquet
(`data/cache/sec_facts/1341439.parquet`, ORCL's CIK):

```
cik      tag                                  filed_on    end         value             form  accn
1341439  EntityCommonStockSharesOutstanding   2012-09-24  2012-09-17  4819056000000000  10-Q  0001193125-12-401697
```

The bad value is already wrong in the cached raw JSON fetched from `data.sec.gov`'s
`companyfacts` endpoint — `capitalscan/jobs/fetch/sec.py::_shares_rows` takes `entry["val"]`
verbatim, with no unit/scale handling of any kind (XBRL "shares" facts have no separate scale
attribute in this feed; the API returns one plain number). This is not our fetch code
mis-parsing a scale attribute. It is the filer's own XBRL instance document for that one
accession reporting the cover-page shares-outstanding fact scaled by 1,000 or 1,000,000
(a real, occasionally-seen SEC XBRL tagging defect — one bad accession, not a systemic feed
problem, which is exactly why `p99_b` stays sane while `max_b` spikes). `run_shares`
(`capitalscan/jobs/ingest.py:774`) then does `shares = int(row["value"])` with no plausibility
check and writes it straight into `shares_outstanding`.

**`close` is not the bug.** Checked ORCL bars around 2012-09-24: `close` ≈ $30-31, exactly in
line with real Oracle prices that quarter, split-adjusted as expected. Multiplying a sane price
by a corrupted share count produces the corrupted market cap; there is no split-adjustment /
raw-price units mismatch here (that was the *previously fixed* TSM ADR bug in ADR 035 — a
different mechanism, and not what's happening in this batch: TSM does not appear in this list at
all, and `adr_adjusted_shares` is confirmed not implicated — none of these 24 tickers are ADRs in
`UniverseParams.adr_ordinary_per_adr`, which today only contains TSM).

**One cause, many instances.** Every single bad row decomposes the same way: one XBRL filing's
raw `shares` value off by an exact power-of-ten scale factor (1e3 or 1e6, varies by filing),
ingested without a sanity check, multiplied by an otherwise-correct split-adjusted close. This is
not several unrelated defects — it is one ingestion-side gap (no bounds/plausibility check on
`shares_outstanding.shares`) manifesting once per bad filing.

## 3. Which factor is wrong

`shares`, not `close`, and not the ADR-ratio step. `core.universe.adr_adjusted_shares` is ruled
out: it only touches tickers in `up.adr_ordinary_per_adr` (currently TSM only), and TSM has zero
rows above the $5T threshold in this scan. `close` was spot-checked for ORCL and is sane. The
defect is entirely upstream, in the raw `shares_outstanding.shares` value for 43 specific
(ticker, filed_on) filings.

## 4. Data problem vs. code problem

Both, in a specific sense:
- **Data problem**: the SEC XBRL source data for those 43 filings is genuinely wrong (scaled by
  1e3 or 1e6 in the filer's own submission, reproduced verbatim in `data.sec.gov`'s companyfacts
  API and in our cached parquet).
- **Code gap** (not a logic bug, an absent guard): nothing between `fetch_company_facts` and the
  `universe.mcap_usd` write ever checks that a share count is in a plausible range for the
  company. `_shares_rows` (fetch/sec.py) and `run_shares` (ingest.py:774-823) pass the value
  through untouched; `run_universe` (compute.py:567-570) multiplies it by price with no bounds
  check either. A single corrupted filing therefore reaches production `mcap_usd` with nothing
  in the pipeline positioned to catch it.

## 5. Blast radius

- **`universe.mcap_usd`**: 43 rows corrupted, confirmed above.
- **`universe.crit_mcap`**: corrupted on every one of those 43 rows — `crit_mcap` reads `mcap_usd
  > min_mcap_usd (200e9)`, so it evaluates `True` for names nowhere near the real $200B threshold.
  Confirmed directly:
  ```sql
  SELECT ticker, as_of, mcap_usd/1e9 mcap_b, crit_mcap, in_trade FROM universe
  WHERE ticker IN ('WST','GRMN','CNX','MAA','HBAN','AAP','SWKS','CCL','PKG','CNP')
    AND mcap_usd > 5e12 ORDER BY ticker, as_of;
  ```
  gives `crit_mcap = t` on all 20 rows shown (WST, GRMN, HBAN, CNX, MAA, SWKS, CCL, AAP, CNP,
  PKG — none of which are anywhere near $200B). Several of those quarters also show `in_trade =
  t` (AAP 2012-03-31, CNP 2012-06-30, GRMN 2018-09-30, GRMN 2018-12-31, PKG 2024-06-30, PKG
  2026-03-31), meaning the bug did tip real ticker-quarters into the trading universe that
  should not have qualified on market cap.
- **`events`**: the `events` job (`capitalscan/jobs/compute.py`, around line 859,
  `core_universe.in_trade(universe_flags, ticker, bar_date)`) filters which bars generate events
  using `universe.in_trade`. Since this bug only ever inflates `mcap_usd` (never deflates it), it
  can only cause **false inclusion** into the trade universe (never false exclusion) for the
  affected ticker-quarters above, which can let events fire for names/periods that should have
  been filtered out.
- **`events.mcap_usd`**: currently **not affected**, because it is not populated. Confirmed:
  ```sql
  SELECT count(*) total, count(mcap_usd) have_mcap FROM events;
  --  1292395 | 0
  ```
  All 1.29M `events` rows have `mcap_usd IS NULL` — no job currently writes it (searched every
  `.py` under `capitalscan/jobs` and `capitalscan/research` for `mcap_usd`; only `compute.py`'s
  `run_universe` touches the column, and that writes to `universe`, not `events`). `docs/DESIGN.md`
  shows `events.mcap_usd` in the schema as a "context tag" column and in `v_ticker_state`'s
  `universe` LATERAL join pattern, so whoever wires that write path next (noted in
  `reports/HANDOFF.md` as still open) will inherit this same corruption unless it's fixed first,
  or filtered/bounded at write time.
- **`mcap_rank`**: not populated anywhere (`count(mcap_rank) = 0` across all of `universe`), so
  not currently affected — it's dead/future-work per the comment in `core/universe.py:57`.
- No other `mcap` usage found anywhere in `capitalscan/core`, `capitalscan/research`, or
  `capitalscan/api` (grepped for `mcap` across the whole tree).

## 6. Recommended fix (not implemented)

Two complementary layers, since the defect is "bad row reaches production with no guard,"
not one bad formula:

1. **Ingestion-side plausibility check in `run_shares`** (`capitalscan/jobs/ingest.py`, around
   line 813-823): compare each new filing's `shares` against the ticker's own recent history
   (e.g. reject/flag a value more than ~5-10x the nearest neighboring filed value for that
   ticker) before writing to `shares_outstanding`. Cheap, catches this exact 1e3/1e6-scale defect
   pattern, and generalizes to any other single-filing outlier. Tradeoff: needs a threshold
   (magic-number-in-`core/config.py` territory per invariant 9 if the bound lives in `core`;
   simplest is to keep the check in `jobs/` where it already has DB access, using a
   `UniverseParams`-style config value rather than a bare literal), and a real 5-10x share count
   jump (a buyback, secondary offering, or split-adjustment edge case) would need to be an
   explicit allowed case rather than silently dropped — likely wants a `bar_rejects`-style reject
   log per invariant 4 rather than a silent skip, so the gap is visible instead of just missing
   data.
2. **Defense in depth in `run_universe`** (`compute.py:567-570`): after computing `mcap`, sanity
   check the result against `ind_row["close"]` times some sane share-count ceiling, or against
   the ticker's own trailing `mcap_usd` history in `universe`, and null it out (with a reject
   log) rather than writing an absurd number forward into `crit_mcap`/`in_trade`. This catches
   the defect even if a bad share count somehow gets into `shares_outstanding` through a
   different path (e.g. the Yahoo fallback source).
3. **One-time backfill**: the 43 known-bad `shares_outstanding` rows should be corrected (divide
   by the inferred scale factor, 1e3 or 1e6, established per-row the same way this diagnosis did
   — compare to the adjacent filings for that ticker) or deleted so `_latest_shares` falls back
   to the next real filing. This is a data fix, separate from the code guard above; either order
   works, but the guard should land first so a backfill/reprocessing run doesn't just recreate
   the same bad numbers if SEC's cache is ever refetched.

Do **not** try to fix this by changing which price series is used (`close` vs `adj_close`) or by
touching `adr_adjusted_shares` — both are confirmed correct and uninvolved for this batch of
outliers.

## 7. Checked and ruled out

- **ADR ratio bug (`adr_adjusted_shares`)**: not implicated. Only TSM is in the ratio map; TSM
  has no rows above the $5T scan threshold.
- **Split-adjustment / price-series units mismatch** (the mechanism from the *previous* ADR 035
  TSM fix): checked ORCL's `bars.close` around the bad filing date — sane, split-adjusted,
  consistent with real Oracle prices. Not the cause here.
- **A shared bad ingestion run / one bad date**: the 43 rows span 2010 through 2026 and 24
  unrelated tickers on unrelated filing dates — not one batch job gone wrong on one day.
- **Symbol-reuse/impostor splicing** (per `reports/AUTONOMOUS_RUN_FINDINGS.md`): not this
  mechanism — the bad number lives entirely inside `shares_outstanding.shares` for one filing of
  the correct company, not a spliced price series. (Did not run the price-range-ratio impostor
  test since the bars data was already confirmed sane for the sampled ticker; worth running
  broadly as a separate check but it's addressing a different failure mode than this one.)
- **`fetch/sec.py` scale/decimals parsing bug**: ruled out — the SEC `companyfacts` "shares" unit
  array has no separate scale/decimals field to mis-parse; `_shares_rows` reads `entry["val"]`
  directly, and the cached raw parquet already contains the inflated value, meaning the error is
  upstream of our fetcher, in the filer's own filed XBRL fact for that one accession.
- **`mcap_rank` and `events.mcap_usd`**: confirmed both are entirely unpopulated (`count = 0`)
  across the whole database, so they carry no current corruption — only latent risk once someone
  wires either write path.
