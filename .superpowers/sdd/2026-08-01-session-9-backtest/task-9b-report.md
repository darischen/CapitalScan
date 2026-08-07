# Task 9b report: per-ticker worker, parallel dispatch, cofire post-pass, write

## Fix round 2 (review response: 1 Important NEW, 2 Minor)

Round 1 was independently re-verified clean on all five findings, including the
determinism sort tests (the re-reviewer traced the fixture construction
themselves rather than trusting my verification). This round fixes one new
defect that round 1 itself introduced through the interaction of two of its
own fixes, plus two small coverage gaps.

### Finding A (Important, NEW) — total-failure now raises instead of returning a clean report

Confirmed the reviewer's diagnosis: Finding 5's `ValueError` (a config-level
fault — every worker resolves the identical `config`, so a bad
`reach_targets` sweep raises identically on every ticker) was landing inside
Finding 4's per-ticker `except Exception`, turning a hard config break into
`BacktestReport(rows_written=0)` with every ticker silently listed in
`failed_tickers`. `test_every_ticker_failing_writes_nothing_but_does_not_raise`
had codified exactly that as the expected behavior.

**Fix**: added `BacktestRunFailed(RuntimeError)`, raised by `run_backtest`
when `len(failed_tickers) == len(sorted_tickers)` and `sorted_tickers` is
non-empty (`capitalscan/research/backtest.py`, right after the two dispatch
branches, before `pd.concat`). Chose a **dedicated exception class** over
re-raising the first underlying exception, for two reasons: (1) a config
fault produces the *same* message on every ticker, so re-raising "the first
one" would arbitrarily pick one ticker's copy of an N-way-identical failure
and imply it was special; a dedicated exception can instead deduplicate the
message set and say "all N failed, distinctly: ..." which is the more honest
summary of what actually happened. (2) `failed_tickers` (the full `{ticker:
message}` mapping) is preserved as an attribute on the exception, so a
caller that wants per-ticker detail after a total failure still has it — a
bare re-raise of one underlying exception would have discarded the other
N-1 tickers' copies entirely, even though under a genuine (non-config)
scenario where all N happen to fail independently, each one's exact message
might differ and matter.

The **partial**-failure path (`len(failed_tickers) < len(sorted_tickers)`,
at least one ticker succeeded) is untouched — `run_backtest` still writes
what succeeded and records the rest on `BacktestReport.failed_tickers`,
exactly as Finding 4 left it. Guarded the total-failure check with
`sorted_tickers and ...` so `run_backtest(tickers=[])` — a legitimate no-op,
not a failure — still returns a clean empty report rather than raising.

