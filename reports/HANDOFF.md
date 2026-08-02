# Handoff — 2026-08-01

Branch `session-9-backtest`, forked from `main` at `9777377`.

Read this plus `docs/superpowers/plans/2026-08-01-session-9-backtest.md` and you
have everything. The rest of the detail is in the commit messages.

## Start here

Session 9 is planned and ready to execute:
`docs/superpowers/plans/2026-08-01-session-9-backtest.md`, twelve tasks, for
`superpowers:subagent-driven-development`. The user pre-approved it on the
condition that it matches BUILD.md §9 and DESIGN §5.2/§5.4/§5.10. Departing
from those docs needs their sign-off first.

Task 1 is the BUILD.md 9.0 prerequisite (wire hourly into the nightly chain).
Tasks 2-12 are the engine. Every task's tests stub IO, so all of them can be
implemented without touching the database.

## Hard safety rules

- **Never run bare `pytest`.** `pyproject.toml` sets
  `testpaths = ["capitalscan/tests"]`, so it collects the integration suite,
  which runs `TRUNCATE TABLE bars CASCADE` against 4.5M rows.
  Use `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.
- Nothing under `capitalscan/tests/integration/` while real data matters.
  `test_ingest.py` and `test_compute.py` truncate `bars`; `test_poll.py`
  truncates `tickers`, which CASCADEs to `bars`.
- No `cscan db migrate` or `uv sync` while a job is running.
- `docker` is not on PATH in agent shells. Use
  `PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan`.
  Prefix `SET max_parallel_workers_per_gather=0;` if a query hits a
  shared-memory error.
- Put these rules verbatim in every subagent prompt. Four agents ran safely
  today because of it.

## State

```
validation clean
465 tests · ruff clean · format clean · mypy clean (93 files)

bars        4,571,361   615 active tickers   daily 2009-01-02, hourly 2024-08-06
indicators  2,501,817
events      1,292,276   from 2010-01-04, config_hash edf5658f5da3807a
universe          621   as_of 2026-06-30, 60 pass crit_mcap, 39 in_trade
earnings      176,398   483/607 tickers reach 2010
shares         73,000+  sec_xbrl + yahoo_shares_full
```

## THE ONE OPEN CORRECTNESS ISSUE — ADR market caps are wrong

Found at the very end of the session, not yet fixed, needs your decision.

SEC 20-F filings report **ordinary** shares; the bar price is per **ADR**.
Multiplying them directly overstates market cap by the ADR ratio:

```
        our DB (SEC)      yfinance (ADR-equiv)   ratio
