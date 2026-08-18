# Sweep wiring report

Wires `cscan backtest --sweep` to actually run the 18-config exit sweep after
the ADR 059 ordering gate passes. `research.backtest.sweep_configs` already
existed and was already correct (18 distinct hashes, `stop_atr_k` collapsed
under `fixed`/`none`) — nothing called it. This closes that gap.

## Where the loop lives

In `capitalscan/jobs/cli.py`, inside the `backtest` command's `if sweep:`
branch. Not a new helper in `research/backtest.py`.

Reasoning:

- The loop needs three things that are already CLI/jobs-layer concerns, not
  research-layer ones: `ingest.run_job` (per-config `runs` bookkeeping — the
  same helper the non-sweep path already uses once), `rich.progress.Progress`
  (CLAUDE.md's 30-second rule; `jobs/ingest.py:run_bars_hourly` sets the
  precedent of putting `Progress` next to the loop it reports on, not one
  layer down), and human-facing `console.print` summaries/exit codes.
- `research/backtest.py`'s own docstring on `sweep_configs` already draws
  this exact line on purpose: config generation "does not run anything...
  out of scope here." Keeping execution in the CLI preserves that split —
  `research/backtest.py` answers "what are the 18 configs, and how do I run
  one," the CLI answers "how do I run all 18 for a human, with resume and
  progress."
- No second orchestration layer: the sweep loop is a thin wrapper calling
  `sweep_configs` (unmodified) and `run_backtest` (unmodified) once per
  config. Adding a `research.backtest.run_sweep()` that itself wraps
  `ingest.run_job` would just relocate the same 20 lines one file over while
  adding an import of `jobs.ingest` into `research/backtest.py`, which it
  doesn't currently have and doesn't need for anything else.
- Testability was the other stated option's argument, but it doesn't apply
  here: `capitalscan/tests/unit/test_backtest_cli.py` already calls
  `cli.backtest(...)` as a plain function, bypassing Typer's dispatch
  entirely, so a CLI-resident loop is exactly as unit-testable as a
  research-resident one would be.

Two new module-level helpers in `cli.py`, next to the existing
`_prior_clean_default_run_exists`:

- `_sweep_config_already_done(engine, config_hash) -> bool` — the resume
  check (see below).
- The sweep block itself, inline in `backtest()`.

`research/backtest.py`, `research/harness.py`, `research/candidates.py`,
`research/enrich.py` are all untouched.

## What each config writes

Confirmed rather than assumed, per the brief. `run_backtest` already scopes
its upsert to `_RUN_BACKTEST_UPDATE_COLUMNS` and conflicts on
`["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"]`
(`research/backtest.py:866-872`) — `config_hash` is part of the conflict key,
so 18 configs' rows coexist in `events` without colliding, exactly as DESIGN
§5.7 describes. No change was needed here; the sweep loop calls
`run_backtest` unmodified, once per config, and each call's upsert only ever
touches that config's own rows.

## `run_id` decision: one per config, 18 total

Each of the 18 `run_backtest` calls gets its own `run_id` and its own
`runs` row (`job = 'backtest_sweep'`), created by wrapping each call in its
own `ingest.run_job(...)` context, the same helper the single-run path uses
once.

Justification: `runs` means one row per execution (CONSTRAINTS.md). Each
config dispatch is a distinct execution — its own ticker pass, its own
timing, its own possible partial-ticker failure — and `events` rows need a
`run_id` that identifies *which config's execution* wrote them
(`_load_events_for_run` already assumes this 1:1 shape for the non-sweep
harness path). Sharing one `run_id` across all 18 would make 18 different
`config_hash` values point at a single `runs` row whose `params` can only
describe one config, breaking that provenance and making the per-config
resume check (below) impossible to express as a `runs` query.

## Checkpoint / resume mechanism