**Tests**: renamed `test_every_ticker_failing_writes_nothing_but_does_not_raise`
to `test_every_ticker_failing_raises_instead_of_returning_a_clean_report`
(the old name literally asserted the behavior Finding A says is wrong) and
rewrote it to assert `pytest.raises(backtest.BacktestRunFailed)`, checking
`.failed_tickers` and the message. Added
`test_partial_failure_still_writes_what_succeeded_not_a_raise` as an explicit
companion proving the boundary: one surviving ticker out of two keeps the
non-raising, partial-write path intact. `test_a_failing_ticker_does_not_block_
the_others` (Finding 4's original three-ticker partial test) is unchanged and
still passes, covering the same boundary from round 1's construction.

### Finding B (Minor) — `atr_14` and `days_to_earnings` now have non-null fixture values

Confirmed the gap: both columns were `None` in `test_backtest_worker.py`'s
`_indicators()` fixture, so `test_state_at_signal_columns_are_populated`
couldn't distinguish "correctly read as null" from "silently never read at
all" for either one — the sibling-comparison test (`test_touch_and_next_
open_siblings_carry_identical_state`) also can't catch this, since dropping
a key makes both sides `NaN` and hits its `pd.isna(left) and pd.isna(right):
continue` skip, exactly as the re-reviewer found.

**Fix**: set `atr_14=0.5` and `days_to_earnings=45` in the fixture
(`test_backtest_worker.py`'s `_indicators()`). Picked `atr_14=0.5`
deliberately small: with `stop_atr_k=1.5` (config default) the resulting ATR
stop sits at roughly `entry - 0.75`, comfortably below every forward bar's
`low` (95.5 in the fixture), so a stop is now genuinely *placed* (unlike the
previous null-ATR fixture, where `stop_level` returns NaN and no stop exists
at all) but never breached — the existing `test_touch_entry_resolves_a_real_
exit`'s TIMEOUT assertion still holds, now exercising a slightly more
realistic path than before. `days_to_earnings=45` is safely outside
`ep.max_hold_days` (5, default), so `earnings_in_window` stays `False`,
which no existing test asserts on either way. Added both to
`test_state_at_signal_columns_are_populated`'s assertions.

### Finding C (Minor) — `above_sma200`'s null test now matches `compute.py`'s spelling

Replaced `core.signals._isnan(sma_200) or core.signals._isnan(close_at_
signal)` with `pd.isna(sma_200) or pd.isna(close_at_signal)` in
`_backtest_one_ticker`, matching `jobs.compute._build_event_row`'s
`pd.isna(ind_row.get("sma_200")) or pd.isna(bar.get("close"))` exactly —
same column, same two writers, same spelling. Removed the now-unused
`from capitalscan.core.signals import _isnan` import (grepped the file to
confirm it had no other call site).

### Verification

```
uv run pytest capitalscan/tests/unit/test_backtest_worker.py capitalscan/tests/unit/test_backtest_determinism.py -v
============================= 33 passed in 0.98s ==============================

uv run pytest capitalscan/tests/unit capitalscan/tests/property
============================ 633 passed in 26.22s =============================

uv run pytest capitalscan/tests/unit/test_spawn_guard.py -v
============================== 2 passed in 0.28s ===============================
```

### Not changed (per controller "not in scope this round")

`_read_market_days`'s per-worker full-table read, the 125-line double loop
in `_backtest_one_ticker`, the `_minimal_row` duplication across the two
test files, and `test_full_universe_true_raises_no_warning`'s
`len(recwarn) == 0` brittleness — all explicitly deferred to the final
whole-branch review.

### Files changed (fix round 2)

- `capitalscan/research/backtest.py` (modified — `BacktestRunFailed` class,
  total-failure re-raise in `run_backtest`, `pd.isna` swap in `above_sma200`)
- `capitalscan/tests/unit/test_backtest_worker.py` (modified — fixture
  `atr_14`/`days_to_earnings` now non-null, new assertions, rewritten
  total-failure test, new partial-failure companion test)

---

## Fix round 1 (review response: 1 Critical, 3 Important, 1 Minor, 1 controller ruling)

### Finding 1 (CRITICAL) — state-at-signal columns now populated, read off `prior_ind`

Confirmed both halves of the reviewer's claim were correct: `prior_ind` was already
in hand (used one line below as `enrich_context`'s `ind_row` argument) and
`run_events` never writes `touch_5m`/`touch_30m`/`next_open` rows at all
(`entry_kind=EntryKind.TOUCH.value` is hardcoded), so there is no second writer to
ever fill those columns in.

Fix: added `bb_pctb`, `bb_width_pct`, `k_full`, `d_full`, `k_fast`, `k_cross_up`,
`k_cross_down`, `atr_14`, `rv_pct_252d`, `dd_52w`, `sma200_slope_60`,
`above_sma200`, `vol_z_20d`, `days_to_earnings` to `_EVENT_COLUMNS`, to
`_RUN_BACKTEST_UPDATE_COLUMNS`, and to the row dict in `_backtest_one_ticker`
(`capitalscan/research/backtest.py`), all read from `prior_ind.get(...)` — the
exact t-1 row `_prior_indicator` already looked up. Computed once per candidate
(outer loop, before the four-entry-kind inner loop), since the state describes
the signal, not the entry.

**`above_sma200` decision** (the one case the reviewer flagged as a legitimate
exception): derived the same way `jobs.compute._build_event_row` does —
`bar["close"] > ind_row["sma_200"]` — using the signal bar's own `close`
(looked up from `ticker_bars` at the signal date) against `prior_ind["sma_200"]`.
`None` only when `sma_200` or the signal-bar close is genuinely unavailable
(e.g. this module's own test fixtures, which don't carry `sma_200`) — a real
null, not an omission, matching the reviewer's framing exactly. All thirteen
other state columns are populated unconditionally from `prior_ind`, with no
exceptions.

**Test**: `test_touch_and_next_open_siblings_carry_identical_state` in
`test_backtest_worker.py` — asserts every state-at-signal column is identical
between a `touch` row and its `next_open` sibling for the same signal.
`test_touch_5m_and_touch_30m_also_carry_the_state_despite_a_null_entry_price`
covers the two entry kinds the reviewer named explicitly (no second writer,
ever). `test_state_at_signal_columns_are_populated` checks concrete values
against the fixture, including a deterministic `above_sma200=True` (fixture now
carries `sma_200=90.0` against a constant `close=96.0`).

### Finding 2 (Important) — the sort tests now fail when the sort is deleted

Verified the reviewer's diagnosis directly: temporarily replaced
`backtest.py`'s `sort_values(...)` call with `pass` and reran the suite — the
original `test_collected_frame_is_sorted_before_write` and both determinism
tests stayed green, exactly as reported, because a single-row-per-ticker fixture
under sequential (`max_workers<=1`) dispatch can't distinguish "sorted" from
"dispatched in ticker order."

Fix: rewrote
`test_collected_frame_is_sorted_by_ticker_signal_date_entry_kind` so ticker
`AAA`'s own fake worker returns two rows already reversed
(`2026-01-07` before `2026-01-05`) — no dispatch order can fix that, only the
`sort_values` call can. Added
`test_entry_kind_is_sorted_alphabetically_not_declaration_order`, which asserts
the real worker's natural output order (`touch, touch_5m, touch_30m,
next_open` — `EntryKind` declaration order) gets reordered to
`next_open, touch, touch_30m, touch_5m` before the write. Strengthened
`test_backtest_determinism.py`'s `TestRunBacktestDeterminism` with
`test_two_full_runs_with_multiple_tickers_out_of_order_are_still_identical`,
which runs two tickers with deliberately out-of-order rows through the real
`run_backtest` dispatch-and-sort path twice and asserts both the byte-identical
repeat AND the specific sorted shape.

**Verified the fix**: reran the same `pass`-for-`sort_values` substitution
against the new/rewritten tests — all three (
`test_collected_frame_is_sorted_by_ticker_signal_date_entry_kind`,
`test_entry_kind_is_sorted_alphabetically_not_declaration_order`,
`test_two_full_runs_with_multiple_tickers_out_of_order_are_still_identical`)
failed with a clear mismatch, then restored the real `sort_values` call and
confirmed all three pass again.

### Finding 3 (Important) — `full_universe` parameter added to `run_backtest`

Chose the "enforce whole-universe, make it loud" side of the reviewer's two
options, implemented as an explicit `full_universe: bool = True` parameter
rather than a raise, because `run_backtest` has no independent way to *detect*
whether a given `tickers` list is the whole universe — it can only take the
caller's word for it. `full_universe=True` (default, matches every existing
caller) writes `cofire_count` normally via the standard
`_RUN_BACKTEST_UPDATE_COLUMNS`. `full_universe=False` (for a future
`--tickers` debug flag) still computes and returns `cofire_count` on the frame
(useful for inspection, and correct for brand-new rows this run alone is
inserting), but the write's `update_columns` drops `cofire_count`, so an
`UPDATE` can never overwrite a previously-correct universe-wide count with a
subset-capped undercount. A `UserWarning` fires whenever `full_universe=False`,
naming the run's ticker-subset size, so the gap is loud rather than silent —
satisfying the reviewer's "raise or warn" instruction via the warn path.

**Tests**: `TestRunBacktestFullUniverseCofire` in `test_backtest_worker.py` —
default includes `cofire_count` in `update_columns`; `full_universe=False`
excludes it from `update_columns` (but keeps it on the written frame) and
raises `pytest.warns(UserWarning, match="full_universe")`; `full_universe=True`
raises no warning (`recwarn` is empty).

### Finding 4 (Important) — per-ticker failures isolated, partial writes preserved

Added `BacktestReport.failed_tickers: dict[str, str]` (ticker → `"ExcType:
message"`), mirroring `IngestReport.rows_flagged`'s idiom of surfacing a
problem on the report object. Wrapped both dispatch paths — the sequential
`max_workers<=1` loop and the `ProcessPoolExecutor` `as_completed` loop — in
per-ticker `try/except Exception`, recording the failure and continuing rather
than letting one `tag_clusters`/`resolve_exit_for_entry` raise abort the whole
run. Rows from every ticker that *did* succeed are still concatenated, sorted,
and written.

**Tests**: `TestRunBacktestPerTickerFailureIsolation` —
`test_a_failing_ticker_does_not_block_the_others` (one of three tickers raises;
the other two still get written; `failed_tickers` names exactly the failing
one); `test_every_ticker_failing_writes_nothing_but_does_not_raise` (total
failure degrades to zero rows written and a populated `failed_tickers`, not an
unhandled exception propagating out of `run_backtest`).

### Finding 5 (Minor) — unknown `path_metrics` columns now raise

Added an explicit check right after each `path_metrics` call: if the returned
dict has any key not in `_EVENT_COLUMNS`, raise `ValueError` naming the
offending key(s), instead of letting `pd.DataFrame(rows, columns=_EVENT_COLUMNS)`
silently drop it.

**Test**: `test_an_unrecognized_reach_target_raises` — builds a `Config` with
`stats.reach_targets=(0.02, 0.07)` (0.07 has no `touched_7pct` column in the
fixed schema) and asserts `_backtest_one_ticker` raises `ValueError` matching
`"touched_7pct"`.

### Controller ruling — `today` override on `run_backtest`

Added `today: date | None = None` to both `run_backtest` and
`_backtest_one_ticker`. `None` (the default, preserving all existing call
sites and tests unchanged) means "derive per-ticker from that ticker's own
`max(bars.ts)`," exactly as before. A caller-supplied `date` overrides the
derivation uniformly for every ticker dispatched in that run.
`_backtest_one_ticker`'s signature widened from `(ticker, config, run_id,
database_url)` to `(ticker, config, run_id, database_url, today=None)` — an
additive, backward-compatible change (positional callers unaffected; the
`ProcessPoolExecutor.submit(...)` call site in `run_backtest` was updated to
pass it positionally too, and manually re-verified against a real
`ProcessPoolExecutor(spawn)` worker after the signature change, see Verification
below).

**Tests**: `test_today_override_replaces_the_per_ticker_derivation` (an
explicit `today` before the signal date makes the signal ineligible, proving
the override reaches `apply_eligibility` rather than being ignored);
`test_an_explicit_today_override_is_deterministic_too` (two runs with the
same fixed `today` are byte-identical).

### Verification

```
uv run pytest capitalscan/tests/unit/test_backtest_worker.py capitalscan/tests/unit/test_backtest_determinism.py -v
============================= 32 passed in 0.98s ==============================