TSM     25,932,524,521     5,186,474,013         5.00
ASML       385,417,665       384,100,000         1.00
SAP      1,228,504,232     1,154,204,232         1.06
NVO      4,421,895,520     3,347,023,520         1.32
```

TSM computes to **$10.5T against an actual ~$2.1T**. NVO is 1.32x, consistent
with Novo's A+B share classes where only the B shares underlie the ADR.

Impact is bounded but real: TSM clears the $200B threshold either way, so
`in_trade` is accidentally correct, but the stored `mcap_usd` and `mcap_rank`
are wrong for these four tickers. Any analysis conditioning on market cap
inherits that.

Not fixed because the obvious fix is not clean: yfinance's `get_shares_full`
(what `run_shares` falls back to) and `.info["sharesOutstanding"]` **disagree
for NVO**, so "prefer yfinance for ADRs" trades one wrong number for another.
Options worth weighing: a per-ticker ADR-ratio constant in `core/config.py`
(only four tickers, and ADR 035 caps the list); preferring `.info` for ADRs
specifically; or excluding ADRs from `crit_mcap` and documenting it.

`data/min_mkcp_200b.csv` was regenerated from the current universe and
therefore carries the inflated TSM figure. Regenerate it after fixing.

## What changed today

Fifteen defects found and fixed. Every one was invisible until the pipeline
ran end to end for the first time.

| # | Fix | Commit |
|---|---|---|
| 1 | SEC submissions pagination — `filings.recent` capped near 1000 filings | `bfee46f` |
| 2 | Purged 13 symbol-reuse impostors | `015ca0c`, `4022edc`, `bfa605f` |
| 3 | Added ADR 035's ADRs (TSM, ASML, SAP, NVO) | `015ca0c` |
| 4 | `shares_outstanding` — SEC tag fallback + yfinance history | `44d25df` |
| 5 | Universe criteria — `required_criteria`, sector fallback, `FutureQuarterError` | `8c57815` |
| 6 | Stooq cross-check raised for every ticker, silently swallowed | `0a421a6` |
| 7 | Missing-bar check documented but never implemented | `0a421a6` |
| 8 | `scan()` read indicators at t instead of t−1 | `8c57815` |
| 9 | CI had never run (`working-directory` pointed at a path with no pyproject) | `e18b995` |
| 10 | `run_earnings` / `run_shares` CardinalityViolation, wrote zero rows | `dca2ee2` |
| 11 | Lint and format debt CI would now catch | `24e8592` |
| 12 | Removed `clear_test_bars.py`, an unguarded DELETE at repo root | `4022edc` |
| 13 | Stale rejects made validation permanently dirty | `1cfad99` |
| 14 | Removed Stooq (vendor now blocks automation); single-source on Yahoo | `9a759ed` |
| 15 | Restated the Phase 3 event-rate criterion (BUILD §9a) | `9a759ed` |

Measured effect: `days_to_earnings` impossible values 34.2% → 2.8%, hard
rejects 1,425 → 0 unresolved, `in_trade` 0 → 39, BRK-B market cap
$480M → $1.10T, missing bars 66 → 0.

## Resolved, for the record

**The event count is not a bug.** BUILD.md's old "~4% of ticker-days for
confluence" named neither side nor price field, and the readings span
four-fold: `confluence_low` on closes 4.43%, on intraday extremes 7.16%,
either side on extremes 18.34%. The engine emits 18.34%, and ADR 005
mandates extremes ("a daily bar's extremes are the intraday touch"). The ~4%
figure matched the close-based reading the engine deliberately does not
implement. The engine also reconciles with itself: 18.34% computed from bars
against 18.24% measured independently from `events`. BUILD.md §9a replaces
the point estimate with structural invariants, component rates against
independent predictions, and a deliberately wide 10-25% band — the measured
value was *not* written in, because a criterion set to the engine's own
output can never fail.

## Still outstanding, non-blocking

1. **`universe_union.csv` → `tickers` sync does not exist.** ADR 055 calls the
   CSV the frozen authoritative universe, but nothing reads it into Postgres.
   The four ADRs were added by hand and vanish if anyone runs
   `cscan membership --force`. Still a design decision, not a queued task.
2. **`sector` and `cik` null for former members.** `tickers --refresh` only
   upserts current constituents. The null `cik` matters most: it is the key
   that would have caught every symbol-reuse impostor found by hand today.
3. **`RESULTS.md` still describes the abandoned 51-ticker dry run.** BUILD.md
   §7.3 wants ticker count, bar count, reject counts by rule, coverage gaps,
   and dropped tickers with reasons — all queryable now.
4. **Phase 1 gate deliberately skipped** (user's call; Phase 2 is a fast
   Monday check). Worth running `cscan scan --ticker TSM --start 2026-07-01
   --end 2026-07-31` as a smoke test — it should now show the 07-29 bands
   (456.523644 / 418.677000 / 380.830356), not 07-30's.
5. **CI has never actually run on GitHub.** Locally everything passes, but the
   workflow fix is unverified against a real runner, including whether
   `uv sync --extra dev` behaves as reasoned.
6. **Single-source data.** Stooq is gone, so nothing independently
   cross-checks Yahoo. A keyed vendor (Alpha Vantage, Tiingo) would restore
   it if that matters later.

## Lessons worth carrying

- **Five confident diagnoses of mine were wrong today**, and only direct
  measurement caught them: a CRLF-corrupted `comm` that "proved" NVDA was
  missing; the YHD-exchange tickers I called "the clean, safe win" that were
  spliced; share-class blindness that was actually SEC dropping a tag; an
  empty `benchmarks` table I blamed for a null-sector bug; and "DESIGN §5.8"
  cited repeatedly for the Stooq check when §5.8 is Parallelism and
  determinism. Check the data before trusting the story.
- **Price-range ratio (max/min) is the cheapest impostor test.** No external
  data, no name matching, no removal dates. A legitimate 17-year equity
  series spans 50-80x; spliced ones span thousands. Pair it with hard-reject
  count and a dead-company end date.
- **A `@cached` fetcher turns a transient failure into a permanent one.** The
  wrapper writes the parsed frame to parquet, so a partial result is served
  to every future call including the repair run. Raise rather than skip.
- **Silent success is the dominant failure mode here.** CI reported green by
  never running. Stooq reported clean by always raising. `days_to_earnings`
  reported 98% populated while a third was impossible. `bar_rejects` made
  validation permanently dirty by counting resolved rows. Assert that a check
  actually ran, not just that it did not complain.
- **Never set an acceptance criterion to the system's own measured output.**
  It passes the day you write it and every day after, including the days
  something breaks.
