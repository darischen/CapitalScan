# entry_sanity reference-frame fix

Commit: `b1d78b5` (branch `session-9-backtest`, parent HEAD `561bac6`).

## The fix

`entry_sanity` (`capitalscan/research/harness.py`) now validates each fill
against the bar it was actually priced from, instead of always the daily bar:

- `TOUCH` / `NEXT_OPEN` — unchanged, validated against the daily bar via
  `entry_date`.
- `TOUCH_5M` / `TOUCH_30M` — validated against the **hourly** bar
  `core.returns.entry_price_for` -> `_first_hourly_touch` actually selected.

`run_harness(events, bars_by_ticker, config, hourly_by_ticker=None)` gained one
new, optional, keyword-only-by-convention parameter: `hourly_by_ticker`, a
second per-ticker dict of raw hourly-bar frames (same shape `jobs.cli`'s
`_load_bars_by_ticker` reads off `bars`, just `interval='1h'`, un-merged with
indicators). It is threaded only into `_check_entry_sanity`; the other four
checks' signatures are untouched.

**Why a new parameter instead of extending `bars_by_ticker`:** `bars_by_ticker`
is one row per `(ticker, date)` — the grain `no_lookahead`, `exit_sanity`, and
`non_overlap` all depend on (date-indexed lookups, the shift ladder). Hourly
bars are several rows per date; merging them in would corrupt that grain for
every check except `entry_sanity`, the only one that needs hourly data at all.
A second dict keeps the existing four checks' inputs exactly as they were.

`jobs/cli.py` gained `_load_hourly_by_ticker(engine, tickers, config)`,
mirroring `_load_bars_by_ticker`'s `config.splits.ingest_start` lower bound and
per-ticker-omit-if-empty convention, reading `bars WHERE interval='1h'`. The
one call site (`backtest()`) now loads it and passes it through:

```python
bars_by_ticker = _load_bars_by_ticker(engine, bt_report.tickers, config)
hourly_by_ticker = _load_hourly_by_ticker(engine, bt_report.tickers, config)
events_for_harness = _load_events_for_run(engine, bt_report.run_id)
harness_report = run_harness(
    events_for_harness, bars_by_ticker, config, hourly_by_ticker=hourly_by_ticker
)
```

## Identifying which hourly bar a fill came from

`events` already carries `touch_level` and `side` (confirmed via
`information_schema.columns` on the live `events` table) — the two inputs
`_first_hourly_touch` needs besides the hourly frame itself. New helper
`_hourly_bar_for_entry` in `harness.py`:

1. Scopes `hourly_by_ticker[ticker]` to the entry's own calendar day
   (`entry_date`, same field `TOUCH`/`NEXT_OPEN` already used — for
   `TOUCH_5M`/`TOUCH_30M`, `resolve_entries` sets `entry_date = signal_date`),
   sorted by `ts` — the exact scoping `research.enrich.resolve_entries` applies
   before calling `entry_price_for` (`enrich.py:144-147`).
2. Calls `capitalscan.core.returns._first_hourly_touch(day_hourly,
   touch_level, side)` directly — **imported, not reimplemented** (invariant
   2). `harness.py`'s import line now reads
   `from capitalscan.core.returns import _first_hourly_touch, realized_return`.

Nothing in `_first_hourly_touch`'s selection logic (first hourly bar whose
`low`/`high` breaches `touch_level`, per `side`) is duplicated anywhere in
`harness.py`.

## TDD evidence

**RED** — added `TestEntrySanityHourlyReferenceFrame` to
`capitalscan/tests/unit/test_backtest_harness.py` (5 tests, built from the
exact PGR trace in the task brief) before touching `harness.py`:

```
PS> uv run pytest capitalscan/tests/unit/test_backtest_harness.py -k "HourlyReferenceFrame" -v
...
FAILED ...test_touch_5m_entry_is_validated_against_its_hourly_bar_not_the_daily_bar
  TypeError: run_harness() got an unexpected keyword argument 'hourly_by_ticker'
FAILED ...test_touch_5m_entry_price_genuinely_outside_its_hourly_bar_is_still_caught
  TypeError: run_harness() got an unexpected keyword argument 'hourly_by_ticker'
FAILED ...test_touch_30m_entry_uses_the_hourly_bars_close_directly
  TypeError: run_harness() got an unexpected keyword argument 'hourly_by_ticker'
FAILED ...test_touch_5m_entry_price_without_hourly_bars_supplied_is_a_violation_not_a_silent_skip
  TypeError: run_harness() got an unexpected keyword argument 'hourly_by_ticker'
FAILED ...test_touch_entry_kind_is_unaffected_and_still_uses_the_daily_bar
  TypeError: run_harness() got an unexpected keyword argument 'hourly_by_ticker'
5 failed, 27 deselected in 0.64s
```

Confirmed the failure was "signature doesn't exist yet," not a fixture
mistake, before writing the implementation.

**GREEN** — after implementing `_hourly_bar_for_entry`, the branch in
`_check_entry_sanity`, and the `run_harness`/`_load_hourly_by_ticker` wiring:

```
PS> uv run pytest capitalscan/tests/unit/test_backtest_harness.py -v
...
32 passed in 1.99s
```

Then the full safe suite (per CONSTRAINTS.md, never bare `pytest`):

```
PS> uv run pytest capitalscan/tests/unit capitalscan/tests/property
746 passed in 29.94s
```

(First pass surfaced 3 unrelated failures in `test_backtest_cli.py` — three
fake `run_harness`/`_load_bars_by_ticker` stand-ins that didn't yet accept the
new `hourly_by_ticker` kwarg. Fixed by updating those fakes and adding
`_load_hourly_by_ticker` to the file's autouse `_no_real_io` fixture; not a
defect in the harness fix itself.)

