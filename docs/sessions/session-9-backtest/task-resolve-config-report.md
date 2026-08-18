# Task: wire `jobs.config.resolve_config` into the CLI

## What I implemented

Added one new helper in `capitalscan/jobs/cli.py`:

- `_CONFIG_FILE` — module-level constant, `<repo_root>/config.toml`, computed
  from `Path(__file__).resolve().parent.parent.parent`. Matches the existing
  `REPO_ROOT` pattern already used in `jobs/db.py`, `jobs/ingest.py`,
  `jobs/verify.py`.
- `_resolve_config_or_exit(cli_overrides=None)` — the one call site for
  `jobs.config.resolve_config`. Calls `resolve_config(cli_overrides=...,
  config_file=_CONFIG_FILE)`, catches `ConfigError`, prints
  `[red]error[/red]: config resolution failed — {exc}`, and raises
  `typer.Exit(code=1)` — same idiom every other error path in `cli.py` uses.

Wired it into five commands:

- `backtest` — `config = Config()` replaced with `config =
  _resolve_config_or_exit()`. Removed the now-unused `from
  capitalscan.core.config import Config` import.
- `indicators` — resolves config, passes `params=config.indicators` to
  `compute.run_indicators` (previously relied on `compute.py`'s own
  `params or IndicatorParams()` default).
- `universe` — passes `up=config.universe` to `compute.run_universe`.
- `events` — passes `sp=config.signals` to `compute.run_events`.
- `poll` — passes `sp=config.signals, ep=config.exits, stats=config.stats`
  to `poll_job.run_poll`.

## Which commands I wired, and why

**Scope: every CLI command that already had an optional config-dataclass
parameter on the `jobs`/`research` function it calls, where that parameter
was previously left to default inside the job function.** That's
`backtest`, `indicators`, `universe`, `events`, `poll` — five commands, all
in `cli.py`.

Reasoning:

- These are exactly the places where a `config.toml`/`CAPSCAN_*` value is
  currently silently ignored — the job function accepts a config section
  but the CLI never supplies one, so the ADR 091 chain never runs.
- Wiring them costs one `_resolve_config_or_exit()` call plus threading an
  existing keyword argument that's already part of each function's public
  signature — no signature changes to `jobs/compute.py`, `jobs/poll.py`, or
  `research/backtest.py` were needed.
- `jobs/compute.py` is explicitly out of scope this session (a concurrent
  agent is reviewing it), so I did not touch it. All five wirings only
  change what `cli.py` passes in, not the callee.

**Explicitly left out of scope**, with reasoning:

- `verify_indicators` (`jobs/verify.py`) — `verify.run()` builds
  `IndicatorParams()` internally with no parameter to override it at all.
  It's a manual golden-reference tool (writes
  `tests/golden/external_reference.csv`) for hand-checking indicator math
  against an external source, not a production run whose output carries a
  `config_hash`. Wiring it would mean adding a new parameter to
  `verify.run`'s signature — a real signature change to a module outside
  this task's stated scope ("connecting the existing resolver ... do not
  rewrite the resolver itself"), and its risk profile (silently changing a
  reviewer's golden-file comparison) argued for leaving it alone and
  reporting it here instead of guessing.
- `jobs/ingest.py`'s several `SplitParams()` construction sites
  (`run_calendar`'s date bound, `drop_pre_window_tickers`, the missing-bar
  reject-window check) — none of the callers of these `ingest.py` functions
  pass a `SplitParams` today; wiring them would require adding new
  parameters through `ingest.py`'s public functions, which is a larger,
  independent change than "connect an existing pass-through," and none of
  the CLI commands that call into `ingest.py` currently expose any config
  surface at all. Flagging this as a gap rather than guessing at scope.
- `calendar`, `tickers`, `bars`, `actions`, `market`, `shares`, `earnings`,
  `scan`, `weekly`, `monthly`, `positions *`, `db *` — none of these
  construct or receive a `core.config` dataclass anywhere in their call
  chain. Nothing to wire.