uv run pytest capitalscan/tests/unit capitalscan/tests/property
============================ 632 passed in 23.87s =============================

uv run pytest capitalscan/tests/unit/test_spawn_guard.py -v
============================== 2 passed in 0.27s ===============================
```

Also manually re-ran the real `ProcessPoolExecutor(spawn)` dispatch check
against `_backtest_one_ticker`'s new 5-argument signature (an unreachable
Postgres URL with a short `connect_timeout`, submitted with `today=None`
positionally) — clean `OperationalError` within 15 seconds, confirming no
hang or recursive process creation was introduced by the signature change:

```
worker raised as expected (no hang): OperationalError
```

### Not changed (per controller ruling / explicit scope hold)

- The five-column overlap between `_RUN_BACKTEST_UPDATE_COLUMNS` and
  `_RUN_EVENTS_UPDATE_COLUMNS` — ruled correct as built, left alone. (It grew
  by the fourteen state-at-signal columns from Finding 1's fix, for the same
  reason: both jobs derive them from the same `core.signals.detect`/t-1
  indicator read, so neither can write a value the other disagrees with.)
- `_read_market_days`'s full-table read per worker (Finding 7) and the
  125-line double loop in `_backtest_one_ticker` (Finding 8) — explicitly
  deferred to the final review; not touched in this round.

### Files changed (fix round 1)

- `capitalscan/research/backtest.py` (modified)
- `capitalscan/tests/unit/test_backtest_worker.py` (modified — new fixture
  field, new test classes for Findings 1/3/4/5, rewritten sort tests for
  Finding 2)
- `capitalscan/tests/unit/test_backtest_determinism.py` (modified — new
  `today`-override test, rewritten/strengthened `TestRunBacktestDeterminism`)

---

## Original report (fix round 0)

## What I implemented

Added to `capitalscan/research/backtest.py` (Task 9a already owned the top of this
file — `BacktestConfig`, `config_hash`, `split_key_for` re-exports — untouched):

- `_EVENT_COLUMNS` — the fixed column order for a fully-built `events` row.
- `_RUN_BACKTEST_UPDATE_COLUMNS` — the column-scoped `db_io.upsert` update list
  (Ruling C4/C5).
- `BacktestReport` — a small dataclass (`run_id`, `rows_written`, `tickers`),
  independent of `jobs.ingest.IngestReport` (no cross-import needed for it).
- `_read_bars`, `_read_indicators`, `_read_market_days`, `_read_universe_flags` —
  `research/`'s own IO helpers (not shared with `jobs/compute.py`'s, which use a
  different bounded-window convention).
- `_prior_indicator` — date-based t-1 lookup (Ruling C3), needed because
  `research.candidates.scan_candidates` does not surface the indicator row it
  used to detect the signal.
- `_entry_idx_for` — derives the entry's positional index from `entry["entry_date"]`
  (CONSTRAINTS.md item 3), never from `entry_kind`.
- `_backtest_one_ticker(ticker, config, run_id, database_url) -> pd.DataFrame` —
  the module-level, spawn-safe worker. Runs DESIGN §5.2 steps 1-12 for one ticker.
- `add_cofire_count(events) -> pd.DataFrame` — the single-threaded, cross-ticker
  post-pass (step 13).
- `run_backtest(tickers, config, run_id, engine=None, max_workers=1) -> BacktestReport` —
  dispatch, sort, post-pass, column-scoped write.
- `if __name__ == "__main__": pass` guard at module end.

## What I tested and results

Two new test files, no live database (all reads monkeypatched, matching the
established pattern in `test_run_events_column_scope.py`):

- `capitalscan/tests/unit/test_backtest_worker.py` (15 tests): one row per
  `EntryKind`, run_id/config_hash on every row, a fully-resolved TIMEOUT trade,
  unfilled hourly entries written (not dropped) with null exit/outcome fields,
  cluster columns present, empty-bars ticker returns the right empty shape,
  `add_cofire_count`'s tickers-not-rows counting (including the entry-kind
  fan-out case), `run_backtest`'s column-scoped upsert call, ticker sort before
  dispatch, frame sort before write, and the no-events-no-write path.
- `capitalscan/tests/unit/test_backtest_determinism.py` (4 tests): two full
  `_backtest_one_ticker` runs are byte-identical ignoring `run_id`; `run_id` is
  the only column that differs; `apply_eligibility`'s `today` is asserted to be
  explicitly passed and derived from `bars["ts"].max()`, never the function's
  own `date.today()` default; two full `run_backtest` runs write identical
  frames ignoring `run_id`.

### TDD evidence

**RED.** First full run of the two new test files (before the `bars`/
`ticker_bars` fix below) failed for a real structural reason, not a fixture
typo:

```
uv run pytest capitalscan/tests/unit/test_backtest_worker.py capitalscan/tests/unit/test_backtest_determinism.py -v
...
E   ValueError: 'ts' is both an index level and a column label, which is ambiguous.
capitalscan\research\enrich.py:73: in _ticker_bars
    frame = frame.sort_values("ts")
