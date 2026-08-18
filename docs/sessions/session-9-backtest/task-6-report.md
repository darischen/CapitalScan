# Task 6 report: Exit resolution

## What I implemented

`resolve_exit_for_entry(entry, entry_idx, side, bars, indicators, ep) -> dict`
in `capitalscan/research/enrich.py`, added after `resolve_entries`. It
slices `bars`/`indicators` around one entry, calls `core.exits.resolve_exit`
once, and maps the returned `ExitResult` onto a dict with keys `exit_idx,
exit_date, exit_price, exit_reason, holding_days, ambiguous`.

Two cases short-circuit before `resolve_exit` is ever called and return a
shared `_unresolved_exit()` dict (all fields `None`/`NaN`):
- `entry["entry_price"]` is `NaN` — the position never filled.
- The forward window (`bars.iloc[entry_idx+1 : entry_idx+1+ep.max_hold_days]`)
  is completely empty — the signal fired on the last available bar.

`ind_at_entry` (`indicators.iloc[entry_idx]`) is always passed to
`resolve_exit`. `atr_at_entry` is read from `ind_at_entry["atr_14"]` via
`.get()` (tolerates a missing column; `stop_mode` other than `"atr"` never
uses it, and `core.exits.stop_level` already treats a null ATR as "no
stop"). No literal exit threshold appears anywhere in the file — every
level comes from `ep`.

## What I tested and results

`capitalscan/tests/unit/test_backtest_exit.py`, 8 tests:
1. `test_first_forward_bar_triggers_a_band_exit` — the sharp requirement.
2. `test_omitting_ind_at_entry_would_have_missed_the_same_exit` — same
   fixture fed straight to `core.exits.resolve_exit` with `ind_at_entry`
   omitted; asserts `TIMEOUT` instead, proving the first test's exit
   genuinely depends on passing `ind_at_entry`.
3. `test_holding_days_equals_exit_idx_plus_one`.
4. `test_entry_idx_offsets_the_forward_window` — 6-bar frame, `entry_idx=2`,
   a target hit planted only reachable if the window starts at
   `entry_idx+1`.
5. `test_truncated_forward_window_times_out_instead_of_raising` —
   `max_hold_days=5`, only 2 forward bars exist.
6. `test_empty_forward_window_does_not_raise` — `entry_idx` is the frame's
   last row.
7. `test_nan_entry_price_returns_unresolved_without_raising`.
8. `test_short_side_uses_its_own_stoch_threshold_not_a_mirror_of_the_long`
   (ADR 092 sanity check at this layer).

## TDD Evidence

**RED**
```
uv run pytest capitalscan/tests/unit/test_backtest_exit.py -q
```
```
ImportError while importing test module '...test_backtest_exit.py'.
E   ImportError: cannot import name 'resolve_exit_for_entry' from
    'capitalscan.research.enrich' (...\capitalscan\research\enrich.py)
```
Expected — the function didn't exist yet, so import failed at collection
before any test body ran.

**GREEN**
```
uv run pytest capitalscan/tests/unit/test_backtest_exit.py -q
```
```
capitalscan\tests\unit\test_backtest_exit.py ........                    [100%]
8 passed in 0.06s
```

Full safe suite:
```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
533 passed in 24.48s
```

## Files changed

- `capitalscan/research/enrich.py` — added `_unresolved_exit()` and
  `resolve_exit_for_entry()`; updated module docstring's Task 6 line;
  added `ExitParams` and `resolve_exit` imports.
- `capitalscan/tests/unit/test_backtest_exit.py` — new file, 8 tests.

Commit: `e42de7d` — "Add resolve_exit_for_entry: slice frames and shape
core.exits.resolve_exit output"

## First-forward-bar band exit proof

`_first_bar_band_fixture()`: entry bar at position 0 with indicator row
`bb_upper=102.0`; three forward bars follow, each with an unreachable
`bb_upper=999.0` on their own indicator rows. The first forward bar's
`high=102.5` breaches `102.0` (its `open=100.0` doesn't gap past it, so
it fills at the level, not the open). `stop_mode="none"` and the default
4% target (`104.0`) both stay clear of `102.5`, so nothing else can fire.
Result: `UPPER_BAND` at `102.0`, `exit_idx=0`, `holding_days=1`. The
companion test replays the identical `fwd_bars`/`fwd_ind` straight through
`core.exits.resolve_exit` with `ind_at_entry` omitted and gets `TIMEOUT`
— proving the exit in the first test is real, not a fixture artifact.

## exit_idx for Task 7

Surfaced, under the key `"exit_idx"`. Task 7 can index `fwd_bars`/its own
window with `result["exit_idx"]` directly rather than recomputing it from
`holding_days - 1`. When `resolve_exit_for_entry` returns the unresolved
dict, `exit_idx` is `None` — Task 7 should treat that as "no path to
measure," not a `0`.

## entry_idx for NEXT_OPEN (t+1 fill)

`resolve_exit_for_entry` does not compute `entry_idx` itself — it takes it
as a parameter and slices positionally (`entry_idx + 1` is where the
forward window starts). The docstring states the contract explicitly:
`entry_idx` must be the position of the bar in `bars`/`indicators` that
corresponds to `entry["entry_date"]` — the actual fill date — not the
signal date. For `NEXT_OPEN`, `entry["entry_date"]` is already one session
later than the signal (that's `resolve_entries`' own behavior from Task 5);
whatever caller assembles `entry_idx` (a future task — no orchestrator
calls this function yet) must look up that later date's position, not the
signal bar's. `test_entry_idx_offsets_the_forward_window` proves the
off-by-one would be caught: it plants a target-hit two bars past a
non-zero `entry_idx` that would be invisible if the window started one
bar early.

## NaN entry_price

Returns `_unresolved_exit()` without calling `resolve_exit`. Justification
in the docstring: `resolve_exit`'s stop/target checks correctly go dark on
a `NaN` entry price (`_breach` returns `False` when either side is `NaN`),
but its band and stochastic checks don't reference `entry_price` at all —
calling it anyway would still be able to return a real exit reason for a
position that was never opened, misreporting a phantom trade as resolved.

## Empty forward window

Returns `_unresolved_exit()` before calling `resolve_exit`, which would
otherwise raise `ValueError` on a wholly empty `fwd_bars`
(`core/exits.py:210`). Justification: a signal on the literal last bar of
ingested history is a real, expected production case (the newest signal
in any nightly run), not a caller error — it shouldn't crash a batch of
otherwise-resolvable entries. A window that is short but non-empty
(end-of-data truncation, a delisting) is left to `resolve_exit`, which
already times out on the last available bar correctly; I didn't special
case it.

## Self-review findings

- No literal exit thresholds in the file — checked with a grep for
  `80.0/20.0/0.04/1.5/0.03` across `enrich.py`; only match locations were
  none (verified by an empty grep result).
- No in-place mutation; the function only reads slices (`.iloc`) and
  builds new dicts.
- No IO, no clock reads — pure function over frames it's handed.
- Reused `_isnan` already imported into the module from
  `core.signals` rather than reimplementing a NaN check.
- Considered whether `_unresolved_exit()` should distinguish "never
  filled" from "empty window" with different sentinel values, but both
  are "nothing to report" from a caller's point of view and the shared
  helper avoids two near-identical literal dicts; the docstring explains
  both paths reach it.

## Fix report — review finding: entry_idx consistency guard

**Finding (Important):** `resolve_exit_for_entry` trusted `entry_idx`
without checking it against `entry["entry_date"]`, so a caller passing the
signal bar's position for a `NEXT_OPEN` entry (which fills one bar later)
would silently shift the entire forward window by one bar — CLAUDE.md
invariant 3's "highest-risk silent failure." I had flagged this as a
concern but deferred it to a future orchestrator; the reviewer's call
(which I agree with) was that the check is cheap and available now with
data already in scope, so it belongs in this task.

**What I changed** (`capitalscan/research/enrich.py`):
- Added `_assert_entry_idx_matches(entry, entry_idx, bars)`. It:
  - Raises `ValueError` if `entry_idx` is negative or `>= len(bars)`,
    rather than letting a negative index silently wrap or an oversized
    one surface as an opaque pandas `IndexError`.
  - Otherwise compares `_as_date(bars.index[entry_idx])` against
    `_as_date(entry["entry_date"])` and raises `ValueError` on mismatch.
    Reuses the module's existing `_as_date` coercion (no second
    date-resolution implementation) so the check works whether `bars` is
    indexed by plain `date` (`research/candidates.py`'s convention) or by
    `Timestamp` (`pd.date_range`, what this module's tests build with).
- Wired the call into `resolve_exit_for_entry`, placed **after** the NaN
  `entry_price` short-circuit and **before** any slicing. Placement
  matters: an entry that never filled has `entry_date=None`
  (`resolve_entries`'s terminal-bar `NEXT_OPEN` case) and nothing to
  verify against, so the guard must not see it — and it must run before
  `fwd_bars`/`fwd_ind`/`ind_at_entry` are sliced, so a bad `entry_idx`
  never reaches `resolve_exit` at all.
- Left the `atr_at_entry` coercion as-is (did not apply the suggested
  simplification): `ind_at_entry.get("atr_14")` returns `None` when the
  column is absent, and `float(None)` raises `TypeError`, so the
  `_isnan(...) else float(...)` guard is load-bearing, not indirection.

**Covering tests added** (`capitalscan/tests/unit/test_backtest_exit.py`):
- `test_entry_idx_pointing_at_the_wrong_bar_raises` — `entry_idx=0`
  (signal bar, 2026-07-30) against an entry that claims to have filled
  2026-07-31 (the exact `NEXT_OPEN` shift bug) → `ValueError`.
- `test_entry_idx_out_of_range_raises` — both `entry_idx == len(bars)`
  and `entry_idx == -1` → `ValueError`.
- `test_next_open_shaped_entry_idx_one_bar_after_the_signal_does_not_raise`
  — the required negative case: `entry_idx=1` legitimately matching
  `entry_date=2026-07-31` must be accepted, not rejected. Asserts the
  call completes and returns the expected dict shape rather than raising.
- `test_entry_idx_dtype_mismatch_between_date_and_timestamp_still_matches`
  — `bars` indexed by `Timestamp`, `entry["entry_date"]` a plain `date`
  (what `resolve_entries` actually produces); asserts the guard doesn't
  reject correct input across that dtype difference.
- Updated `test_entry_idx_offsets_the_forward_window`, which previously
  passed `entry_idx=2` with a stale `entry_date` from the default `_entry()`
  fixture (2026-07-30, matching only position 0) — the new guard now
  requires that date and position agree, so the test now passes
  `entry_date=date(2026, 8, 3)`, position 2's real date, verified via
  `pd.date_range("2026-07-30", periods=6, freq="B")`.

**Commands and output**

```
uv run pytest capitalscan/tests/unit/test_backtest_exit.py -v
```
```
12 items
test_first_forward_bar_triggers_a_band_exit PASSED
test_omitting_ind_at_entry_would_have_missed_the_same_exit PASSED
test_holding_days_equals_exit_idx_plus_one PASSED
test_entry_idx_offsets_the_forward_window PASSED
test_truncated_forward_window_times_out_instead_of_raising PASSED
test_empty_forward_window_does_not_raise PASSED
test_nan_entry_price_returns_unresolved_without_raising PASSED
test_short_side_uses_its_own_stoch_threshold_not_a_mirror_of_the_long PASSED
test_entry_idx_pointing_at_the_wrong_bar_raises PASSED
test_entry_idx_out_of_range_raises PASSED
test_next_open_shaped_entry_idx_one_bar_after_the_signal_does_not_raise PASSED
test_entry_idx_dtype_mismatch_between_date_and_timestamp_still_matches PASSED
12 passed in 0.08s
```

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
```
```
537 passed in 24.32s
```

Commit: (see below).

## Issues or concerns

- ~~`resolve_exit_for_entry` trusts the caller's `entry_idx` completely~~
  — **resolved** by the fix above: `_assert_entry_idx_matches` now
  verifies `entry_idx` against `entry["entry_date"]` before any slicing,
  and raises `ValueError` on mismatch or out-of-range. The contract is no
  longer documentation-only.
- No orchestrator wires `resolve_entries` output into
  `resolve_exit_for_entry` yet — that assembly (computing `entry_idx` per
  entry kind, calling this function per entry) isn't part of this task's
  scope per the brief, but it's the next integration point and worth
  flagging so it isn't assumed already done.
