# Task 1 Report: Wire the hourly pull into the nightly chain

## What I implemented

Added a call to `ingest.run_bars_hourly(tickers, start, end, engine=engine)` in
`capitalscan/jobs/cli.py`'s `nightly()` function, immediately after the existing
`ingest.run_bars_daily(...)` call, using the same `tickers`, `start`, `end` window
already computed for the daily pull (`start = end - timedelta(days=5)`). This keeps
the hourly `bars` table current so `core.returns.entry_price_for` doesn't fall back
to NaN for two of the four entry kinds once Task 5 (backtest engine) depends on it.

A comment above the new line explains why it's there (BUILD.md §9.0), what it
protects against (stale hourly table -> NaN entry prices -> silently dropped entry
kinds), and the cost tradeoff (one 60-day window per ticker via the 5-day `start`,
~21 min for ~630 tickers at `RATE_LIMIT_PER_SEC = 0.5`, vs. hours for the 13-window
730-day backfill the `bars --hourly --backfill` CLI path walks).

No changes to `core/`, `docs/`, or any other job module. Left the pre-existing
uncommitted `max_workers=1` change to `compute.run_indicators` in `nightly()` alone
(same file, brief said including it in the commit is fine) and left
`docker-compose.yml`'s `max_connections=200` change unstaged/untouched.

## What I tested

New file: `capitalscan/tests/unit/test_nightly_chain.py`.

`test_nightly_calls_run_bars_hourly_with_daily_window`:
- Monkeypatches `db_io.get_engine`, `cli._resolve_tickers`, and
  `scheduled_runs.record` (autouse fixture) so the test never touches a real
  database or engine object.
- Monkeypatches every `ingest.run_*` and `compute.run_*` function `nightly()`
  invokes onto the **module objects** (`ingest.run_bars_daily = ...`, etc.), not
  names imported into the test file, per the task note that `nightly()` imports
  `compute, db_io, ingest, scheduled_runs` inside the function body — patching
  has to hit the attribute on the module, which it does either way since Python
  module objects are singletons in `sys.modules`, but the test does it explicitly
  to make that requirement visible in the test itself.
- Each patched function appends `{name, args, kwargs}` to a shared list instead of
  doing real work.
- Calls the real, unmodified `cli.nightly()`.
- Asserts `"run_bars_hourly"` appears in the recorded call names.
- Asserts the hourly call's positional `start`/`end` (args[1], args[2] — `nightly()`
  passes `tickers, start, end` positionally, not as kwargs) equal the daily call's
  `start`/`end`.
- Pins the actual window values (`end - timedelta(days=5)` to `end`) so a future
  change to the lookback window can't silently pass just because daily and hourly
  happen to still agree with each other.

## TDD Evidence

### RED

Command:
```
uv run pytest capitalscan/tests/unit/test_nightly_chain.py -v
```

Output (relevant excerpt):
```
FAILED capitalscan/tests/unit/test_nightly_chain.py::test_nightly_calls_run_bars_hourly_with_daily_window - AssertionError: nightly() never called run_bars_hourly
assert 'run_bars_hourly' in ['run_bars_daily', 'run_actions', 'run_market', 'run_shares', 'run_earnings', 'run_indicators', 'run_events']
============================== 1 failed in 0.59s ==============================
```

This is the expected failure: `run_bars_hourly` was recorded as absent from the
call list because `nightly()` didn't call it yet, and the recorded call list shows
exactly the six pre-existing calls in order, confirming the monkeypatching worked
correctly (nothing touched the real database) and the test fails for the right
reason rather than an error in test setup.

### GREEN

Command:
```
uv run pytest capitalscan/tests/unit/test_nightly_chain.py -v
```

Output:
```
capitalscan/tests/unit/test_nightly_chain.py::test_nightly_calls_run_bars_hourly_with_daily_window PASSED [100%]
============================== 1 passed in 0.07s ==============================
```

Full suite, run once before committing:
```
uv run pytest capitalscan/tests/unit capitalscan/tests/property
============================ 472 passed in 24.30s =============================
```

## Files changed

- `capitalscan/jobs/cli.py` — added `ingest.run_bars_hourly(...)` call inside
  `nightly()`, with explanatory comment (BUILD.md §9.0). Also carries the
  pre-existing, out-of-scope `max_workers=1` addition to `run_indicators` that was
  already in the working tree before this task started (left as-is, included in
  the commit per instructions since it's the same file).
- `capitalscan/tests/unit/test_nightly_chain.py` (new) — the RED/GREEN test above.

Commit: `c5b0a0a` "Wire hourly bars into the nightly chain (BUILD 9.0)"
(`docker-compose.yml`'s pre-existing `max_connections=200` change was left
unstaged, not part of this commit.)

## Self-review

- **Completeness against brief**: all 5 steps done — failing test written first,
  run and confirmed failing for the stated reason, implementation added
  immediately after `run_bars_daily`, full unit+property suite run, commit made
  with the exact message the brief specifies.
- **Naming**: test file and function names follow the existing convention seen in
  `test_bars_hourly_checkpoint.py`, `test_run_validate.py`, etc. (`test_<subject>`
  in `tests/unit`).
- **YAGNI**: no test infrastructure beyond what's needed — no new fixtures added to
  the shared `conftest.py`, no helper functions beyond the one small
  `_record_call` closure, no assertions beyond what the brief asked for plus one
  extra pin on the literal window (justified above, not gold-plating: it protects
  the "same window" assertion from becoming vacuous).
- **Test verifies real behavior, not mock behavior**: the function under test
  (`cli.nightly()`) runs unmodified; only its IO dependencies (`db_io.get_engine`,
  ticker resolution, `scheduled_runs.record`, and the six `ingest`/`compute` job
  functions) are stubbed. The assertion is on what `nightly()` actually passed to
  `run_bars_hourly`, not on whether a mock was "called" in the abstract — it
  checks the values reached it and that the RED failure was a plain "never
  called" absence rather than a setup error.
- **Pristine test output**: full suite run shows `472 passed` with no warnings
  emitted in the tail of the output; the new test alone shows `1 passed` with no
  extra printed noise beyond `nightly:` console output during the RED run.

## Issues or concerns

None. The task was self-contained and matched the brief exactly; no ambiguity
required escalation.