```

10 of 19 tests failed this way. Root cause: I was passing my own
`Timestamp`-indexed `ticker_bars` (index name `"ts"`, column `"ts"` kept via
`drop=False`) into `resolve_entries`, which internally calls
`research.enrich._ticker_bars` — that helper does its own `sort_values("ts")` +
date-indexing, and a frame that already has `"ts"` as both its index name and a
column makes that call ambiguous. This was a genuine bug the test caught, not a
fixture problem — confirmed correct failure, not a false negative, because the
traceback pointed at the real orchestration bug (passing the wrong frame to
`resolve_entries`), not at an assertion in the test itself.

**GREEN**, after passing the raw `bars` frame (not `ticker_bars`) into
`resolve_entries` (with a comment explaining why), plus two fixture corrections
(the initial fixture's uniform `high=100.0` triggered a gap-fill TARGET exit on
the first forward bar instead of the intended TIMEOUT — a fixture bug, not an
implementation one, since target = 95 × 1.04 = 98.8 was inside `high`'s reach):

```
uv run pytest capitalscan/tests/unit/test_backtest_worker.py capitalscan/tests/unit/test_backtest_determinism.py -v
============================= 19 passed in 0.63s ==============================
```

Full required suite:

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property
============================ 619 passed in 24.94s =============================

uv run pytest capitalscan/tests/unit/test_spawn_guard.py -v
============================== 2 passed in 0.27s ===============================
```

