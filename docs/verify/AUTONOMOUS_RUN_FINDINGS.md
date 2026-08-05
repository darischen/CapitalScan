# Autonomous run findings — 2026-08-01

Branch `session-9-backtest`. Executed from `reports/SESSION_9_STANDING_ORDERS.md`
after the hourly backfill completed.

## Completed

| Step | Result |
|---|---|
| hourly backfill | **ok** — 2,083,045 rows, 614 tickers, 2024-08-06 → 2026-07-31, 296 min |
| `earnings --historical` | 69,514 rows, 607 tickers (after fixing a blocking bug) |
| `shares` | 33,568 rows, 572 tickers (after fixing a blocking bug) |
| `universe --quarter 2026Q3` | 625 tickers evaluated — **but see FINDING 3** |
| `indicators --workers 8 --lookback 6500` | running at time of writing |

Daily bars: 2,653,245 rows, 633 tickers, 2000-01-03 → 2026-07-31.

The hourly checkpoint fix held for the full 296-minute run. 614 of 633 tickers
returned data; the ~19 that did not stopped trading before the 725-day window
opened on 2024-08-06, which is expected.

## FINDING 1 — two jobs had never worked at scale (FIXED, commit `dca2ee2`)

`run_earnings` and `run_shares` both upserted rows whose own proposed values
collided on the target primary key. Postgres rejects that outright:

```
psycopg.errors.CardinalityViolation: ON CONFLICT DO UPDATE command
cannot affect row a second time
```

Both failed after several minutes and wrote **zero** rows. Neither had ever been
executed against the real universe, so neither defect was reachable before now.

- `earnings` is keyed `(ticker, report_date)`; a company files several 8-Ks in one
  day and ADR 036 treats every 8-K date as an earnings date.
- `shares_outstanding` is keyed `(ticker, filed_on)`; one filing reports the share
  count for several periods.

Fixed with unit tests that stub the engine and fetchers, so they run without a
database. 417 tests pass, ruff and mypy clean.

## FINDING 2 — SEC 8-K history is truncated, defeating ADR 036

```
 tickers | reaches_2010 | short_of_2010 |  earliest
---------+--------------+---------------+------------
     607 |           14 |           590 | 2003-04-22
```

Only **14 of 607** tickers have 8-K history reaching 2010. Most begin 2014-2016.

Root cause: `sec.fetch_submissions` reads only `raw["filings"]["recent"]`, which
SEC caps near the most recent 1,000 filings. Older filings live in
`filings.files[]` shards the code never fetches. A large-cap issuer files enough
forms to blow past 1,000 well short of 2010.

ADR 036's stated purpose is reaching past 2009 where Finnhub's free tier cannot.
As written it does not. **Consequence:** `days_to_earnings` will be null for
events before roughly 2014 for most tickers, so the earnings-contamination
window DESIGN relies on is unenforced across the first third of the study period.

Not fixed — standing orders route this to the user as an ADR 036 correctness
question. The fix is paging `filings.files[]` in `fetch_submissions`; the cache
is keyed per CIK so a re-run after fixing is cheap.

### 2b. The downstream damage is worse than nulls (HIGHEST PRIORITY)

After the full indicator recompute, `days_to_earnings` is 98% populated — which
looked like good news and is not. `_merge_days_to_earnings` takes the *nearest
future* `report_date`, so a 2010 bar whose ticker has no 8-K before 2014 gets a
non-null distance to that 2014 report:

```
 a) <=95d (plausible)  | 1408456 | 58.7%
 b) 96-200d            |   48061 |  2.0%
 c) 201-400d           |   77262 |  3.2%
 d) >400d (IMPOSSIBLE) |  820951 | 34.2%
 null                  |   46678 |  1.9%
```

A public company reports quarterly, so `days_to_earnings` can never exceed ~95.
**34.2% of post-2010 rows carry a definitively impossible value**, and a further
5.2% in buckets b and c are suspect.

This is worse than null. Null is honest and testable. A large plausible-looking
integer tells the contamination filter "no earnings nearby" when a report was in
fact days away, so DESIGN's rule — any 5-day window containing an earnings report
is contaminated regardless of session — is silently inverted across roughly a
third of the study period. Backtest results over 2010-2014 cannot be trusted
until this is fixed.

Suggested guard once pagination is fixed: reject or flag any
`days_to_earnings > 95` at write time rather than storing it, so a coverage gap
surfaces as a rejected row instead of a plausible number.

## FINDING 3 — `universe.in_trade` is 0 for all 625 tickers