Per-config, not per-ticker. `run_backtest` performs exactly one
`db_io.upsert` at the end of collecting all tickers for a given config — that
upsert is the atomic checkpoint unit. An interrupt before it lands means that
one config's events never landed at all (config discarded, rerun from
scratch); an interrupt after it means that config's `events` rows and its
`runs` row (`status='ok'`) are both durable, independent of every other
config. This mirrors `run_bars_hourly`'s per-ticker checkpoint
(`jobs/ingest.py:560-802`) one level up: same principle (commit the unit of
work immediately, don't buffer 18 configs' worth of writes for one final
flush), coarser grain (config instead of ticker), because a config's own
internal ticker dispatch inside `run_backtest` is not itself resumable
without changing that function (out of scope — Task 9b already landed and
is not part of this task's file list).

Resume: `_sweep_config_already_done(engine, config_hash)` queries `runs` for
`job='backtest_sweep' AND status='ok' AND notes IS NULL AND
params->>'config_hash' = :chash AND params->>'full_universe' = 'true'` —
identical shape to `_prior_clean_default_run_exists`, deliberately scoped to
a different `job` value so a completed sweep member is never confused with
the Task 11 default-config run. The sweep loop checks this before dispatching
each config; a hit skips straight to the next one. Rerunning `cscan backtest
--sweep` after an interrupt at config 15 checks configs 1-14, finds clean
rows, skips all 14, and resumes at 15. Nothing here relies on skip being
*correct for correctness* — the `events` upsert is idempotent on
`(config_hash, ticker, signal_date, signal_type, entry_kind)`, so re-running
an already-done config would just rewrite the same rows — the skip exists
purely to avoid burning ~20 minutes per already-finished config on a rerun.

A total-failure config (`BacktestRunFailed`, every ticker's worker raised)
stops the sweep loop immediately with a clear message naming which configs
already completed and that a rerun resumes from the failing one; a
partial-ticker failure (some tickers failed, not all) does not stop the
loop — `run_backtest` itself already treats that as a data problem, not a
config problem, records it on `report.notes`, and moves on. (`notes` being
non-null also means that config's `runs` row will never satisfy
`_sweep_config_already_done`, so a partial-failure config is retried, not
silently treated as done, on the next `--sweep` invocation.)

## Harness decision: not run per config, not run once over the sweep — skipped entirely for `--sweep`

DESIGN §5.9's ordering rule is: the *default* config passes the full
validation harness and ~20 hand-inspected events, **before** any sweep runs.
The ADR 059 gate this task builds on top of (`_prior_clean_default_run_exists`)
is exactly the mechanical proof that already happened — a sweep cannot start
without a `runs` row from a clean, full-universe, harness-passing default run
sitting in the database.

Running the harness 18 more times inside the sweep loop was rejected: at
~2h28m each (measured, per the brief) that is ~45 hours on top of the ~6-hour
sweep itself, and every one of the 18 configs shares the identical
detection/entry engine the harness already validated against the default
run — only `exits` differs between configs, and the harness's checks
(no-look-ahead, entry sanity, exit sanity, return identity, non-overlap) are
either entirely about detection/entry (already proven) or would need to be
re-derived per exit config to mean anything (exit sanity, non-overlap) —
which is a materially different, unscoped task, not "rerun the same check."

Running it once over the *sweep's* combined events was also rejected: the
harness's shape (`bars_by_ticker`, one no-look-ahead check, one
entry/exit-sanity pass) is built around a single run's events sharing one
config's exit logic; pointing it at 18 configs' worth of coexisting rows
would either silently check only one config's exit rules against all 18
configs' rows, or require changes to `research/harness.py` — explicitly out
of scope for this task.

So: the sweep loop calls `run_backtest` only. No `run_harness` call anywhere
in the `if sweep:` branch.

## TDD evidence

RED — new tests against not-yet-existing behavior:

```
$ uv run pytest capitalscan/tests/unit/test_backtest_cli.py -q
...
FAILED test_sweep_with_prior_clean_run_does_not_run_a_single_default_backtest
FAILED test_sweep_dispatches_all_18_configs_in_deterministic_order
FAILED test_sweep_failure_at_config_n_does_not_discard_earlier_configs
FAILED test_sweep_resume_skips_already_completed_configs
FAILED test_sweep_config_already_done_queries_expected_filters
5 failed, 18 passed in 0.80s
```
All five failed with `AttributeError: ... has no attribute
'_sweep_config_already_done'` — the helper didn't exist yet.

GREEN — after implementing `_sweep_config_already_done` and the sweep loop
in `cli.py`:

```
$ uv run pytest capitalscan/tests/unit/test_backtest_cli.py -q
23 passed in 0.17s

$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
767 passed in 28.32s
```

New/changed tests, all in `capitalscan/tests/unit/test_backtest_cli.py`:

- `test_sweep_with_prior_clean_run_does_not_run_a_single_default_backtest`
  (replaces the old Task-11-era test that asserted the sweep was refused
  with a "Task 12" message — that expectation is exactly what this task
  removes) — asserts `run_backtest` is called 18 times, not once, once the
  gate passes.
- `test_sweep_dispatches_all_18_configs_in_deterministic_order` — asserts
  the 18 configs dispatched, in order, hash-match `sweep_configs(base)`
  exactly; asserts 18 distinct `run_id`s; asserts `workers`/tickers are
  threaded through unchanged.
- `test_sweep_failure_at_config_n_does_not_discard_earlier_configs` — a
  fake `run_backtest` raises `BacktestRunFailed` on the 5th call; asserts
  exactly 5 calls happened (configs 1-4 completed and written, 6-18 never
  dispatched) and the CLI reports which config failed.
- `test_sweep_resume_skips_already_completed_configs` — stubs
  `_sweep_config_already_done` to report the first 14 (by real
  `sweep_configs`/`config_hash` values) as already done; asserts
  `run_backtest` is called exactly 4 times, only for the remaining configs.
- `test_sweep_config_already_done_queries_expected_filters` — unit-tests the
  new helper's SQL shape against a fake connection, mirroring the existing
  test for `_prior_clean_default_run_exists`.

All tests stub `research.backtest.run_backtest` and `ingest.run_job` — no
real backtest executes and no real database connection is opened, per the
task's testing constraint.

## The exact command, and runtime estimate

```
cscan backtest --sweep
```

(No `--tickers` — a sweep is defined over the full trade universe, matching
the same `full_universe` convention the ADR 059 gate itself requires of the
prior default run. `--workers N` may be added for parallelism, same as the
non-sweep command.)

**Estimated runtime: ~6 hours**, serially (`--workers 1`), assuming the
ADR 059 gate has already passed (i.e., a prior `cscan backtest` run exists
and passed the harness — that prior run is not part of this estimate).
Reasoning: the brief states a measured full-universe pass is ~20 minutes for
the write phase alone; 18 configs x ~20 minutes = ~6 hours. This matches
DESIGN §5.9's own estimate style (~4-5 minutes stated there assumes the
"entry prices compute once, reused across exit configs" optimization
described in that section, which `run_backtest`/`_backtest_one_ticker`'s
current implementation does not yet apply per-config — each of the 18 calls
here independently recomputes candidates and entries; that optimization is
not part of this task's scope, since it lives inside `_backtest_one_ticker`
in `research/backtest.py`, explicitly out of bounds here). If interrupted,
rerunning the same command resumes rather than restarting — the completed
configs are skipped via `_sweep_config_already_done`, so a resumed run's
remaining cost is proportional to configs left, not the full 6 hours again.

