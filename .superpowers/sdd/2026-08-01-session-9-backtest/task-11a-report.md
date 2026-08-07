# Task 11a report: `cscan backtest` CLI wiring

Scope: CLI command and its tests only. No `cscan backtest` invocation was run
against a database; no database was connected to at any point in this task.

## What I implemented

Added `cscan backtest [--tickers] [--workers N] [--sweep] [--config-name NAME]`
to `capitalscan/jobs/cli.py`, plus four helper functions:

- `_prior_clean_default_run_exists(engine, config_hash) -> bool` — the ADR 059
  `--sweep` ordering gate.
- `_load_bars_by_ticker(engine, tickers, config) -> dict[str, pd.DataFrame]` —
  builds the harness's merged bars+indicators shape.
- `_load_events_for_run(engine, run_id) -> pd.DataFrame` — reads back the
  `events` rows this run wrote, for the harness's `events` argument.
- `_print_harness_report(report) -> None` — prints the five `HarnessReport`
  checks pass/fail.

Command flow:

1. `--config-name` given → hard error, exit 1, nothing else runs.
2. Resolve `config = Config()` (the dataclass defaults) and its `config_hash`.
3. `--sweep` given → check the gate. Fails the gate → error citing ADR 059,
   exit 1. Passes the gate → note that the sweep itself is Task 12 scope,
   exit 1. Either way, `run_backtest` is never called under `--sweep`.
4. Otherwise: resolve tickers via `_resolve_tickers`, derive `full_universe`,
   record a `runs` row via `ingest.run_job("backtest", …)`, call
   `research.backtest.run_backtest`, handle `BacktestRunFailed` and partial
   failure, print `config_hash` and the row/ticker counts, then run
   `research.harness.run_harness` automatically against what was written
   and gate the exit code on it.

## What I tested and results

New file: `capitalscan/tests/unit/test_backtest_cli.py`, 19 tests. All patch
`db_io.get_engine`, `ingest.run_job`, `research.backtest.run_backtest`, and
`research.harness.run_harness` (or the module's own `_prior_clean_default_run_exists`
/ `_load_bars_by_ticker` / `_load_events_for_run`) — no test touches a real
connection.

Covers: ExitParams defaults match ADR 059's k=1.5/4%/atr; `--workers` default
is 1 and passed through as `max_workers`; `--tickers` reuses `_resolve_tickers`
and forces `full_universe=False`; no `--tickers` keeps `full_universe=True`;
the exact `Config()` default (not a re-literalized copy) reaches
`run_backtest`; `--sweep` refuses without a prior clean run and cites ADR 059;
`--sweep` with a prior clean run still refuses to execute (Task 12 note) and
never calls `run_backtest`; the gate helper's SQL filters (`job='backtest'`,
`status='ok'`, `notes IS NULL`, `config_hash`, `full_universe='true'`) against
a fake connection; `--config-name` refuses without calling `run_backtest`;
`BacktestRunFailed` prints a clean message and exits 1 without a traceback;
partial failure prints the failed ticker(s) and exits 1; a fully clean run
with a passing harness does not raise; `config_hash` appears in the printed
output; the harness runs automatically and gates the exit code on failure;
the harness is skipped (not called) when zero events were written; and
`_load_bars_by_ticker` merges bars+indicators correctly and skips a ticker
with no bars.

## TDD Evidence

RED — `git stash push -- capitalscan/jobs/cli.py` (test file already present,
untracked), then:

```
uv run pytest capitalscan/tests/unit/test_backtest_cli.py -q
```
```
E   AttributeError: module 'capitalscan.jobs.cli' has no attribute '_prior_clean_default_run_exists'
=========================== short test summary info ===========================
ERROR capitalscan/tests/unit/test_backtest_cli.py - AttributeError: module 'c...
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.61s ===============================
```

Failed for the right reason: the helper (and the `backtest` command) simply
did not exist yet on `cli.py`.

`git stash pop` restored the implementation.

GREEN:
```
uv run pytest capitalscan/tests/unit/test_backtest_cli.py -q
```
```
capitalscan\tests\unit\test_backtest_cli.py ...................          [100%]
============================= 19 passed in 0.14s ==============================
```

Full safe suite:
```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
```
```
============================ 676 passed in 27.71s =============================
```

## Files changed

- `capitalscan/jobs/cli.py` — added the `backtest` command and its four
  helpers.
- `capitalscan/tests/unit/test_backtest_cli.py` — new, 19 tests.

Commits:
- `ec11294` — Add cscan backtest CLI command (Task 11a: wiring only, no execution)
- `74546bd` — Move backtest's dataclasses.asdict import to the top of the function body (cosmetic follow-up)

## How I detect a prior clean default-config run for the `--sweep` guard

`_prior_clean_default_run_exists(engine, config_hash)` queries `runs`:

