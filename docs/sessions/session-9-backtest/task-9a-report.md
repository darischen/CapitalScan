# Task 9a report — column-scoped upsert + `_in_trade` consolidation

HEAD before this work: `6e05887`. Branch: `session-9-backtest`.

## What I implemented

### Change 1 — column-scoped upsert (Ruling C4)

`capitalscan/jobs/db_io.py`: `upsert()` gains an optional `update_columns: list[str] | None = None`
parameter.

- `update_columns=None` (the default, and every existing caller's behavior) builds `DO UPDATE SET`
  from every table column not in `conflict_cols` — the exact same dict-comprehension the function
  always ran, just moved into an `else` branch. No existing caller passes the new argument, so
  `run_indicators`, `run_universe`, and every ingest job are untouched.
- `update_columns=[...]` narrows `DO UPDATE SET` to exactly that list.
- Before building any statement, the function validates `update_columns` against the table's real
  column names and against `conflict_cols`. Any name that is not a real column, or that is itself
  a conflict column, raises `ValueError` naming the offending entries. This happens once, before
  the batch loop, so a bad name fails before any row is sent. An empty `data` list still
  short-circuits to `return 0` before that validation runs (matches the pre-existing empty-write
  no-op, and there is nothing to validate against if nothing is being written).

`capitalscan/jobs/compute.py`: `run_events`'s `db_io.upsert(...)` call for the `events` table now
passes `update_columns=_RUN_EVENTS_UPDATE_COLUMNS`. Two new module-level constants, placed directly
above `_build_event_row` (which they document):

```python
_CLUSTER_COLUMNS = ["cluster_id", "seq_in_cluster", "is_cluster_head", "days_since_head"]

_RUN_EVENTS_UPDATE_COLUMNS = [
    "run_id", "signal_types_all", "signal_strength", "side", "touch_level",
    "bb_pctb", "bb_width_pct", "k_full", "d_full", "k_fast", "k_cross_up",
    "k_cross_down", "atr_14", "rv_pct_252d", "dd_52w", "sma200_slope_60",
    "above_sma200", "vol_z_20d", "days_to_earnings", "vix_close", "spx_ret_1d",
    "dd_bucket", "entry_date", "entry_price", "entry_gapped", "split_key",
]
```

The module docstring's "known gap" paragraph is updated to say the gap is closed and to point at
`_RUN_EVENTS_UPDATE_COLUMNS`.

### Change 2 — consolidated `_in_trade`

New home: `capitalscan/core/universe.py`, as a public function `in_trade(universe_flags, ticker,
signal_date) -> bool`. Semantics are byte-identical to both prior copies (fail-open `True` when no
universe evaluation exists on or before `signal_date`, otherwise the most recent evaluation's
`in_trade`).

- `capitalscan/jobs/compute.py`: the private `_in_trade` def is deleted; the one call site
  (`run_events`, the "Step 3" universe check) now calls `core_universe.in_trade(...)` — `compute.py`
  already imported `core.universe as core_universe` for `evaluate_criteria`/`is_tradeable`, so no
  new import was needed.
- `capitalscan/research/candidates.py`: the private `_in_trade` def is deleted; added
  `from capitalscan.core import universe as core_universe`; the call site in `apply_eligibility`
  now calls `core_universe.in_trade(...)`.

## Where `_in_trade` now lives, and why

`core/universe.py`. Both `jobs/compute.py` and `research/candidates.py` need it, and neither may
reach into the other's private namespace (that was the whole reason Task 3 duplicated it instead
of importing across module boundaries). `core/` performs no IO (invariant 1), but this function
doesn't perform any either — `universe_flags` arrives as an already-loaded `DataFrame`; the
function only filters and reduces it. `core/universe.py` already holds exactly this shape of
function (`evaluate_criteria`, `is_tradeable` — pure functions over already-loaded rows/frames), so
this is not a new pattern for that module, just one more function of the same kind. Both callers
already imported `core.universe` (compute.py did; candidates.py now does), so the change is a
straight delegate-and-delete, not a new dependency edge.

## TDD Evidence

### RED — `db_io.upsert`

Command: `uv run pytest capitalscan/tests/unit/test_db_io_upsert.py -q`

