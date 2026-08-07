# Scan dedupe, ticker-list reconciliation, and split-commit — report

HEAD at start: `b1d78b5`.

## Task 1 — committed the hourly split-adjustment fix

Verified before committing:

- `git diff` on all four touched files, read in full. `capitalscan/jobs/cli.py`'s
  uncommitted change is a `--confluence-only` filter option on the `scan` CLI
  command — unrelated to the split fix, left uncommitted as instructed.
- `uv run pytest capitalscan/tests/unit capitalscan/tests/property` — **752
  passed**.
- `config_hash(Config())` — **`3e598c59e7d71eae`**, unchanged from the value
  set as the Postgres GUC. `HourlySplitGuard` is a standalone frozen
  dataclass outside `Config` (same shape as `SweepParams`/`SharesPlausibility`),
  so `dataclasses.asdict(Config())` never sees it.
- Read the diff against the report's claims: `_split_adjustment_factor`
  divides pre-ex-date OHLC by the cumulative product of `ratio` over every
  split with `ex_date > ts.date()`; nulls/non-positive ratios are dropped,
  not guessed (invariant 4); `_flag_range_escape` rejects whole days whose
  hourly aggregate range escapes the daily range by more than
  `HourlySplitGuard.range_escape_tolerance` (0.50); wired into
  `run_bars_hourly` after `validate_bars`, guard-rejected rows dropped from
  what's upserted. Matches the report.

Commit `fa65d66`: `capitalscan/core/config.py`, `capitalscan/jobs/ingest.py`,
`capitalscan/tests/unit/test_bars_hourly_checkpoint.py`,
`capitalscan/tests/unit/test_bars_hourly_split_adjust.py`. `cli.py` and
`docker-compose.yml` left untouched.

## Task 2 — reconciled ticker list

**Reconciled list (17 tickers, back-adjustable now):**

```
AMCR,ANET,BKNG,BNY,CRWD,CVNA,DD,ETR,FAST,IBKR,KLAC,NFLX,NOW,ORLY,PANW,TPL,TSCO
```

This is the controller's 18 **minus SBNY**.

### Criterion

Day-aggregate hourly/daily range escape at a **50% relative-deviation
threshold**, applied per `(ticker, day)`:

```sql
h_high > daily.high * 1.5  OR  h_low < daily.low * 0.667
```

plus a **sustained-pattern check**: the escape must recur across enough
days to look like an unadjusted split (which corrupts every bar before the
ex-date), not a single stale/illiquid bar.

Derived directly from the live database (queried read-only, not inferred):

- Across all 18 controller-flagged tickers, the day-aggregate range ratio
  clusters into two populations separated by two orders of magnitude:
  ordinary noise stays under ~10%, and confirmed splits deviate 80%–2400%
  (AMCR's reverse split at 80% is the smallest; CRWD 300%, KLAC 900%, BKNG
  2400%).
- The 8 tickers unique to the fix agent's 19 (BDX, CMCSA, FDX, FTV, J, LEN,
  SPGI, WDC) were checked directly: **none of them cross 1.5x/0.667 at
  all** — their measured max deviation ranges from 1.0% (J) to 32.7% (FTV),
  matching small stock-split/rights-adjustment ratios on file in
  `corporate_actions` (1.017–1.327), not real N-for-1 splits. These sit
  squarely inside the noise band the task's own guidance calls out, and are
  correctly excluded.
- **50% sits with wide margin** above the largest observed non-split
  anomaly (33%, FTV/WDC) and well below the smallest real split (80%,
  AMCR) — the same threshold already implemented as
  `HourlySplitGuard.range_escape_tolerance = 0.50` in Task 1's commit, so
  the reconciliation and the shipped guard now agree.