```sql
SELECT 1 FROM runs WHERE job = 'backtest' AND status = 'ok'
  AND notes IS NULL AND params->>'config_hash' = :chash
  AND params->>'full_universe' = 'true' LIMIT 1
```

`status = 'ok'` comes from `ingest.run_job`'s own bookkeeping (`'running'` on
entry, `'ok'`/`'failed'` on exit — `_finish_run` in `jobs/ingest.py`). A
`BacktestRunFailed` (every dispatched ticker raised — a config-level fault)
propagates out of the `with ingest.run_job(...)` block, so `run_job` marks
that row `'failed'` and the gate correctly excludes it.

`notes IS NULL` is my addition beyond bare `status = 'ok'`: `backtest()`
writes a non-null `report.notes` the instant `BacktestReport.failed_tickers`
is non-empty (a *partial* failure — `run_backtest` does not raise for this,
it writes what succeeded). A run that silently dropped tickers is exactly the
"buggy engine" scenario ADR 059 exists to catch before a sweep runs 18 times
against it, so I do not treat `status='ok'` alone as "clean."

`params->>'full_universe' = 'true'` excludes a `--tickers` debug/partial run
from satisfying the gate — it never claims universe-wide coverage, so it
cannot stand in for the full-universe validation ADR 059 requires.

`params->>'config_hash' = :chash` scopes the check to *this* config
specifically — a clean run of a different config says nothing about this
one.

The full resolved `Config` (via `dataclasses.asdict`) is also stored in
`runs.params`, alongside `config_hash`, `full_universe`, `workers`, and
`n_tickers` — matching `core/config.py`'s own module docstring ("Each
backtest serializes its full config into `runs.params`, so reproducing a
result means reading one JSON blob").

## What I decided `--config-name` means

I could not justify a definition. There is exactly one config path in this
codebase (`jobs.config.resolve_config`: CLI overrides > env > `config.toml` >
dataclass default), and it produces one `Config` per invocation with no name
attached anywhere — no registry of named configs exists to select from, and
building one was not in this task's scope (the brief only asked me to resolve
the ambiguity, not invent a feature). So `--config-name <anything>` is a hard
error: it prints an explanation and exits 1 without calling `run_backtest`,
rather than silently ignoring the value (worse) or guessing a mapping to
apply it against (scope creep + a second config-resolution path, which
invariant 2 makes unattractive on its own).

## Whether I wired the harness, and my reasoning

Wired automatically, not behind a separate flag or command. ADR 059's
ordering rule is "default config, full harness, then hand-inspect ~20 events,
*then* sweep" — making the harness a manual second step would let a human
skip straight from a clean-looking run summary to `--sweep` (or to trusting
the numbers) without ever running the gate the ADR requires. The harness is
also cheap relative to the run itself: it re-reads only the tickers and
`run_id` this invocation actually touched, not the whole `events` table or
the whole trade universe.

It is skipped only when `BacktestReport.tickers` is empty (nothing was
written — nothing to check), and its five-check pass/fail gates the CLI's
exit code alongside partial ticker failure.

## How `--tickers` interacts with `full_universe`

`full_universe = tickers is None`. Passing `--tickers` at all — even a single
ticker, even one that happens to be the whole active universe — sets
`full_universe=False` and is passed through to `run_backtest`. This mirrors
`run_backtest`'s own contract: a subset run's `cofire_count` is only correct
*within* that subset, so `full_universe=False` drops `cofire_count` from the
write's `update_columns`, and `run_backtest` raises a `UserWarning` making the
gap visible. I did not try to detect "the --tickers list happens to equal the
full active universe" and treat that as `full_universe=True` — that would
require re-deriving the active-ticker set and comparing, which is exactly the
kind of implicit inference `run_backtest`'s own docstring argues against
("that judgment belongs to the caller ... rather than inferring it here").
`--tickers` is explicit user intent to run a subset; the CLI takes it at face
value.

## What the CLI does on `BacktestRunFailed` vs a partial failure, and the exit codes

- **`BacktestRunFailed`** (every dispatched ticker's worker raised — a
  config-level fault): caught outside the `with ingest.run_job(...)` block
  (it is allowed to propagate through that block first, so `run_job`'s own
  exception handler marks the `runs` row `'failed'` with `notes=str(exc)`).
  The CLI then prints a clean one-paragraph error naming the failure and
  exits 1. No traceback reaches the terminal.