Before implementing `update_columns`, 5 of 6 tests failed with
`TypeError: upsert() got an unexpected keyword argument 'update_columns'` — the expected failure,
since the parameter didn't exist yet:

```
FAILED ...::test_run_events_shaped_write_does_not_null_exit_columns
FAILED ...::test_backtest_shaped_write_does_not_null_signal_columns
FAILED ...::test_unknown_column_name_raises
FAILED ...::test_conflict_column_in_update_columns_raises
FAILED ...::test_empty_data_short_circuits_before_validation
5 failed, 1 passed in 0.51s
```

(The 6th, `test_update_columns_none_is_byte_identical_to_today`, passed trivially since it doesn't
pass the new kwarg — expected, it's pinning pre-existing behavior.)

After adding the `update_columns` parameter but before fixing the test's own assertion scope (see
below), 2 tests still failed for a different, informative reason — the SET-clause assertions were
matching column names that also appear in the INSERT's column list/VALUES clause (which always
includes every row-dict key regardless of `update_columns` — that's correct; only the SET clause is
scoped). Fixed by slicing the compiled SQL to only the text after `DO UPDATE SET`.

### GREEN — `db_io.upsert`

Command: `uv run pytest capitalscan/tests/unit/test_db_io_upsert.py -q`

```
capitalscan\tests\unit\test_db_io_upsert.py ......
6 passed in 0.05s
```

### RED — `run_events` column scope + `_in_trade` consolidation

Command: `uv run pytest capitalscan/tests/unit/test_run_events_column_scope.py -q`

Before wiring `update_columns` into `run_events`'s upsert call: not run separately as its own RED
(the db_io RED above already covers the primitive) — but running the full suite before the
`core_universe.in_trade` move produced, once the constants/call-site edit was in and the
consolidation edit was not yet made:

```
E   AttributeError: module 'capitalscan.core.universe' has no attribute 'in_trade'
```

at `compute.py:781` — confirming the test genuinely exercises the new `core_universe.in_trade` call
path, not a stub that happened to already exist. (Also caught, en route: my fixture's stub for
`_read_market_days` originally pre-indexed on `"ts"`, but `run_events` itself calls
`.set_index("ts")` on the return value — a `KeyError` that the fixture is now corrected for.)

### GREEN — `run_events` column scope + `_in_trade` consolidation

Command: `uv run pytest capitalscan/tests/unit/test_run_events_column_scope.py -q`

```
capitalscan\tests\unit\test_run_events_column_scope.py ...
3 passed in <1s (as part of full run below)
```

### Full required suite