I also manually verified real `ProcessPoolExecutor(spawn)` dispatch of
`_backtest_one_ticker` (not just the generic guard test) against a real,
unreachable Postgres URL with a short `connect_timeout` — the worker process
raised `OperationalError` cleanly within 15 seconds, confirming no recursive
process creation and no hang:

```
worker raised as expected (no hang): OperationalError
```

## Files changed

- `capitalscan/research/backtest.py` (modified — Task 9a's re-exports at the
  top are untouched; everything below `__all__` is new)
- `capitalscan/tests/unit/test_backtest_worker.py` (new)
- `capitalscan/tests/unit/test_backtest_determinism.py` (new)

## `_RUN_BACKTEST_UPDATE_COLUMNS`

```python
[
    "run_id", "signal_types_all", "signal_strength", "side", "touch_level",
    "cluster_id", "seq_in_cluster", "is_cluster_head", "days_since_head",
    "entry_date", "entry_price", "entry_gapped",
    "exit_date", "exit_price", "exit_reason", "holding_days", "ambiguous",
    "gross_ret", "net_ret", "mfe", "mae", "time_to_mfe", "capture_ratio",
    "touched_2pct", "day_touched_2pct", "touched_3pct", "day_touched_3pct",
    "touched_5pct", "day_touched_5pct", "touched_10pct", "day_touched_10pct",
    "fwd_ret_1d", "fwd_ret_2d", "fwd_ret_3d", "fwd_ret_5d", "fwd_ret_10d",
    "dd_bucket", "bw_regime", "era", "cofire_count", "earnings_in_window",
    "split_key", "vix_close", "spx_ret_1d",
]
```