- **Partial failure** (`BacktestReport.failed_tickers` non-empty, at least
  one ticker succeeded): `run_backtest` returns normally; the `runs` row is
  marked `'ok'` by `run_job`, but I set `report.notes` to a count + a sample
  of up to 10 failed tickers (`+N more` beyond that) before the `with` block
  exits, so the note lands in the database too — this is also what makes
  `notes IS NULL` in the `--sweep` gate correctly exclude it from counting as
  "clean." The CLI prints the same failed-ticker summary to the console and
  **also exits 1**.

  Reasoning for exiting 1 rather than 0 on a partial failure: this is
  scheduled under Windows Task Scheduler with catch-up (CLAUDE.md
  "Platform"), and a scheduler or any calling script needs a way to
  distinguish "wrote what I could, but something needs attention" from a
  clean run without parsing console text. The alternative — exit 0 with a
  loud console message — is exactly the failure mode CONSTRAINTS.md and the
  task brief call out repeatedly ("a run that silently succeeds with 200 of
  600 tickers failed"). I used the same exit code (1) for both cases rather
  than inventing a third code, since I could not find a caller in this
  codebase that would need to distinguish "total failure" from "partial
  failure with an otherwise-good run" programmatically — both currently mean
  "a human needs to look at this before trusting the row count."
- **Harness failure** (harness ran, at least one of the five checks failed):
  also folds into the same `exit_code = 1` path, for the same reason —
  Task Scheduler catch-up should not treat an engine that fails its own
  Phase-3 gate as a clean nightly/weekly run.

## Self-review findings

- Confirmed `ExitParams` defaults are `stop_mode="atr"`, `stop_atr_k=1.5`,
  `target_pct=0.04` by reading `core/config.py` directly (not by trusting the
  brief's summary) before writing `test_exit_params_defaults_match_adr_059`
  — this is the test that actually enforces invariant 9 here, since the CLI
  itself never restates these as literals; it only calls `Config()`.
- Caught a self-inflicted test bug during the first run: my `autouse` fixture
  patches `cli._prior_clean_default_run_exists` and `cli._load_bars_by_ticker`
  to inert stand-ins for every test, which broke the two tests meant to
  exercise those functions' *real* bodies (they were calling the patched
  stand-in through the module attribute, not the real function). Fixed by
  capturing the real function objects at module import time, before the
  fixture ever runs, and calling those captured references directly in the
  two tests that need the real implementation.
- Ran the full harness JSON payload through my head once more: `runs.params`
  now stores the entire resolved `Config` via `asdict`, not just
  `config_hash` — this matches `core/config.py`'s own stated intent
  ("reproducing a result means reading one JSON blob") and costs nothing,
  since `params` is already `jsonb`.
- Considered whether `_load_bars_by_ticker` should reuse
  `research.backtest._read_bars`/`_read_indicators` instead of writing its
  own SQL. Decided against: those return every column (including
  `interval`, `source`, `ingested_at`, `computed_at`, `run_id`), which would
  leak non-indicator columns into the harness's no-look-ahead shift ladder
  (`harness._check_no_lookahead` treats anything outside `_BAR_COLUMNS` as an
  indicator to shift). A column-scoped read is the correct shape for this
  caller; it is not a second copy of the backtest engine's IO logic
  (invariant 2 covers detection/exit/config *logic*, not "which columns a
  read selects").
- Checked that no literal duplicates a config value anywhere in the new
  code — the ticker-sample truncation (`failed[:10]`) is a display-only
  constant, same category as the existing `timedelta(days=5)` /
  `timedelta(days=730)` literals already in `cli.py`'s `bars`/`nightly`
  commands, not a modeling parameter.
- Verified `--sweep` never calls `run_backtest` in either branch (gate fail
  or gate pass) — both paths in the test suite assert
  `run_backtest_called` stays empty.

## Issues or concerns

- **`_load_bars_by_ticker` reads one ticker at a time in a loop.** For the
  eventual 11b run (~600+ tickers) this is a lot of round trips. I left it
  this way rather than batching into one `WHERE ticker = ANY(:tickers)`
  query, because `research.backtest._read_bars`/`_read_indicators` use the
  same one-ticker-per-call shape (CLAUDE.md "One ticker per `core/` function
  call" — this extends by convention to `research`'s own per-ticker reads),
  and because the harness's `_indexed_bars` step needs a per-ticker dict
  either way. If 11b's actual run turns out to be too slow because of this,
  batching the two `SELECT`s into one query each (grouped in Python
  afterward) is a contained follow-up, not a redesign.
- **The harness now runs inside `cscan backtest` unconditionally on every
  non-sweep invocation**, including a small `--tickers` debug run during
  development. That is deliberate (see "harness wiring" above) but worth
  flagging: it means even a one-ticker smoke-test invocation will read that
  ticker's bars+indicators twice (once inside `run_backtest`'s worker, once
  here for the harness) and do a real query against `events`. This is
  inherent to the requirement, not a bug, but it changes what "a quick
  debug run" costs.
- I did not add a `--config-name` value that maps to anything, per the task's
  explicit permission to refuse rather than guess. If a named-config
  registry becomes real scope later, this refusal is the marker to replace.