```
 rows | in_train | in_trade
------+----------+----------
  625 |      625 |        0

 mcap | sma200 | slope | rel_ret | rev_growth | rev_null
------+--------+-------+---------+------------+----------
   44 |    433 |   373 |       0 |          0 |      625
```

Three separate problems, in ascending order of severity:

**3a. `crit_rev_growth` is a permanent `None` stub** (`compute.py:299-311`).
`core.universe.is_tradeable` requires all five ADR 014 criteria to be `is True`,
and `None` correctly fails rather than passing. So `in_trade` can never be true
for any ticker under the default `required` set, regardless of the other four.

**3b. `crit_rel_return` passes for 0 of 625**, and `benchmarks` is empty (0 rows).
No job populates it. Worth confirming whether `rel_return_756d` has any source at
all right now.

**3c. `as_of` is `2026-09-30`, a future date.** This is the subtle one.
`_in_trade` (`compute.py:522`) matches rows with `as_of <= signal_date` and
returns `True` when none match — a documented v1 fallback so `run_events` works
before `run_universe` has ever run. Every signal date is ≤ 2026-07-31, so no
universe row matches and the filter is currently **inert**.

That means events generated today are not wrongly excluded. It also means the
filter will silently begin excluding **everything** once the calendar passes
2026-09-30, with no error and no log line. A quarter-end `as_of` combined with a
fail-open default is a trap.

Events were generated with the filter inert, so they are usable. They will need
regenerating once 3a and 3b are resolved and `universe` is re-run with a
sensible `as_of`.

## FINDING 4 — the ADR 035 ADRs were never ingested (Phase 1 gate cannot pass)

```
SELECT ... FROM tickers WHERE ticker IN ('TSM','ASML','SAP','NVO')  ->  0 rows
grep -c 'TSM|ASML|SAP|NVO' data/universe_union.csv                  ->  0
```

ADR 035: "Non-US exposure comes through US-listed ADRs only (TSM, ASML, SAP, NVO
and comparable), never foreign primary listings."

The universe is built from Wikipedia's S&P 500 constituent table, which contains
no foreign ADRs, and nothing else adds them. So an entire mandated slice of the
universe has never existed in this database.

TESTS.md §10's Phase 1 gate is `cscan scan --ticker TSM --start 2026-07-01
--end 2026-07-30`. It returns "no events found" — not because `scan()` is broken,
but because TSM has no bars, no indicators, and no events. The gate references a
ticker the pipeline cannot produce.

**Phase 1 gate: FAIL.** Three of its five criteria cannot pass as written:
- the TSM scan (no such ticker)
- "zero nulls in indicators after 2010-01-01" (`days_to_earnings`, FINDING 2b)
- the Stooq agreement check (FINDING 5)

## FINDING 5 — the Stooq cross-check is dead

`cscan validate --report` printed `stooq cross-check skipped for <T>: <T>` for
every ticker sampled. The exception message is the bare ticker, so
`stooq.fetch_daily` is raising for all of them and the `except Exception` at
`ingest.py:788` swallows it per-ticker by design.

Consequence: `clean` is computed as `n_hard_rejects == 0 and not disagreements`,
and `disagreements` is *always* empty because the check never runs. Combined with
the missing `trading_days` gap check, "validation clean" currently means only
"zero hard rejects" — one of three intended checks.

## FINDING 6 — validation is no longer clean, and it caught real contamination

```
close_outside_range          reject   1027
open_outside_range           reject    398
unexplained_split_like_move  reject    262
high_lt_low                  reject      6
```

1,425+ hard rejects, all from `bars_daily`. This is a change: validate was clean
before the recovered union tickers were ingested.

The rejects are doing their job. Samples:

```
CBE  2013-10-23  low=0.045 high=0.045 close=0.035
MEE  2011-10-31  low=8.968 high=8.968 close=9.219
```

**This corrects an earlier claim of mine.** During the universe-gap triage I
recovered 60 tickers and asserted the `YHD`-exchange group was "the clean, safe
win." That was wrong. Price ranges expose several as spliced or wrong-company:

```
 ticker |  n   |  lo  |   hi   |   first    |    last
--------+------+------+--------+------------+------------
 CBE    | 4506 | 0.01 | 305.00 | 2000-01-03 | 2018-01-30
 COL    | 2055 | 0.02 |   1.80 | 2012-08-02 | 2020-11-30
 GR     | 4191 | 0.03 |  84.80 | 2001-01-02 | 2018-02-22
 RSH    | 1178 | 0.08 |  58.79 | 2009-11-27 | 2017-10-05