## Test proving a genuinely out-of-range hourly fill is still caught

`test_touch_5m_entry_price_genuinely_outside_its_hourly_bar_is_still_caught`:
plants `entry_price=400.0` against the PGR hourly bar `[273.16, 285.00]`
(daily bar `[274.39, 279.93]` — 400 is outside both, but the assertion is
specifically that the check flags it using the **hourly** frame):

```python
report = run_harness(
    self._pgr_event(entry_price=400.0), self._pgr_daily(), CONFIG,
    hourly_by_ticker=self._pgr_hourly(),
)
assert not report.entry_sanity.passed
assert report.entry_sanity.violations[0]["reason"] == "entry_price_outside_bar_range"
assert report.entry_sanity.violations[0]["frame"] == "hourly"
```

Also added: a row with a priced `TOUCH_5M`/`TOUCH_30M` entry but
`hourly_by_ticker=None` is a violation (`no_hourly_bar_for_entry`), not a
silent skip — so a run where hourly data simply isn't wired up can't pass
`entry_sanity` by accident.

## Other four checks unaffected

- `exit_sanity`, `return_identity`, `non_overlap` — signatures and bodies
  untouched (verified in the diff: only `_check_entry_sanity` and
  `run_harness` changed inside `harness.py`, besides the new
  `_hourly_bar_for_entry` helper and the `_HOURLY_ENTRY_KINDS` constant).
- `no_lookahead` — still reads only `bars_by_ticker`, never
  `hourly_by_ticker`.
- Full unit/property suite (746 tests, including the pre-existing
  `TestExitSanity`, `TestReturnIdentity`, `TestNonOverlap`, `TestNoLookahead`
  classes) passes unmodified except for the two `test_backtest_cli.py` fakes
  noted above, which needed the new kwarg accepted, not any behavior change.
- `TOUCH`/`NEXT_OPEN` rows still read the daily bar even when
  `hourly_by_ticker` is supplied — proven by
  `test_touch_entry_kind_is_unaffected_and_still_uses_the_daily_bar`.

## Hourly-vs-daily inconsistency quantification

Query (run via the read-only `psql` path per CONSTRAINTS.md):

```sql
SET max_parallel_workers_per_gather=0;
WITH hourly_agg AS (
  SELECT ticker, ts::date AS day, MIN(low) AS h_low, MAX(high) AS h_high
  FROM bars WHERE interval='1h' GROUP BY ticker, ts::date
),
daily AS (
  SELECT ticker, ts::date AS day, low AS d_low, high AS d_high
  FROM bars WHERE interval='1d'
),
joined AS (
  SELECT h.ticker, h.day, h_low, h_high, d_low, d_high,
    GREATEST(d_low - h_low, 0) AS low_breach,
    GREATEST(h_high - d_high, 0) AS high_breach
  FROM hourly_agg h JOIN daily d USING (ticker, day)
)
SELECT COUNT(*) AS pairs_checked,
       COUNT(*) FILTER (WHERE low_breach > 0.0001 OR high_breach > 0.0001) AS breaking_pairs
FROM joined;
```

Result: **297,790 `(ticker, day)` pairs checked, 50,239 breaking (16.9%)** —
the day's aggregated hourly range `[MIN(low), MAX(high)]` falls outside the
daily bar's own `[low, high]`.

Splitting by breach magnitude (breach amount / daily high) surfaces two
distinct populations:

| Class | Pairs | Tickers | Magnitude |
|---|---|---|---|
| Small, tick-level | 45,815 | 604 | median 4.7e-5 (~$0.005 on a typical price), p95 4.6% |
| Large, scale-level | 4,424 | 17 | breach > 50% of the daily high; worst cases ~25x |

The large-scale group is not tick noise. Worst examples are all BKNG:
hourly `high` ≈ 5839, daily `high` ≈ 233 on the same `(ticker, day)` —
almost exactly a 25x ratio, consistent with the two feeds disagreeing about a
split-adjustment factor for that ticker, not a bad print. 17 tickers carry
this pattern across thousands of days each.

The small-tick group (604 tickers, 45,815 pairs, median breach ~$0.005) looks
like ordinary cross-feed rounding/tick noise — the PGR case in the task brief
sits in this population (breach of ~$0.23 on a ~$280 stock, well under the
p95).

**This is quantified, not fixed, per task scope** — DESIGN §2.3 owns
validation rules and `bar_rejects` is where an ingest-side check for this
would log, but wiring that in is out of scope here.

## Concerns

1. **The large-scale (17-ticker, ~25x) group is not "hourly feed noise" — it
   looks like a split-adjustment disagreement between the daily and hourly
   ingest paths for those specific tickers.** If any `TOUCH_5M`/`TOUCH_30M`
   fills exist for those tickers on the affected days, this fix will validate
   them against an hourly bar that is itself wrong by ~25x, and — because the
   check only compares the fill to the bar it was priced from, not the bar to
   any external truth — it will pass. The fix corrects the check's reference
   frame; it does not (and per scope, should not) validate that the hourly
   feed itself is trustworthy for those 17 tickers. Recommend the controller
   treat those 17 tickers as a priority follow-up for a `bar_rejects` rule
   (DESIGN §2.3), separate from this task.
2. `_hourly_bar_for_entry` re-derives `day_hourly` per row (one groupby-free
   scan of `hourly_by_ticker[ticker]` filtered by date) rather than
   pre-indexing hourly bars by `(ticker, day)` the way `_indexed_bars` does
   for daily bars. On the current dataset (7 violating rows in the failing
   run) this is immaterial; if a future full-universe run has orders of
   magnitude more `TOUCH_5M`/`TOUCH_30M` rows, this is the first place to
   optimize.