Command: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`

```
598 passed in 23-25s
```

(Zero live-database commands were run at any point — see CONSTRAINTS.md's hard safety rules. No
`cscan` invocation, no bare `pytest`, nothing under `capitalscan/tests/integration/`.)

## Files changed

- `capitalscan/jobs/db_io.py` — `upsert()` gains `update_columns`.
- `capitalscan/jobs/compute.py` — module docstring updated; `_in_trade` deleted, replaced with
  `core_universe.in_trade` call; `_CLUSTER_COLUMNS`/`_RUN_EVENTS_UPDATE_COLUMNS` constants added;
  `run_events`'s `events` upsert call passes `update_columns=_RUN_EVENTS_UPDATE_COLUMNS`.
- `capitalscan/core/universe.py` — new public `in_trade()` function.
- `capitalscan/research/candidates.py` — `_in_trade` deleted, replaced with `core_universe.in_trade`
  call; new `core.universe` import.
- `capitalscan/tests/unit/test_db_io_upsert.py` — new. Six tests: both update-scope directions
  (signal-shaped vs. exit-shaped writes), `update_columns=None` parity, unknown-column-name raise,
  conflict-column-name raise, empty-data short-circuit.
- `capitalscan/tests/unit/test_run_events_column_scope.py` — new. Three tests: `run_events` passes
  `_RUN_EVENTS_UPDATE_COLUMNS` to `db_io.upsert`; that list excludes `_CLUSTER_COLUMNS`; that list
  matches exactly `_build_event_row`'s output keys minus the conflict-key columns.

Not touched by me: `capitalscan/jobs/cli.py` and `docker-compose.yml` show as modified in
`git status`, but those changes predate this task (present in the working tree before I started —
see the session's initial `gitStatus`). I left them alone and did not include them in my commit.

## The column-ownership split, stated explicitly

Conflict key (never an update target, since it's the `ON CONFLICT` index): `config_hash`, `ticker`,
`signal_date`, `signal_type`, `entry_kind`.

**Owned by `run_events` (signal side)** — `_RUN_EVENTS_UPDATE_COLUMNS`, 26 columns: `run_id`,
`signal_types_all`, `signal_strength`, `side`, `touch_level`, `bb_pctb`, `bb_width_pct`, `k_full`,
`d_full`, `k_fast`, `k_cross_up`, `k_cross_down`, `atr_14`, `rv_pct_252d`, `dd_52w`,
`sma200_slope_60`, `above_sma200`, `vol_z_20d`, `days_to_earnings`, `vix_close`, `spx_ret_1d`,
`dd_bucket`, `entry_date`, `entry_price`, `entry_gapped`, `split_key`. This is exactly the set of
keys `_build_event_row` returns, minus the five conflict-key columns. Verified by a test
(`test_owned_column_list_matches_build_event_row_minus_keys`) that calls `_build_event_row` with a
constructed hit and asserts set equality against `_RUN_EVENTS_UPDATE_COLUMNS`, so this list cannot
silently drift from what the function actually builds.

**Owned by the backtest (`research/backtest.py`, Task 9b)** — everything else on the `events` table
that isn't in the signal-side list above: the exit/return columns (exit price, exit reason, MFE/MAE,
realized return, holding days, cost-adjusted figures, etc. — Task 9b's own concern, not enumerated
here since that module doesn't exist yet) **plus, per Ruling C5, the four cluster columns**
(`cluster_id`, `seq_in_cluster`, `is_cluster_head`, `days_since_head`).

**Both own `run_id`** (Ruling C4) — each row records whichever run last touched it, regardless of
which job that was.

**Cluster columns are the one deliberate exception inside the "signal side."** `run_events` still
*computes and inserts* them on a brand-new row (via `_tag_clusters`, called just before the upsert —
unchanged), because a fresh INSERT with no cluster value at all would be wrong too. What changed is
that they are excluded from `_RUN_EVENTS_UPDATE_COLUMNS`, so on a conflict (an existing row), a
plain `run_events` re-run leaves whatever the backtest last wrote for those four columns alone. This
is the "intended consequence" flagged in the task brief, not treated as a side effect — documented
in the comment directly above `_CLUSTER_COLUMNS` in `compute.py`, and pinned by
`test_owned_column_list_excludes_the_backtest_owned_cluster_columns`.

**Tests proving both directions** (in `test_db_io_upsert.py`, using synthetic `signal_strength` /
`exit_price` / `realized_return` / `cluster_id` columns on a stand-in `events` table so the test
doesn't depend on Task 9b's not-yet-written schema):

- `test_run_events_shaped_write_does_not_null_exit_columns` — `update_columns=["run_id",
  "signal_strength"]` produces a SET clause containing `signal_strength = excluded.signal_strength`
  and `run_id = excluded.run_id`, and containing **none** of `exit_price`, `realized_return`,
  `cluster_id`.
- `test_backtest_shaped_write_does_not_null_signal_columns` — the mirror: `update_columns=["run_id",
  "exit_price", "realized_return"]` produces a SET clause containing the exit columns and `run_id`,
  and **not** `signal_strength` or `cluster_id`.

Plus the `run_events`-specific tests in `test_run_events_column_scope.py` verifying the real
constant (not a synthetic stand-in) is what actually reaches `db_io.upsert` from `run_events`.

## Unknown/conflict column name decision

Raises `ValueError`, validated once up front (before the batch loop, so nothing is written before
the check runs). Rationale, per the task brief's own framing: this codebase has a demonstrated
pattern of exactly this failure mode — a column silently dropped or silently NULL'd because nobody
caught a name mismatch (`is_tradeable` in `core/universe.py` already raises `KeyError` for an
unknown criteria name for the identical reason: "a typo in an ablation config fails loudly instead
of silently widening the universe"). Silently ignoring an unrecognized `update_columns` entry would
mean a typo in a future Task 9b column name produces a working-looking upsert that just quietly
never updates that column — the same class of silent-NULL bug `update_columns` exists to close,
one layer up. Raising immediately is also strictly cheaper to debug than a value that's `None`
forever in the database for no visible reason.

A conflict-column name (e.g. `ticker`) is treated as equally invalid and raises the same way, on
the reasoning that naming a key column as an update target is never a legitimate intent for this
function — it's either a copy-paste mistake or a misunderstanding of what `conflict_cols` already
covers, and Postgres would silently accept `ticker = excluded.ticker` as a no-op (since it must
already equal itself on a conflict), which would hide the mistake rather than surface it.

## Confirmation: `update_columns=None` is byte-identical to prior behavior

- Structurally: the `else` branch of `target_cols` (`[c.name for c in table.columns if c.name not
  in conflict_cols]`) is character-for-character the same expression the function used
  unconditionally before this change (previously `update_cols = {c.name: stmt.excluded[c.name] for
  c in table.columns if c.name not in conflict_cols}`; the loop variable name changed from an
  inline dict-comprehension to a two-step `target_cols` → `update_cols`, but the set of columns
  selected and the `EXCLUDED` mapping built from them are identical).
- Tested directly: `test_update_columns_none_is_byte_identical_to_today` calls `upsert()` with no
  `update_columns` argument and asserts the compiled SET clause contains `col = excluded.col` for
  every column on the stand-in table (`run_id`, `signal_strength`, `cluster_id`, `exit_price`,
  `realized_return`) — i.e. every non-key column, same as before.
- No existing caller (`run_indicators`, `run_universe`, every `ingest.py` job) was touched or needed
  to be, since none of them pass the new keyword argument.

## Self-review findings

- **Completeness**: both changes from the brief are done — `db_io.upsert`'s `update_columns`, wired
  into `run_events`; `_in_trade` consolidated into `core/universe.py` with both call sites updated.
- **Naming**: `in_trade` (public, no leading underscore) in `core/universe.py` matches that module's
  existing public-function convention (`evaluate_criteria`, `is_tradeable`, `adr_adjusted_shares`).
  `_RUN_EVENTS_UPDATE_COLUMNS`/`_CLUSTER_COLUMNS` follow `compute.py`'s existing
  underscore-prefixed-module-constant convention (`_SPECIFICITY`-style, `_SCAN_COLUMNS`,
  `INDICATOR_COLUMNS`).
- **YAGNI**: did not invent a `_RUN_BACKTEST_UPDATE_COLUMNS` constant — that belongs to Task 9b,
  which owns the actual exit-column schema; inventing a guess here would just be something for 9b
  to delete or contradict.
- **Real behavior, not mocks-all-the-way-down**: `test_db_io_upsert.py` builds a real (unattached)
  SQLAlchemy `Table` and compiles a real `INSERT ... ON CONFLICT` statement with the Postgres
  dialect, then asserts on the actual generated SQL text — not on an internal attribute or a
  trivially-true mock call assertion. `test_run_events_column_scope.py` runs the real `run_events`
  function end-to-end (with only IO stubbed), so it would fail if the call site were ever reverted
  or the constant renamed without updating the call.
- **Pristine output**: reformatted `db_io.py` with `ruff format` (whitespace-only line-length fix
  in the validation list comprehension); `ruff check` is clean on every file I touched. One
  pre-existing `ruff check`/`ruff format` finding in `candidates.py:409` (an unrelated line, inside
  `_trading_bars_between`, not part of this task's diff) was left alone — fixing it wasn't in scope
  and touching unrelated code isn't warranted here.

## Issues or concerns

None blocking. One thing worth flagging for whoever picks up Task 9b: when that task defines its
own `update_columns` list for the backtest's write to `events`, it must likewise exclude the four
`_CLUSTER_COLUMNS` from *its own* omission logic in the other direction — i.e. the backtest's
update list should *include* the cluster columns (it owns them), while `run_events`'s excludes
them. I did not add a cross-module constant or assertion enforcing that the two lists partition the
table's columns correctly, because Task 9b's schema doesn't exist yet and I did not want to
guess at or lock in its column names. Whoever implements 9b should re-read the `_CLUSTER_COLUMNS`
comment in `compute.py` before writing that list.

---

## Fix report — code review follow-up

Review came back with one Important finding and one Minor finding on `db_io.upsert`. Both fixed.

### Finding 1 (Important) — `update_columns=[]` was not validated

`update_columns=[]` (distinct from the `None` default) fell through the existing `invalid = [...]`
check vacuously — an empty list has no elements to be invalid — and reached
`on_conflict_do_update(set_={})`, which SQLAlchemy itself rejects with
`ValueError: set parameter dictionary must not be empty`. The reviewer confirmed this against a
standalone SQLAlchemy repro. Not the NULL-overwrite defect this feature exists to close (it fails
loudly, not silently), but an unowned, undocumented error path inside a function whose job is
controlled, descriptive validation.

**Fix chosen: reject it explicitly**, with the same kind of descriptive `ValueError` the function
already raises for unknown/conflict column names, rather than just documenting and pinning
SQLAlchemy's own error. Reasoning: an empty `update_columns` list means "update no columns at
all" — a no-op `DO UPDATE SET` on a natural-key table, which is never a deliberate ask; it's what
you get when a caller builds the list programmatically (e.g. filters some computed-columns set down
by a condition) and the filter happens to remove everything. That's a caller bug, and the function's
whole stated purpose here is to catch exactly this class of caller mistake before it reaches SQL,
the same way it already does for a mistyped column name. Letting SQLAlchemy's generic `set_`
parameter message surface it would mean the caller sees an error about a parameter (`set_`) they
never passed, in a stack frame belonging to a library internal, instead of getting told what they
actually did wrong.

`capitalscan/jobs/db_io.py`, `upsert()`: added an explicit `if not update_columns:` check
(inside the existing `if update_columns is not None:` branch, so it only fires when the caller
passed an empty list, never for the default `None`), raising before the unknown/conflict-name
check runs. Docstring updated with a new paragraph explaining the empty-list case and quoting the
SQLAlchemy error it now preempts.

### Finding 2 (Minor) — duplicate names untested

Confirmed by the reviewer as harmless by inspection (`update_cols` is a dict comprehension keyed on
column name, so `["run_id", "run_id"]` naturally collapses to one entry). Added
`test_duplicate_names_in_update_columns_are_harmless`, which passes `update_columns=["run_id",
"run_id", "signal_strength"]` and asserts the compiled SET clause contains exactly one
`run_id = excluded.run_id` (via `sql.count(...) == 1`) plus the `signal_strength` entry — pinning
the current dict-comprehension behavior so a future refactor to something list-based can't
reintroduce a duplicate-SET-target ambiguity silently.

### Covering tests

`capitalscan/tests/unit/test_db_io_upsert.py` — two new tests added to `TestColumnScopedUpdate`:

- `test_empty_update_columns_list_raises` — `update_columns=[]` raises `ValueError` matching
  `"empty list"`.
- `test_duplicate_names_in_update_columns_are_harmless` — as described above.

### Commands and output

```
uv run pytest capitalscan/tests/unit/test_db_io_upsert.py -v
```

```
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_run_events_shaped_write_does_not_null_exit_columns PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_backtest_shaped_write_does_not_null_signal_columns PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_update_columns_none_is_byte_identical_to_today PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_unknown_column_name_raises PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_conflict_column_in_update_columns_raises PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_empty_update_columns_list_raises PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_duplicate_names_in_update_columns_are_harmless PASSED
capitalscan/tests/unit/test_db_io_upsert.py::TestColumnScopedUpdate::test_empty_data_short_circuits_before_validation PASSED

8 passed in 0.05s
```

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
```

```
600 passed in 22.95s
```

(598 from the original task-9a work plus the 2 new tests above; no other test count changes.)

`ruff check` and `ruff format --check` on `capitalscan/jobs/db_io.py` and
`capitalscan/tests/unit/test_db_io_upsert.py`: clean, no changes needed.

No live-database commands were run. No `capitalscan/tests/integration/` invocation. No `uv sync`,
`uv add`, or `cscan db migrate`.