```

Cooper Industries (CBE) was bought by Eaton in Nov 2012 at ~$72; a series
spanning $0.01 to $305 through 2018 is two companies concatenated. Rockwell
Collins (COL) traded $50-140 and was acquired Nov 2018; a max of $1.80 through
2020 is not that company. Goodrich (GR) was acquired by UTC in 2012 at $127.50.

My triage tested that history *predates* the removal date and that the Yahoo name
matched the Wikipedia name. Neither test catches a series that *continues past*
the acquisition, which is the actual signature of symbol reuse. **The missing
check is an end-date test: a delisted member's series must stop at its removal,
not run years beyond it.**

Recommend re-auditing all 60 recovered tickers on that basis before Session 9
consumes them, and treating `bar_rejects` price anomalies as an identity signal
rather than only a data-quality one.

## FINDING 7 — 65,767 events predate the ADR 035 window

`min(signal_date)` is 2008-10-15; ADR 035 pins the event start at 2010-01-01.
Caused by my `--lookback 6500` choice, not a code defect — the job honored the
parameter. Those rows sit outside the study window and should be deleted or
filtered before the backtest reads them.

## FINDING 8 — event rate is ~5x the analytical estimate

BUILD.md §9's Phase 3 gate wants event count within 20% of ~4% of ticker-days for
confluence. Actual:

```
 confluence_high  282311
 confluence_low   178883   -> 461,194 of 2,400,803 post-2010 ticker-days = 19.2%
```

Roughly 4.8x the estimate, far outside the ±20% band. All six signal types
together touch 57.3% of ticker-days. Not diagnosed — this is what Session 9's
validation harness (task 9.8) exists to investigate, and it may be a parameter
question rather than a defect. Flagging it because it is the Phase 3 criterion
most likely to fail.

## FINDING 8 RESOLVED — the ~4% estimate was wrong, not the engine

Re-measured on clean data after every purge and fix. The engine fires
confluence on **18.34%** of ticker-days against BUILD.md's "~4% of
ticker-days for confluence", a 4.1x discrepancy that survived every data
repair. That made it look like a detection defect. It is not.

Measured directly from `bars` joined to `indicators` at **t−1** (the pairing
`detect` actually uses), 2010-01-01 onward, 2,371,529 ticker-days:

```
                              rate
confluence_low,  intraday extremes    7.16%
confluence_low,  close                4.43%   <- matches the ~4% estimate
either side,     intraday extremes   18.34%   <- what the engine measures
either side,     close               11.38%
```

Component rates, same window:

```
P(low  <= bb_lower)   11.18%      P(close <= bb_lower)   5.29%
P(k_full <= 20)       15.92%
```

Two definitional differences, both deliberate design choices, fully account
for the gap:

1. **ADR 005 uses the bar's intraday low/high, not the close** — "a daily
   bar's extremes are the intraday touch". A textbook Bollinger figure of
   ~2.5% per tail is a *close*-based number. Using extremes roughly doubles
   the touch rate: 5.29% → 11.18%. Effect on confluence: 4.43% → 7.16%.
2. **The criterion says "for confluence" without specifying one side or
   both.** Counting `confluence_high` as well: 7.16% → 18.34%.

Combined: 4.43% × 1.6 × 2.6 ≈ 18.3%. The engine also reconciles with itself
— 18.34% computed from bars matches the 18.24% measured independently from
the `events` table.

**Action: restate the acceptance criterion, do not change the engine.** The
criterion needs to name which side(s) it counts and whether it is measured on
closes or extremes. As written it is ambiguous, and the one reading that
makes it pass (`confluence_low`, on closes, 4.43%) is the one the engine
deliberately does not implement.

This does not tell you whether 18% is a *useful* event rate — that is a
Phase 4 question about statistical power, not a Phase 3 correctness one.

## Still open from the session 0-8 verification

1. `run_validate` never queries `trading_days` — the DESIGN §2.3 missing-bar check
   is documented at `ingest.py:341` but not implemented. "Clean" does not mean
   "no gaps".
2. `scan()` joins indicators on `e.signal_date = i.ts` (`compute.py:734`), reading
   bands at t while the event fired off t−1. Display only.
3. `.github/workflows/ci.yml` sets `working-directory: capitalscan` for every step
   but `pyproject.toml` is at the repo root, so CI has never run.
4. `db_io.upsert` overwrites every non-key column — a design constraint for any
   Session 9 job that writes to `events`.
5. ADR 073's exit-signal push is not implemented.
6. `docs/RESULTS.md` still describes the abandoned 51-ticker dry run.
7. `FB`, `PCLN`, `PCS`, `Q` are symbol-reuse impostors still active in `tickers`.
   The 568 originally-loaded tickers were never identity-audited.