## `config_hash(Config())` unchanged

No field was added to any `Config` member. Verified directly:

```
>>> from capitalscan.core.config import Config
>>> from capitalscan.jobs.config import config_hash
>>> config_hash(Config())
'3e598c59e7d71eae'
```

Matches the value stated in the task brief and CONSTRAINTS.md. Also verified
`sweep_configs(Config())` still returns exactly 18 configs with 18 distinct
hashes, and that one of the 18 (`stop_mode="atr", stop_atr_k=1.5,
target_pct=0.04`) equals the base hash itself — expected, since that
combination is the dataclass default and the grid is meant to include it.

## Concerns

- **The ~6-hour estimate assumes the entry-price-once optimization DESIGN
  §5.9 describes is not yet implemented.** If it already exists somewhere
  in `_backtest_one_ticker` (not verified here — that file is out of this
  task's scope to modify or deeply audit), the real runtime could be closer
  to DESIGN's stated ~4-5 minutes and this report's estimate would be
  conservatively high, not wrong-direction.
- **No progress checkpoint *within* a single config's ~20-minute write
  phase.** If a config is interrupted mid-dispatch, that entire config is
  redone from scratch on resume (not corrupted — just re-run) — this is a
  known, accepted coarser grain than `run_bars_hourly`'s per-ticker
  checkpoint, justified above, but worth flagging since a single config's
  ~20 minutes is itself close to CLAUDE.md's 10-minute checkpoint
  threshold.
- **`--tickers` combined with `--sweep` is technically still accepted** (the
  loop reuses `_resolve_tickers`/`full_universe` the same way the non-sweep
  path does) but is not the documented/expected usage — a partial-universe
  sweep produces `cofire_count` gaps the same way a partial-universe single
  run does (`run_backtest`'s existing `UserWarning`). Not restricted here
  since restricting it wasn't asked for and the existing single-run path
  allows the same subset debugging.
- **Two operators running `--sweep` concurrently could both pass
  `_sweep_config_already_done` for the same not-yet-finished config** and
  double-dispatch it — no locking was added. Matches the existing
  `_prior_clean_default_run_exists` gate's own concurrency assumption
  (single operator), not a new gap introduced here.