- Verifying the controller's exact SQL (`max(high) > daily.high*1.5 OR
  min(low) < daily.low*0.667`, joined on the day-aggregate) reproduces
  **exactly the controller's 18 names**, no more, no fewer.

### Why SBNY is excluded from the repair list

SBNY passes the 50% threshold test (its one matching day has `h_low=0.355`
vs `daily.low=0.65`, a 45% escape past `0.667`) but fails the sustained-
pattern check: it has exactly **one** (ticker, day) pair on file, backed by
only 4 hourly bars, at sub-$1 prices — consistent with Signature Bank's
2023 receivership and a stale/illiquid post-delisting quote, not a split.
`corporate_actions` has **zero** rows for SBNY (any action type, any date).
A real unadjusted split shows up as a sustained divergence across every
pre-ex-date day (BNY: 378 days; the smallest of the 17, TSCO: 84 days);
SBNY's single isolated day doesn't fit that shape. Back-adjustment has
nothing to correct here regardless — there is no split ratio on file to
divide by — so this is a separate, likely-immaterial data-quality question
about a delisted ticker, not part of this defect.

### Why the two lists differed

The controller's threshold (1.5x high / 0.667x low, i.e. 50% relative
deviation) and the shipped `HourlySplitGuard` tolerance (0.50) agree with
each other and reproduce the 18-name list exactly. The fix agent's 19 used
a looser method — its own report describes "matching the escape ratio to
the split ratio within 5% over 20+ days" — which picked up 8 tickers whose
*only* qualifying "split" rows are small (1.0–1.3x) stock-split/rights
adjustments that never actually produce a range escape past 50%, while
apparently dropping 6 tickers (ANET, DD, FAST, NFLX, SBNY, TSCO) that do
show clear escapes matching a real split ratio on file. I could not
reproduce the fix agent's drops from the data — ANET, DD, FAST, NFLX, and
TSCO all have a single matching split with `ex_date` inside the observed
hourly window (2024-08-06 through 2026-07-31) and an observed range ratio
matching that split's ratio almost exactly (ANET 4.00x vs. ratio=4; DD
0.333x vs. ratio=0.333; FAST 2.00x vs. ratio=2; NFLX ~10x vs. ratio=10;
TSCO 5.00x vs. ratio=5) — there is no compounding or window-boundary
ambiguity that would explain excluding them. SBNY's inclusion in the
controller's 18 but exclusion from the fix agent's 19 is explained above
(SBNY fails a 20+-day sustained-pattern requirement, which is a real and
useful distinction the controller's raw-threshold method missed).

### BNY — verified: no split row, cannot be repaired by backfill

Confirmed directly: `SELECT ... FROM corporate_actions WHERE ticker='BNY'`
returns 6 rows, most recent `ex_date = 2007-07-02, ratio = 0.9434` — nothing
within the hourly window, and nothing near the ~0.08–0.18x magnitude BNY's
378-day mismatch actually shows. `_split_adjustment_factor` has nothing to
divide by, so `cscan bars --hourly --backfill` will not fix BNY; it needs
`cscan actions --tickers BNY` first to (attempt to) ingest the missing
split, then a hourly backfill after that succeeds. **No other ticker in the
17-name reconciled list has this problem** — every other name has a split
row on file whose `ex_date` falls inside the hourly window and whose ratio
matches the measured deviation.

### Repair command

```
cscan bars --hourly --backfill --tickers AMCR,ANET,BKNG,CRWD,CVNA,DD,ETR,FAST,IBKR,KLAC,NFLX,NOW,ORLY,PANW,TPL,TSCO
```

16 tickers — the reconciled 17 minus BNY, which this command cannot repair
(see above). Not run: a `cscan backtest` may still be live against this
database per the prior report's caution; queue for after it finishes.

**What this command will NOT fix:**

- **BNY** — no split row in `corporate_actions` in any window; back-adjustment
  divides by 1.0 for every bar, so nothing changes. Needs
  `cscan actions --tickers BNY` first, and even then only if a split row is
  actually recoverable from the source feed.
- **SBNY** — not in the repair list at all. No split row on file, and the
  single-day/4-bar pattern doesn't look like an unadjusted split in the
  first place; running the backfill would re-fetch the same unadjusted
  data and change nothing. If this ticker matters, it needs separate
  investigation as a stale-quote/illiquid-delisted-ticker data-quality
  question, not a split-adjustment one.

## Task 3 — `scan()` deduplication

**Fix** (`capitalscan/jobs/compute.py`, `scan()`): resolve
`current_setting('capitalscan.default_config_hash', true)` via a plain
`SELECT` before building the events query, then bind it as
`e.config_hash = :config_hash` and add `e.entry_kind = :entry_kind` bound to
`EntryKind.TOUCH.value`.

- **GUC unset → empty frame**, not "return everything" (the current bug)
  and not an error. `v_events` already lands on the same outcome in this
  state (its `WHERE e.config_hash = current_setting(..., true)` clause
  becomes `= NULL`, which matches zero rows) — this keeps `scan()`
  consistent with the one other place in the schema that reads this GUC,
  and it is safer than guessing which config to show or exposing every
  sweep generation mixed together.
- **entry_kind filtered to `'touch'`**, not collapsed via `SELECT DISTINCT`.
  Every entry_kind row for one event carries identical signal-level
  columns today — that's *why* they were exact duplicates — but `DISTINCT`
  silently stops deduplicating the moment a future column (e.g. an
  entry-price display field) varies by entry_kind, reintroducing this
  exact defect. Filtering to one kind stays correct regardless of what
  columns get added later. `'touch'` is the row `run_events` itself
  writes at detection time (`_build_event_row`, before any backtest-added
  entry_kind row exists), and it's the same kind `jobs/poll.py` already
  treats as canonical for existing-event lookups.
- No column change: `_SCAN_COLUMNS` and the projected SQL columns are
  untouched.

**TDD evidence:** `capitalscan/tests/unit/test_scan_dedupe.py`, new file.
A fake `pd.read_sql` simulates Postgres applying `scan()`'s bound
parameters against a synthetic superset (one event x 3 `config_hash`
values x 4 `entry_kind` values = 12 candidate rows). Against the
pre-fix `scan()` (no `config_hash`/`entry_kind` params bound), the fake
returns all 12 rows and the test fails (`assert 12 == 1`) — confirmed RED
before implementing. After the fix, all 4 new tests pass: one row for the
pinned config regardless of which config is pinned, empty-not-everything
when the GUC is unset (and `pd.read_sql` is asserted never called in that
case), and the documented column list is unchanged.
`test_scan_indicator_lag.py`'s fake engine gained a minimal `.execute()`
stub to satisfy the new GUC-resolution query; its three pre-existing
assertions on the events query text are unchanged and still pass.

`uv run pytest capitalscan/tests/unit capitalscan/tests/property`: **756
passed** (752 + 4 new).

## Concerns

1. **Task 2 numbers came from a live, currently-unadjusted database.**
   Once the Task 1 fix's repair command runs and hourly bars for the 16
   reparable tickers are back-adjusted, the day-aggregate range-escape
   query used to derive this list will no longer flag them — expected and
   correct, but worth noting so nobody re-runs the reconciliation query
   post-repair and is confused when the list comes back empty.
2. **SBNY is unresolved, not fixed.** Excluding it from the repair command
   is correct (there's nothing to back-adjust), but its hourly data is
   still whatever it is — a separate small investigation if anyone cares
   about a delisted ticker's stale quotes.
3. **`scan()`'s new GUC-resolution query adds one extra round trip** to
   every call (a `SELECT current_setting(...)` before the main query,
   rather than folding it into the main query's `WHERE` clause). Chosen
   deliberately so "unset" is an explicit Python branch instead of a
   silent zero-row SQL clause, and to keep the fix unit-testable without a
   live database — the extra round trip is one cheap, non-parallel,
   session-local `SELECT`, not worth avoiding at the cost of testability.
4. I did not run the repair command (`cscan bars --hourly --backfill ...`)
   — it writes to the database, which the safety rules for this session
   prohibit, and the prior report flagged a possibly-live `cscan backtest`
   run as a reason to queue it regardless.
