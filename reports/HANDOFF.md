# Handoff — 2026-08-01

Branch `session-9-backtest`, forked from `main` at `9777377`.

Read this plus `docs/superpowers/plans/2026-08-01-session-9-backtest.md` and you
have everything. The rest of that session's detail is in the commit messages.

## Start here

Session 9 is planned and ready to execute:
`docs/superpowers/plans/2026-08-01-session-9-backtest.md`, twelve tasks, intended
for `superpowers:subagent-driven-development`. The user pre-approved it on the
condition that it matches BUILD.md §9 and DESIGN §5.2/§5.4/§5.10. Departing from
those docs needs their sign-off first.

Task 1 is the BUILD.md 9.0 prerequisite (wire hourly into the nightly chain).
Tasks 2-12 are the engine. Every task's tests stub IO, so all of them can be
implemented before the data finishes rebuilding.

## Hard safety rules

- **Never run bare `pytest`.** `pyproject.toml` sets
  `testpaths = ["capitalscan/tests"]`, so it collects the integration suite,
  which runs `TRUNCATE TABLE bars CASCADE` against 4.6M rows.
  Use `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.
- Nothing under `capitalscan/tests/integration/` while real data matters.
  `test_ingest.py` and `test_compute.py` truncate `bars`; `test_poll.py`
  truncates `tickers`, which CASCADEs to `bars`.
- No `cscan db migrate` or `uv sync` while a job is running.
- `docker` is not on PATH in agent shells. Use
  `PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan`.
  Prefix `SET max_parallel_workers_per_gather=0;` if a query hits a
  shared-memory error.
- Put these rules verbatim in every subagent prompt. Three agents ran safely
  today because of it.

## State as of this handoff

`cscan events --lookback 6056` was running. Everything before it completed:

| Job | Result |
|---|---|
| `earnings --historical` | 176,398 rows, 483/607 tickers now reach 2010 (was 14) |
| `indicators --workers 8 --lookback 6500` | 2,529,289 rows, 23 min |
| `universe --quarter 2026Q2` | 621 rows, **39 in_trade** (was 0) |
| `events --lookback 6056` | was running |

Verify events finished, then run `cscan validate --report`.

Data: 4.6M daily bars + 2.08M hourly across 625 active tickers, daily from
2009-01-02, hourly from 2024-08-06, events from 2010-01-04.

## What changed today

Twelve defects found and fixed. Every one was invisible until the pipeline ran
end to end for the first time.

| # | Fix | Commit |
|---|---|---|
| 1 | SEC submissions pagination — `filings.recent` capped at ~1000 filings | `bfee46f` |
| 2 | Purged 12 symbol-reuse impostors | `015ca0c`, `4022edc`, + this session |
| 3 | Added ADR 035's ADRs (TSM, ASML, SAP, NVO) | `015ca0c` |
| 4 | `shares_outstanding` — SEC tag fallback + yfinance history | `44d25df` |
| 5 | Universe criteria — `required_criteria`, sector fallback, `FutureQuarterError` | `8c57815` |
| 6 | Stooq cross-check raised for every ticker, silently swallowed | `0a421a6` |
| 7 | Missing-bar check documented but never implemented | `0a421a6` |
| 8 | `scan()` read indicators at t instead of t−1 | `8c57815` |
| 9 | CI had never run (`working-directory` pointed at a path with no pyproject) | `e18b995` |
| 10 | `run_earnings` / `run_shares` CardinalityViolation, wrote zero rows | `dca2ee2` |
| 11 | Lint and format debt CI will now catch | `24e8592` |
| 12 | Removed `clear_test_bars.py`, an unguarded DELETE at repo root | `4022edc` |

Measured effect: `days_to_earnings` impossible values 34.2% → 2.8%,
hard rejects 1,425 → 28, `in_trade` 0 → 39, BRK-B market cap $480M → $1.10T.

## Outstanding

**Blocking nothing, but real.**

1. **`universe_union.csv` → `tickers` sync does not exist.** ADR 055 calls the
   CSV the frozen authoritative universe, but nothing reads it into Postgres.
   The four ADRs were added by hand and will vanish if anyone runs
   `cscan membership --force`. This is the durable fix for item 3 and it is
   still a design decision, not a queued task.
2. **`sector` and `cik` are null for 208 former members.** `tickers --refresh`
   only upserts current constituents. The null `cik` matters most: it is the
   key that would have caught every symbol-reuse impostor found by hand today.
3. **Stooq cross-check returns UNAVAILABLE.** The code defect is fixed, but the
   endpoint returned 404 or a JS bot challenge from the agent's network. Needs
   confirming from the user's own machine before being called a code problem.
4. **`RESULTS.md` still describes the abandoned 51-ticker dry run.** BUILD.md
   §7.3 wants ticker count, bar count, reject counts by rule, coverage gaps,
   and dropped tickers with reasons. All queryable once events finishes.
5. **Phase 1 gate deliberately skipped.** The user's call — Phase 2 is a fast
   Monday check. Worth running `cscan scan --ticker TSM --start 2026-07-01
   --end 2026-07-31` as a smoke test anyway; it should now show the 07-29 bands
   (456.523644 / 418.677000 / 380.830356), not 07-30's.
6. **Event count needs re-measuring.** Pre-fix, confluence fired on ~19% of
   ticker-days against BUILD.md's expected ~4%. That predates the purges and
   universe fixes. Re-measure before concluding the engine is wrong — it is the
   Phase 3 criterion most likely to fail.
7. **CI will be red on first push** until someone confirms the workflow runs on
   GitHub. Locally: 461 tests pass, ruff clean, mypy clean across 93 files,
   coverage 98.03% on `core/`.

## Lessons worth carrying

- **Four confident diagnoses of mine were wrong today**, and only direct
  measurement caught them: a CRLF-corrupted `comm` that "proved" NVDA was
  missing; the YHD-exchange tickers I called "the clean, safe win" that turned
  out to be spliced; share-class blindness that was actually SEC dropping a tag;
  and an empty `benchmarks` table I blamed for a null-sector bug. Check the data
  before trusting the story.
- **Price-range ratio (max/min) is the cheapest impostor test.** No external
  data, no name matching, no removal dates. A legitimate 17-year equity series
  spans 50-80x; spliced ones span thousands. Pair it with hard-reject count and
  a dead-company end date.
- **A `@cached` fetcher turns a transient failure into a permanent one.** The
  wrapper writes the parsed frame to parquet, so a partial result is served to
  every future call including the repair run. Raise rather than skip.
- **Silent success is the dominant failure mode here.** CI reported green by
  never running. Stooq reported clean by always raising. `days_to_earnings`
  reported 98% populated by being a third wrong. Assert that a check actually
  ran, not just that it did not complain.