**CORRECTION (post-review):** the line above originally also listed
`nightly` as "nothing to wire." That was wrong — `nightly` calls
`compute.run_indicators(...)` and `compute.run_events(...)`, the same two
functions the `events`/`indicators` commands wire, and it called them
without `params`/`sp`, so it silently defaulted every time the Task
Scheduler ran it. See "Fix (post-review)" below for the correction — this
paragraph is left in place, corrected in-line, rather than deleted, so it
isn't read out of context as still true.

## `config.toml`'s CWD-relative default path

`resolve_config`'s own default (`config_file="config.toml"`) reads whatever
the process's current working directory happens to be — exactly the
`test_jobs_config.py` module docstring's warning: "a resolver reading
ambient CWD makes the suite depend on the machine it runs on."

The CLI pins `_CONFIG_FILE` to the repo root instead of inheriting CWD, and
does **not** expose a `--config-file` flag. Reasoning: `cscan` is meant to
behave identically no matter which directory the user happened to run it
from — a user running `cscan events` from `capitalscan/` and another
running it from the repo root should resolve the *same* config, not two
different ones (or one silently getting the "no file" default because
`config.toml` isn't in their CWD). A `--config-file` override would add
surface area and a second way to point at config without a concrete
requirement asking for it (YAGNI); the repo-root pin is the minimal correct
default and can be extended later if a real multi-config-file need shows
up.

## The observed `config_hash(Config())` value

Confirmed via direct check and via the pinned regression test
(`test_default_config_hash_is_pinned` in the new test file):

```
$ uv run python -c "from capitalscan.core.config import Config; from capitalscan.jobs.config import config_hash; print(config_hash(Config()))"
22df3117b890793b
```

Matches the required value exactly. No `config.toml` exists at the repo
root (`Test-Path .\config.toml` → `False`) and no `CAPSCAN_*` env vars are
set in this environment, so `resolve_config()` returns pure dataclass
defaults — identical to the `Config()` call it replaced.

## How a `ConfigError` surfaces to the user

`_resolve_config_or_exit` catches `ConfigError` and does:

```python
console.print(f"[red]error[/red]: config resolution failed — {exc}")
raise typer.Exit(code=1) from None
```

`from None` suppresses the exception chain in the traceback typer would
otherwise print if `--show-locals`/debug tooling were involved, matching
the "clean message and non-zero exit, never a traceback" requirement.
Verified directly:

```
$ CAPSCAN_INDICATORS="{bad json" uv run cscan indicators --tickers AAPL
error: config resolution failed — CAPSCAN_INDICATORS is not valid JSON: ...
(exit code 1, no traceback)
```
(exercised via the test suite's `CliRunner`, not a live shell invocation —
see Safety notes below.)

## TDD Evidence

**RED** — wrote `capitalscan/tests/unit/test_cli_config_resolution.py`
first, ran before any implementation:

```
$ uv run pytest capitalscan/tests/unit/test_cli_config_resolution.py -q
...
E   AttributeError: <module 'capitalscan.jobs.cli' ...> has no attribute '_CONFIG_FILE'
...
14 errors in 0.69s
```

All 14 new tests failed at fixture setup for the right reason: `_CONFIG_FILE`
didn't exist yet, i.e. nothing was implemented.

**GREEN** — after implementing `_CONFIG_FILE`, `_resolve_config_or_exit`,
and wiring the five commands:

```
$ uv run pytest capitalscan/tests/unit/test_cli_config_resolution.py -q
14 passed in 0.24s
```

One iteration in between: `test_resolve_config_or_exit_is_pinned_to_repo_root_not_cwd`
initially failed because it read `cli._CONFIG_FILE` *after* the autouse
`clean_env` fixture had already monkeypatched it to a tmp path for test
isolation — fixed by capturing the real value (`_REAL_CONFIG_FILE =
cli._CONFIG_FILE`) at module import time, before any fixture runs, same
pattern `test_backtest_cli.py` uses for `_REAL_PRIOR_CLEAN_CHECK` /
`_REAL_LOAD_BARS_BY_TICKER`.

**Full unit + property suite**, run per CONSTRAINTS.md's only safe form:

```
$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
712 passed in 26.44s
```

## Files changed

- `capitalscan/jobs/cli.py` — added `_CONFIG_FILE`, `_resolve_config_or_exit`;
  wired `backtest`, `indicators`, `universe`, `events`, `poll`. +51/-6 lines.
- `capitalscan/tests/unit/test_cli_config_resolution.py` — new, 14 tests.

`docker-compose.yml` was already modified in the working tree before I
started (unrelated `max_connections=200` change, not part of this task) —
left untouched and unstaged.

## Whether any existing test changed behavior

None. `test_backtest_cli.py`'s `DEFAULT_CONFIG = Config()` /
`DEFAULT_HASH = config_hash(DEFAULT_CONFIG)` module-level constants, and
its assertions like `captured["config"] == DEFAULT_CONFIG`, still pass
because `_resolve_config_or_exit()` with no `config.toml` and no
`CAPSCAN_*` vars resolves to exactly the same dataclass defaults `Config()`
produces. `test_jobs_config.py`'s 29 tests are untouched and still pass
(resolver itself was not modified). Full suite: 712 passed, 0 failed, 0
modified.

## Self-review findings

- **Completeness**: every command that had a config parameter to thread
  now threads a resolved one. Two genuine gaps identified and reported
  above (`verify.py`, `ingest.py`'s `SplitParams()` sites) rather than
  silently left unaddressed.
- **Naming**: `_resolve_config_or_exit` mirrors the existing
  `_resolve_tickers` naming and "resolve-or-exit-cleanly" shape already
  established in this file.
- **YAGNI**: no `--config-file` flag added (see CWD section above) — no
  requirement asked for one, and the repo-root pin satisfies the stated
  goal (determinism regardless of invocation directory).
- **Do tests verify real behavior?**: yes — each wiring test writes a real
  `config.toml` to a tmp path, monkeypatches `cli._CONFIG_FILE` to point at
  it, invokes the actual Typer command via `CliRunner` (not a raw function
  call, except for `backtest` which the existing `test_backtest_cli.py`
  precedent calls directly), and asserts the *downstream* job function
  received the overridden value — not just that `_resolve_config_or_exit`
  works in isolation.
- **Pristine output**: `ConfigError` messages verified free of "Traceback"
  in captured stdout across both the helper-level and command-level tests.

## Issues or concerns

- `jobs/ingest.py`'s three `SplitParams()` construction sites and
  `verify.py`'s `IndicatorParams()` remain unwired — flagged above as
  deliberate scope decisions, not oversights. If the controller wants these
  covered too, they need new parameters added to `ingest.py`/`verify.py`
  function signatures first (out of "connect the existing resolver" scope
  as I read it).
- No other concerns. `config_hash(Config())` confirmed unchanged.

---

## Fix report (post-review)

Review found two Important findings. Both addressed.

### Finding 1 — `nightly` was unwired; original report's justification was wrong

**Confirmed correct.** `nightly` (`cli.py`) calls `compute.run_indicators(tickers,
start, end, max_workers=1, engine=engine)` and `compute.run_events(tickers,
start, end, engine=engine)` — neither passed `params=`/`sp=`, so both
silently defaulted every time the Task Scheduler ran the nightly chain,
while the standalone `cscan indicators` / `cscan events` commands (wired in
the original pass) resolved a user's `config.toml`. Same job function, two
different effective configs depending on which entry point ran it.

**Fix applied:**

```python
config = _resolve_config_or_exit()   # first line of nightly(), before
                                      # db_io.get_engine() or any IO —
                                      # a bad config now aborts the whole
                                      # chain up front instead of after
                                      # bars/actions/market/shares/earnings
                                      # have already run
...
compute.run_indicators(tickers, start, end, params=config.indicators, max_workers=1, engine=engine)
compute.run_events(tickers, start, end, sp=config.signals, engine=engine)
```

Placed the resolve call before `db_io.get_engine()`/`scheduled_runs.record`
rather than just before the two `compute.*` calls, so a malformed config
fails fast instead of after five other IO steps have already run against
real data — verified by `test_nightly_command_malformed_config_exits_before_any_io`,
which does **not** stub `db_io.get_engine` (it makes it raise
`AssertionError("IO reached")` if called) and asserts the `ConfigError`
path wins before that assertion would ever fire.

**`weekly` and `monthly` checked, not omission:** both call only
`scheduled_runs.record(engine, "...")` and a `console.print` — no
`compute.*`/`poll.*`/`research.*` call, no config dataclass anywhere in
either function. Confirmed by reading both function bodies in full;
nothing to wire. (This matches what the original report said about them,
which was correct — only the `nightly` line was wrong.)

**Corrected in place:** the original "Which commands I wired" section's
bullet claiming `nightly` had nothing to wire is left visible but annotated
with a correction, rather than silently edited, so anyone who already read
the original text doesn't carry the wrong claim forward.

New test: `test_nightly_command_threads_resolved_config` — writes a real
`config.toml` with `bb_window = 25` / `stoch_oversold = 25.0`, calls
`cli.nightly()` directly (stubbing every IO step except the two `compute.*`
calls under test, same pattern `test_backtest_cli.py` uses for `backtest`),
and asserts the *captured* `params`/`sp` arguments carry those values —
not merely that `resolve_config` ran.

### Finding 2 — wiring `poll` made it fail-closed on unrelated config sections

**Confirmed and accepted as correct behavior**, per the review's own framing:
`jobs/config.py::_read_env` loops over all seven `SECTIONS` unconditionally,
so a malformed `CAPSCAN_COSTS` or `CAPSCAN_UNIVERSE` — sections `poll`
never reads — now blocks `poll` from starting, where previously `poll`
never called `resolve_config` at all and was immune to any env state. This
is `resolve_config`'s existing all-or-nothing contract (ADR 091: raise
rather than default), not something to special-case around.

**Fix applied:**

1. Documented directly in `poll`'s docstring (`cli.py`) — quoted here in
   full since this is the one place an operator debugging a poller that
   won't start at market open would look first:

   > Resolves config via `_resolve_config_or_exit`, which reads and
   > validates all seven config sections at once
   > (`jobs.config.resolve_config`'s all-or-nothing contract) even though
   > `poll` only consumes `signals`/`exits`/`stats`. A malformed
   > `CAPSCAN_COSTS` or `CAPSCAN_UNIVERSE` — sections this command never
   > touches — will still block startup with a `ConfigError`. This is
   > deliberate (ADR 091: raise rather than default), not a
   > `poll`-specific bug, but it means a poller refusing to start at
   > market open can be caused by an env var this command doesn't
   > otherwise care about.

2. New test `test_poll_command_blocks_on_malformed_unrelated_section` —
   sets `CAPSCAN_COSTS="{bad json"` (a section `poll` never consumes),
   invokes `cscan poll --tickers AAPL` through `CliRunner`, and asserts
   exit code 1, no traceback in output, and `CAPSCAN_COSTS` named in the
   error message. This pins the behavior as deliberate rather than
   incidental, per the review's ask.

### Minors addressed (cheap, so done)

- `test_malformed_config_toml_exits_clean` — a syntactically broken
  `config.toml` (`[indicators\nbb_window = 25\n`, missing closing bracket)
  routed through `cli._resolve_config_or_exit` exits clean, no traceback.
- `test_unknown_section_in_cli_overrides_exits_clean` — an override naming
  an unknown section (`{"indicatorz": {...}}`) through
  `cli._resolve_config_or_exit` exits 1 and names the bad section
  (`"indicatorz"`) in the output.

### Re-verification

`config_hash(Config())` observed again after all fixes:

```
$ uv run python -c "from capitalscan.core.config import Config; from capitalscan.jobs.config import config_hash; print(config_hash(Config()))"
22df3117b890793b
```

Unchanged — matches the required value.

```
$ uv run pytest capitalscan/tests/unit/test_cli_config_resolution.py -v
19 passed in 0.27s

$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
717 passed in 27.29s
```

(717 = the 712 from the original pass + 5 net new tests: `nightly` × 2,
`poll` fail-closed × 1, and the two "minors" tests × 2 = 5.)

No existing test was edited or weakened. `docker-compose.yml` remains an
unrelated pre-existing uncommitted change (`max_connections=200`), still
left unstaged.

### Files changed (this fix pass)

- `capitalscan/jobs/cli.py` — `nightly` now resolves config before any IO
  and threads `params=config.indicators` / `sp=config.signals` into its two
  `compute.*` calls; `poll`'s docstring documents the fail-closed behavior
  on unrelated sections.
- `capitalscan/tests/unit/test_cli_config_resolution.py` — 5 new tests
  (19 total in the file).
- This report — corrected the `nightly` claim in place, appended this fix
  section.

---

## Fix report (second round) — `scheduled_runs.record` ordering vs. config resolution

Re-review found an ordering consequence in the `nightly` fix above: placing
`config = _resolve_config_or_exit()` before `scheduled_runs.record(engine,
"nightly")` meant a malformed config produced a `nightly` attempt that was
never recorded at all. `scheduled_runs.record` is what `cscan status` reads
to surface Task Scheduler catch-up delays (ADR 080); with the ordering as
first fixed, "the scheduler never fired" and "nightly fired and died on a
bad config" were both simply absent from `scheduled_runs` — indistinguishable
symptoms of two different problems.

**Verified before reordering, as asked:** read `scheduled_runs.record`
(`capitalscan/jobs/scheduled_runs.py:47-72`) in full. Its signature is
`record(engine, job, run_id=None, now=None)` — it takes no config
parameter, computes `scheduled_for`/`delay_seconds` from `job` and a
wall-clock timestamp only, and does one `db_io.upsert` into
`scheduled_runs`. Nothing in it reads or depends on `core.config` in any
way, so reordering it ahead of config resolution cannot have it act on or
write anything a malformed config would have shaped. Reorder confirmed
safe.

**Fix applied** — `db_io.get_engine()` → `scheduled_runs.record(engine,
"nightly")` → `config = _resolve_config_or_exit()`, in that order, still
ahead of every `ingest.run_*`/`compute.run_*` call:

```python
engine = db_io.get_engine()
# Recorded before config resolution, not after: scheduled_runs.record
# takes no config and does one cheap upsert (ADR 080)...
scheduled_runs.record(engine, "nightly")
config = _resolve_config_or_exit()
tickers = _resolve_tickers(None)
...
```

This keeps the property the original fix was for — a bad config still
aborts before any `ingest.run_*`/`compute.run_*` call, so no partial
pipeline — while restoring the attempted-run record ADR 080 depends on.

**Test updated:** `test_nightly_command_malformed_config_exits_before_any_io`
renamed to `test_nightly_command_malformed_config_exits_before_any_ingest_or_compute_io`
and rewritten: `db_io.get_engine` now returns a fake engine (it must
succeed, since `scheduled_runs.record` needs it and runs first) instead of
raising if called; `scheduled_runs.record` is stubbed to a recording spy
instead of left real; and every `ingest.run_*` step is stubbed to raise
`AssertionError` if reached. The test asserts both halves of "no partial
pipeline, not no writes at all": `record_calls == [("fake-engine",
"nightly")]` (the one row that must be written) and that none of the six
`ingest.run_*` stubs fired (would have raised `AssertionError`, which would
fail the test) before the malformed `CAPSCAN_INDICATORS` env var surfaces
as `typer.Exit(1)`.

### Re-verification

```
$ uv run python -c "from capitalscan.core.config import Config; from capitalscan.jobs.config import config_hash; print(config_hash(Config()))"
22df3117b890793b
```

Unchanged.

```
$ uv run pytest capitalscan/tests/unit/test_cli_config_resolution.py -v
19 passed in 0.32s

$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
717 passed in 27.02s
```

Test count unchanged at 19/717 — this round renamed and rewrote one
existing test rather than adding a new one. No other existing test edited.

### Files changed (this round)

- `capitalscan/jobs/cli.py` — reordered `nightly`: `scheduled_runs.record`
  now precedes `_resolve_config_or_exit()`, both still ahead of all
  ingest/compute IO.
- `capitalscan/tests/unit/test_cli_config_resolution.py` — renamed and
  rewrote `test_nightly_command_malformed_config_exits_before_any_io` to
  assert `scheduled_runs.record` fires while no ingest/compute job does.
- This report — appended this section.
