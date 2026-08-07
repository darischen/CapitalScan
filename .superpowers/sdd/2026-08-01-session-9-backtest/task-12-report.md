# Task 12 report — sweep_configs

## What I implemented

`sweep_configs(base: BacktestConfig) -> list[BacktestConfig]` in
`capitalscan/research/backtest.py`. Pure config generator: 18
`dataclasses.replace`-derived variants of `base`, each varying only
`base.exits`. No IO, no ticker or entry/exit resolution, module remains
importable with no side effects.

Grid (DESIGN §5.9):

- `stop_mode="atr"` x `stop_atr_k` in `(1.0, 1.5, 2.0, 2.5)` -> 4 stop
  variants, each paired with all 3 `target_pct` values -> 12 configs
- `stop_mode="fixed"` -> 1 stop variant x 3 targets -> 3 configs
- `stop_mode="none"` -> 1 stop variant x 3 targets -> 3 configs
- Total: `(4 + 1 + 1) x 3 = 18`

## What I tested and results

`capitalscan/tests/unit/test_backtest_sweep.py`, 15 tests:

- Exactly 18 configs; all 18 `config_hash` values distinct
- All 4 `stop_atr_k` values present under `stop_mode="atr"`
- Stop-variant counts are 12/3/3 (atr/fixed/none)
- Every one of the 6 stop variants is paired with all 3 targets
- `stop_atr_k` under `fixed`/`none` equals the base config's value,
  including a non-default base value (proves it's held, not defaulted)
- `exit_stoch_threshold` / `exit_stoch_threshold_short` never vary,
  including with a non-default base value
- Every section of the returned configs except `exits` equals `base`'s
- `base` itself is not mutated
- `sweep_configs` calls neither `research.enrich.resolve_entries` nor
  `jobs.db_io.get_engine` (monkeypatched to raise if called)
- Returns `Config` instances
- `sweep_configs(None)` raises (no silent construction of a bogus config)

### TDD evidence

RED — before implementation, ImportError for the right reason:

```
$ uv run pytest capitalscan/tests/unit/test_backtest_sweep.py -q
...
E   ImportError: cannot import name 'sweep_configs' from 'capitalscan.research.backtest'
1 error in 0.68s
```

GREEN — after implementation:

```
$ uv run pytest capitalscan/tests/unit/test_backtest_sweep.py -q
15 passed in 0.07s
```

Full safe suite:

```
$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
690 passed, 1 failed in 24.96s
```

The 1 failure (`test_stop_exits_land_at_or_beyond_the_stop_level` in
`test_exit_invariants.py`) is a Hypothesis `FailedHealthCheck` about
filtered-input volume, not an assertion failure, in a file I never touched.
Re-running just that file in isolation passes cleanly:

```
$ uv run pytest capitalscan/tests/property/test_exit_invariants.py -q
5 passed in 6.84s
```

Pre-existing flakiness in an unrelated property test, not caused by this
change.

## Files changed

- `capitalscan/core/config.py` — added `SweepParams` (frozen dataclass,
  `stop_atr_ks`, `target_pcts`) and `DEFAULT_SWEEP = SweepParams()`
- `capitalscan/research/backtest.py` — added `sweep_configs`, imported
  `ExitParams`, `DEFAULT_SWEEP`, and `dataclasses.replace`; added
  `sweep_configs` to `__all__`
- `capitalscan/tests/unit/test_backtest_sweep.py` — new, 15 tests

## Inactive `stop_atr_k` under `fixed`/`none`

Held at `base.exits.stop_atr_k`, not swept, not normalized to a sentinel.
Under `stop_mode="fixed"`, the stop uses `stop_fixed_pct` instead; under
`stop_mode="none"` there is no stop at all (`core/exits.py`) — in both
cases `stop_atr_k` has zero effect on behavior. Sweeping it there would
manufacture configs that are behaviorally identical but hash differently
(exactly the 36-config trap the brief flags), so Phase 4 would report the
same backtest result twice under two different `config_hash` values, as if
they were independent findings. Holding it at whatever `base` already
carries — proven by `test_a_nondefault_base_stop_atr_k_is_preserved_under_
fixed_and_none`, which uses a non-default base value and checks it survives
— keeps every hash difference among the 18 configs tied to an actual
behavioral difference, and doesn't invent a new default the way normalizing
to some fixed placeholder value would.

## Where the grid values live, and why

`SweepParams` in `capitalscan/core/config.py`, a standalone frozen
dataclass (**not** a field of `Config`). Two constraints pointed here
together:

1. Invariant 9 forbids magic numbers outside `core/config.py`. The grid
   values (`1.0, 1.5, 2.0, 2.5` for `stop_atr_k`; `0.03, 0.04, 0.05` for
   `target_pct`) are the sweep definition, not incidental literals, but
   they're still numbers that belong in the config module by the letter
   of the rule.
2. Adding a field to `Config` (or nesting `SweepParams` inside it) would
   change `config_hash` for every resolved config, swept or not, because
   `config_hash` hashes `dataclasses.asdict(config)` — exactly the kind of
   hash-shifting change the brief calls out for prominent reporting
   (`fwd_ret_horizons`'s earlier `edf5658f5da3807a` -> `22df3117b890793b`).

`SweepParams` sidesteps the conflict: it's a `core/config.py` dataclass
(invariant 10 still holds — `dataclasses` remains the module's sole
import), but it is never referenced from inside `Config`, so it plays no
part in any `Config` instance's `asdict()`. `sweep_configs` reads
`DEFAULT_SWEEP.stop_atr_ks` / `DEFAULT_SWEEP.target_pcts` to build the 6
stop variants, the same way `research/backtest.py` already reads
`DEFAULT_CONFIG` elsewhere in the codebase.

## config_hash impact

**None.** I verified directly:

```
$ uv run python -c "from capitalscan.core.config import Config; from capitalscan.jobs.config import config_hash; print(config_hash(Config()))"
22df3117b890793b
```

Unchanged from the value already on record after the `fwd_ret_horizons`
change earlier this session. `SweepParams` is a new top-level name in
`core/config.py`, not a new field on `Config`, `ExitParams`, or any section
`Config` contains, so no existing or future non-swept config's hash moves.

## Honest state of the entry-reuse optimization

**Not implemented. Do not read the green test suite as proof it is.**

`sweep_configs` only builds `Config` objects. It has no opinion on how a
caller runs 18 configs, because at HEAD `run_backtest` gives it nowhere to
plug in a shared entry cache:

- `run_backtest(tickers, config, ...)` dispatches
  `_backtest_one_ticker(ticker, config, ...)` once per ticker, and that
  worker calls `research.enrich.resolve_entries` once per candidate
  **inside that call**, keyed to the one `config` it was handed.
- There is no notion of "entry parameters" separated from "exit
  parameters" anywhere in this path — `resolve_entries` takes `cp:
  CostParams`, not the full config, but nothing upstream of it caches its
  output keyed on `(ticker, candidate)` independent of `config.exits`.
- Calling `run_backtest` 18 times (once per `sweep_configs` output) means
  `_backtest_one_ticker` runs 18 times per ticker, and `resolve_entries`
  runs 18 times per candidate event — the full 18-pass cost the brief
  explicitly wants to avoid, not the "1 candidate pass + 18 exit passes"
  DESIGN §5.9 describes.

What actual reuse would take: splitting `_backtest_one_ticker` into (a) a
candidate/entry-resolution phase producing one entry-resolved event table
per ticker, independent of `config.exits`, and (b) an exit-resolution phase
that takes that table plus one `ExitParams` and calls
`resolve_exit_for_entry`/`path_metrics`/`enrich_context` for it — then a
sweep driver would run (a) once per ticker and (b) 18 times against the
cached result. That's a restructuring of `_backtest_one_ticker` and
probably `run_backtest`'s call signature, not something `sweep_configs`
itself can provide, and it's out of scope for this task per the brief
("You do NOT execute a sweep or a backtest").

I did not write a call-counter test that exercises `run_backtest` across
the 18 configs and asserts `resolve_entries`'s call count — that test would
currently fail (18x calls, not 1x), and inventing a fixture that made it
pass without the restructuring above would be exactly the kind of test
that "could not fail" the task brief warns against. Instead,
`TestSweepConfigsIsAPureGenerator` in the test file proves the narrower,
true claim: `sweep_configs` itself never touches `resolve_entries` or the
database, because it isn't running anything at all.

## Self-review

- Completeness: `sweep_configs` matches the pinned interface signature
  exactly; 18/18 distinct hashes verified by test, not just asserted by
  count.
- Naming: matches `CONSTRAINTS.md`'s name list (`sweep_configs`).
- YAGNI: no `SweepConfig` result wrapper, no CLI wiring (Task 11's CLI is
  out of scope; nothing here executes a sweep). `SweepParams` carries only
  the two fields the grid needs.
- Tests verify real behavior: hash distinctness is computed via the real
  `config_hash`, not inferred from field values; the "held at base value"
  claim is tested against a non-default base, not just the dataclass
  default, so the test can't pass by coincidence.
- Comment density matches the surrounding module's idiom (why, not just
  what, per CONSTRAINTS.md conventions).
- No wall-clock reads, no mutation of `base` (verified by test), module
  importable with no side effects (existing `if __name__ == "__main__":`
  guard untouched).

## Issues or concerns

- The entry-reuse optimization gap above is the main open item. Running
  the sweep today, once approved, costs 18 full `run_backtest` passes, not
  the "roughly 4-5 minutes total" DESIGN §5.9 describes for the
  1-candidate + 18-exit shape — actual wall-clock will be closer to 18x a
  single full pass until `_backtest_one_ticker` is restructured. The
  controller should decide whether that restructuring is scoped into a
  follow-up task before the sweep is run, or whether the 18-pass cost is
  accepted as-is.
- `docker-compose.yml` and `capitalscan/jobs/cli.py` had pre-existing
  uncommitted changes in the working tree from before this task started
  (visible in `git status` at session start). I left both untouched and
  did not stage or commit them — only `core/config.py`,
  `research/backtest.py`, and the new test file are in this commit.