Derivation: CONSTRAINTS.md pins the mandatory core — "the exit/return/path/
context columns you compute PLUS the four cluster columns." I built the list
by walking the `events` schema (DESIGN §5.7) and including exactly the columns
`_backtest_one_ticker` computes a real value for: all of entry (§5.4, all four
`EntryKind`s, not just `TOUCH`), exit (§5.5), outcome, reachability, forward
returns, context/split, and the four cluster columns Ruling C5 assigns
exclusively to the backtest.

Five columns overlap with `_RUN_EVENTS_UPDATE_COLUMNS` (`run_id`,
`signal_types_all`, `signal_strength`, `side`, `touch_level`, plus the
already-established overlap on `entry_date`/`entry_price`/`entry_gapped`,
`dd_bucket`, `split_key`, `vix_close`, `spx_ret_1d`). This is deliberate, not
an oversight: both jobs derive these from the same pure computation
(`core.signals.detect` for the signal-identity fields; `split_key_for` for
`split_key`; the same `market_days` row for `vix_close`/`spx_ret_1d`), so
neither job can write a value the other disagrees with — the two-writers
precedent `_RUN_EVENTS_UPDATE_COLUMNS` already set for the entry/dd_bucket/
split_key/vix columns.

**Deliberately excluded**: `bb_pctb`, `bb_width_pct`, `k_full`, `d_full`,
`k_fast`, `k_cross_up`, `k_cross_down`, `atr_14`, `rv_pct_252d`,
`sma200_slope_60`, `above_sma200`, `vol_z_20d`, `days_to_earnings`. These
remain `run_events`' exclusive domain: `research.candidates.scan_candidates`'s
own `_CANDIDATE_COLUMNS` deliberately does not retain the indicator row it
used to detect a signal (see that module's docstring), so `_backtest_one_ticker`
has no value to write for them. On a row this job is the sole writer of — the
three entry kinds `run_events` never creates (`touch_5m`, `touch_30m`,
`next_open`) — these columns are genuinely `NULL` until `run_events` next runs
against the same date range and fills them in. This is a real, intentional gap:
under invariant 4, an honest `NULL` beats a value this job cannot actually
derive. `mcap_usd`, `sector`, and `is_terminal` are also not computed anywhere
in Task 9's scope (no ADR pins their derivation) and stay `NULL` for the same
reason — matching `enrich_context`'s own precedent of leaving `bw_regime`
deliberately `None`.

## Entry-index derivation

`_entry_idx_for(entry, ticker_bars)` reads `entry["entry_date"]` (never
`entry["entry_kind"]`) and looks up its position in a `pd.Timestamp`-indexed,
date-sorted `ticker_bars` frame via `.index.get_loc(pd.Timestamp(entry_date))`.
Returns `None` only when `entry_date` itself is `None` — the terminal-bar
`NEXT_OPEN` case, where there is no bar to open on at all. A known
`entry_date` with a `NaN` `entry_price` (the pre-2024 hourly case) still
resolves to a real index, because `resolve_exit_for_entry` needs a valid index
to reach its own NaN-price short-circuit, and `path_metrics`' unconditional
`fwd_ret_*d` still wants a price anchor even when that specific entry kind
never filled.

One correctness detail this surfaced: `core.exits.resolve_exit` calls
`.date()` on `fwd_bars.index[exit_idx]`, which requires the bars frame to be
indexed by `pd.Timestamp`, not by plain `date` — the convention
`research.enrich._ticker_bars` uses internally for its own (different)
purposes. `_backtest_one_ticker` therefore builds its own `ticker_bars`
(`bars.sort_values("ts").set_index("ts", drop=False)`) distinct from the frame
it hands to `resolve_entries`, which does its own internal date-indexing on
the raw `bars` frame. Passing the wrong one into `resolve_entries` was exactly
the RED failure above.

## Price series

- `fwd_bars` passed to `path_metrics`: **split-adjusted OHLC** — a direct
  slice of `ticker_bars` (built from `bars`, the split-adjusted table), the
  same frame passed to `resolve_exit_for_entry`. Comment at the slice site
  (`backtest.py`, the `fwd_window = ticker_bars.iloc[...]` block) states this
  explicitly and cites DESIGN §2.2.
- `adj_close_fwd` passed to `path_metrics`: **total-return adjusted close**
  (`ticker_bars["adj_close"]`), starting at the entry bar itself
  (`entry_idx`) through `entry_idx + max(fwd_ret_horizons) + 1`. Comment at
  that slice states the same DESIGN §2.2 rule and why it's `adj_close`, not
  `close`.
- The full `[t+1, t+max_hold_days]` window is passed for `fwd_bars`
  (never pre-truncated to the realized `exit_idx`) — `path_metrics` handles
  the MFE/MAE-vs-reachability window split internally, per CONSTRAINTS.md
  item 5.

## Determinism

- `tickers` sorted (`sorted(set(tickers))`) before dispatch in `run_backtest`.
- The collected frame is sorted by `(ticker, signal_date, entry_kind)` before
  the write, unconditionally — not only when `max_workers > 1` — since a
  sequential run should produce the identical on-disk order to a parallel one.
- `run_id` is a required parameter to both `_backtest_one_ticker` and
  `run_backtest`; neither function generates one or creates a `runs` row —
  that responsibility stays with the caller (Task 11's CLI), consistent with
  "no wall-clock read inside the engine."
- `apply_eligibility`'s `today` is always passed explicitly, derived from
  `bars["ts"].max().date()` — a function of the loaded data, not the clock.
  Same config + same DB snapshot always resolves the same bound.

**What the determinism test actually compares**: `_backtest_one_ticker`
invoked twice with identical config/data but different `run_id` strings; the
two output frames are asserted `pd.testing.assert_frame_equal`-identical after
dropping the `run_id` column. A second test at the `run_backtest` level does
the same across two full dispatch-and-write cycles, comparing the two frames
captured at the mocked `db_io.upsert` call site.

## Entries that never filled

**Written, never dropped.** `resolve_entries` always returns one dict per
`EntryKind`, and an entry with `entry_price = NaN` (pre-2024 hourly kinds
without an `hourly` frame, or `NEXT_OPEN` on the terminal bar) still becomes a
row in the output frame, with every exit/outcome/path column null instead of
a fabricated value. This follows directly from two places in the existing
code I was told to read rather than re-derive: `resolve_entries`'s own
docstring ("yield a NaN price rather than being dropped... that absence
belongs in the data, not as a missing row a downstream `COUNT(*)` would
silently undercount") and invariant 4 ("never fill... NaN is the honest
answer where coverage is absent"). Dropping these rows would make hourly
coverage look like it started earlier than 2024-08-06 to anyone counting rows
per entry kind.

## Self-review findings

- **Circular import risk avoided deliberately, not accidentally.**
  `research/enrich.py` already imports `split_key_for` from
  `research/backtest.py` at module scope (Ruling C2, landed before this
  task). Adding a module-level `from capitalscan.research import enrich` to
  `backtest.py` would create a real import cycle. I used function-body
  imports inside `_backtest_one_ticker` instead, documented with a comment at
  the top of the Task 9b section — verified working both as a plain `import`
  and under real `ProcessPoolExecutor(spawn)`.
- **YAGNI check**: I did not add a `today` parameter to `run_backtest`'s
  signature (the pinned interface has none) — `today` is scoped inside
  `_backtest_one_ticker`, derived per-ticker from that ticker's own loaded
  bars, which is also more correct than a single global `today` would be for
  tickers with different data currency.
- **Naming**: fixed one copy-paste typo caught on self-review —
  `add_cofire_count`'s docstring said "entry_type" where it meant
  "signal_type" in the grain description.
- **Pristine output**: reran the full required suite plus the spawn guard
  test after every edit; all green with no warnings beyond pandas' expected
  `SettingWithCopyWarning`-class noise (none observed).

## Issues or concerns

- **Overlap in `_RUN_BACKTEST_UPDATE_COLUMNS` with `_RUN_EVENTS_UPDATE_COLUMNS`**
  (5 columns, listed above) is a judgment call, not something CONSTRAINTS.md
  states outright. I followed the precedent the already-landed
  `_RUN_EVENTS_UPDATE_COLUMNS` itself set (it already overlaps with what this
  task computes on `entry_date`/`entry_price`/`entry_gapped`/`dd_bucket`/
  `split_key`/`vix_close`/`spx_ret_1d`) rather than introduce a second,
  narrower rule. Flagging for controller review in case the intended design
  was strict disjointness after all.
- **Indicator-state columns left unfilled on backtest-only rows** (see the
  "Deliberately excluded" list above) is a real, permanent gap for any event
  whose only writer is `run_backtest` (the three non-`touch` entry kinds) run
  before `run_events` covers that date range. This is a scope decision under
  CONSTRAINTS.md's explicit narrowing, not an oversight — flagging it clearly
  in case the controller wants those columns backfilled by a future task
  rather than left to `run_events`'s next pass.
- Did not build the CLI command, the validation harness, or the sweep
  (Tasks 10-12), per scope discipline in the brief.
