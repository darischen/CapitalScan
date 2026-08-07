# Final review fix report: config threading + sort determinism

## What was implemented

### Finding 1 (Important) — `run_events` now threads the full resolved `Config`

`capitalscan/jobs/compute.py`:

- `run_events(tickers, target_start, target_end, engine=None, sp=None, config=None)`
  gained a `config: Config | None` parameter.
  - If `config` is passed, it is used directly as the resolved config for
    both `config_hash` and `split_key` (via `resolved_config.splits`).
  - If `config` is `None`, behavior is unchanged from before: `sp or
    SignalParams()` is wrapped in `Config(signals=sp)`, everything else
    defaulted — this is the exact pre-fix computation, preserved for
    backward compatibility.
  - If both `sp` and `config` are passed and `sp != config.signals`, `run_events`
    raises `ValueError` rather than silently picking one — ambiguous intent
    should fail loudly, not guess.
- `_build_event_row` gained an optional `splits: SplitParams | None = None`
  parameter, defaulting to `SplitParams()` when omitted (keeps direct
  callers/tests of `_build_event_row` that don't pass `splits` unchanged).
  `run_events` now passes `resolved_config.splits` through.

`capitalscan/jobs/cli.py`: both call sites (`events` command, `nightly`
command) changed from `sp=config.signals` to `config=config`.

### Finding 2 (Minor) — sort key gains `signal_type`

`capitalscan/research/backtest.py`, `run_backtest`'s final sort:

```python
events = events.sort_values(
    ["ticker", "signal_date", "signal_type", "entry_kind"]
).reset_index(drop=True)
```

Chose adding the column over `kind="stable"` — it names the actual
tiebreaker explicitly in the code, rather than leaning on sort-algorithm
behavior to compensate for an underspecified key. A future reader sees
exactly which columns fully determine row order.

## Design choice: full `Config`, not `SplitParams`-only

**Chose full `Config`.** Reasons:

1. It matches `run_backtest`'s existing shape exactly — `config_hash(config)`
   and (via `enrich_context`) `split_key_for(signal_date, config.splits)`
   both come off the identical object already. Threading the same shape
   through `run_events` means both writers are provably looking at the same
   data, not two objects that happen to agree today.
2. A `SplitParams`-only fix would have repaired the split-key drift (finding
   1's first symptom) but left the `config_hash` divergence untouched — and
   the task description is explicit that the hash divergence is "worse,"
   since it breaks the upsert join. Only threading the full `Config` fixes
   both symptoms from one root cause.
3. It costs nothing extra at the call sites: both CLI callers (`events`,
   `nightly`) already hold the full resolved `config` in scope; passing
   `config=config` instead of `sp=config.signals` is strictly simpler than
   picking apart `config.splits` before the call.

## TDD Evidence

**RED** — new tests written first, run against the unmodified code:

```
uv run pytest capitalscan/tests/unit/test_events_backtest_config_agreement.py capitalscan/tests/unit/test_backtest_worker.py -q
```

```
capitalscan\tests\unit\test_events_backtest_config_agreement.py FF...FF  [ 20%]
capitalscan\tests\unit\test_backtest_worker.py ..................F...... [ 91%]
...                                                                      [100%]
FAILED ...TestRunEventsThreadsFullConfig::test_split_key_uses_the_passed_config_not_a_hardcoded_default
  TypeError: run_events() got an unexpected keyword argument 'config'
FAILED ...TestRunEventsThreadsFullConfig::test_config_hash_uses_the_full_passed_config_not_just_signals
  TypeError: run_events() got an unexpected keyword argument 'config'
FAILED ...TestRunEventsBackwardCompatibility::test_sp_and_config_disagreeing_raises_rather_than_silently_picking_one
  TypeError: run_events() got an unexpected keyword argument 'config'
FAILED ...TestRunEventsAndRunBacktestAgreeOnTheSameConfig::test_config_hash_and_split_key_agree_across_both_jobs
  TypeError: run_events() got an unexpected keyword argument 'config'
FAILED ...TestRunBacktestDispatchAndWrite::test_sort_key_includes_signal_type_so_same_day_long_and_short_signals_are_deterministic
  AssertionError: assert ['stoch_overbought', 'bb_lower_touch'] == ['bb_lower_touch', 'stoch_overbought']
5 failed, 30 passed in 1.22s
```

All five failures are for the right reason: `run_events` didn't yet accept
`config`, and the sort test's fixture deliberately returns rows in the
*wrong* alphabetical order so a passing assertion proves `signal_type`
actually drove the sort rather than passing by coincidence.

**GREEN** — after implementation:

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
```

```
725 passed in 26.96s
```

## The test proving `run_events` and `run_backtest` agree

`capitalscan/tests/unit/test_events_backtest_config_agreement.py`,
`TestRunEventsAndRunBacktestAgreeOnTheSameConfig::test_config_hash_and_split_key_agree_across_both_jobs`.

This test builds one shared fixture ticker/signal that fires the identical
`bb_lower_touch` (long) signal on the same `signal_date` (2026-07-30) in
both `run_events`'s one-bar fixture and `run_backtest`'s 25-bar fixture, then:

1. Calls `compute.run_events([TICKER], SIGNAL_DATE, SIGNAL_DATE,
   engine=_FakeEngine(), config=_OVERRIDE_CONFIG)` with `_read_*` stubbed.
2. Calls `backtest.run_backtest([TICKER], _OVERRIDE_CONFIG, "run-1",
   engine=_FakeEngine())` with its own `_read_*` stubbed.
3. Captures both jobs' `db_io.upsert` calls off the SAME `db_io.upsert`
   patch point (both modules `from capitalscan.jobs import db_io` — it's the
   identical module object, not two copies — documented in the
   `captured_events_upsert` fixture's docstring so this isn't accidentally
   re-broken into two patches that clobber each other).
4. Asserts the row `run_events` wrote and the `touch`-entry-kind row
   `run_backtest` wrote for the same signal_date have equal `config_hash`
   and equal `split_key`, AND that both equal the value computed directly
   from `jobs.config.config_hash(_OVERRIDE_CONFIG)` / `"train"` (the
   expected split label under the overridden `SplitParams`).

`_OVERRIDE_CONFIG` uses `SplitParams(train_end="2026-12-31",
validate_end="2027-12-31")` — under the OLD hardcoded-default behavior,
2026-07-30 would have labelled `"holdout"` (default `validate_end` is
2023-12-31); under the fix it labels `"train"`. This directly distinguishes
"reads the passed config" from "reads a default that happens to match."

## The observed `config_hash(Config())` value

```
uv run python -c "from capitalscan.core.config import Config; from capitalscan.jobs.config import config_hash; print(config_hash(Config()))"
22df3117b890793b
```

Unchanged from the value stated in the task. Also asserted directly in
`TestRunEventsBackwardCompatibility::test_documented_default_hash_is_unchanged`.

## Files changed

- `capitalscan/jobs/compute.py` — `run_events` and `_build_event_row` (Finding 1)
- `capitalscan/jobs/cli.py` — `events` and `nightly` commands now pass `config=config`
- `capitalscan/research/backtest.py` — sort key gains `signal_type` (Finding 2)
- `capitalscan/tests/unit/test_events_backtest_config_agreement.py` — new file,
  Finding 1 coverage (RED/GREEN evidence above)
- `capitalscan/tests/unit/test_backtest_worker.py` — new test for Finding 2
- `capitalscan/tests/unit/test_cli_config_resolution.py` — updated the two
  existing CLI tests whose fake `run_events` signature needed a `config`
  kwarg to match the new call sites (`test_events_command_threads_resolved_params`,
  `test_nightly_command_threads_resolved_config`)

## How backward compatibility was preserved

- `sp` remains a valid, working parameter on its own. Every existing caller
  that only knows about `sp` (`capitalscan/tests/integration/test_compute.py`,
  `capitalscan/tests/unit/test_run_events_column_scope.py`, which pass
  neither `sp` nor `config`) is unaffected — `resolved_config = Config(signals=sp
  or SignalParams())` reproduces the exact pre-fix computation byte for byte.
  `TestRunEventsBackwardCompatibility` in the new test file covers both the
  "no args at all" and "`sp`-only" paths and asserts the resulting
  `config_hash` still matches `config_hash(Config())` (`"22df3117b890793b"`,
  no override) or `config_hash(Config(signals=sp))` (`sp` override, matching
  the old formula) respectively.
- `_build_event_row`'s new `splits` parameter defaults to `None` (resolved
  internally to `SplitParams()`), so `test_run_events_column_scope.py`'s
  direct call to `_build_event_row(...)` without `splits` is untouched.
- The two CLI call sites that now pass `config=config` instead of
  `sp=config.signals` are a deliberate, intentional behavior widening (the
  entire point of the fix) — not a silent behavior change for a caller that
  passes no override, since `config_hash(Config())` is unchanged and no
  `config.toml`/`CAPSCAN_*` is set in this repo today.

## Self-review

- **Completeness**: both findings addressed; the `sp`/`config` conflict case
  raises rather than guessing, closing an ambiguity the task didn't
  explicitly ask for but that the "no caller may silently get different
  behavior" constraint implies.
- **Naming**: `resolved_config` reads clearly next to the now-shadowed `sp`
  reassignment (`sp = resolved_config.signals`) — considered avoiding the
  reassignment but the rest of the function body already refers to `sp`
  throughout (`core_signals.detect(bar, prior_ind, sp)`), so keeping that
  name intact was less invasive than renaming every use site.
- **YAGNI**: did not touch the `ExitParams().max_hold_days` hardcoded
  default at the `_tag_clusters` call site (line ~872 of `compute.py`),
  even though it is a same-shaped issue (cluster columns computed off a
  default `ExitParams()` regardless of `config.exits`). It's out of scope:
  the finding names only `split_key`/`config_hash`, and `_RUN_EVENTS_UPDATE_COLUMNS`
  already deliberately excludes the cluster columns (Ruling C5) — the
  backtest overwrites them with the real, config-derived values on its own
  pass, so `run_events`'s cluster tagging never actually reaches a live
  `events` row unless the backtest never runs on it. Flagging it here for
  visibility, not fixing it in this pass.
- **Do the tests verify real behavior**: the cross-module agreement test
  exercises both `run_events` and `run_backtest` through their public entry
  points with real fixture data producing a real signal, not two isolated
  unit calls to `config_hash`/`split_key_for` that would pass even if
  neither job actually used them. The Finding 2 sort test constructs input
  in deliberately-wrong order so a pass can't be explained by input order
  alone.
- **Pristine output**: ran the full safe suite (`unit` + `property`) once
  clean at the end — 725 passed, 0 failed, 0 skipped.

## Issues or concerns

- None blocking. The `ExitParams()` default noted above under YAGNI is a
  pre-existing, separate smell worth a future look but is not part of
  either finding and touching it risks the cluster-column ownership
  invariant (Ruling C5) for no benefit in this pass.
- `docker-compose.yml` and `capitalscan/jobs/cli.py` both showed as already
  modified in `git status` before this session started (per the task's own
  git status snippet). I verified `cli.py`'s diff contains only my two
  intended one-line changes (`git diff capitalscan/jobs/cli.py` — confirmed
  clean). `docker-compose.yml` was never touched by me and is left as-is,
  uncommitted, since it's unrelated to either finding.
