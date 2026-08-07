# Task 3 report: Candidate scan, eligibility, debounce

## What I implemented

`capitalscan/research/candidates.py`, three functions covering DESIGN §5.2
steps 3-5:

- `scan_candidates(bars, indicators, sp) -> tuple[pd.DataFrame, list[dict]]`
  — per-ticker date-based pairing of each bar to the latest indicator row
  strictly before it (Controller Ruling C3), null-required-field check,
  then `core.signals.detect`. Returns candidate rows plus reject records.
- `apply_eligibility(candidates, universe_flags, sp_splits, today=None) -> tuple[pd.DataFrame, list[dict]]`
  — drops rows outside `[event_start, today]` and rows whose ticker is not
  in-trade on that date. `today` is an injectable argument, not a clock
  read, so a backtest stays a pure function of its inputs (ADR 060).
- `debounce(candidates) -> pd.DataFrame` — one row per
  `(ticker, bound, signal_date)` via `core.signals.debounce_key`, keyed on
  an explicit `SimpleNamespace(ticker, ts, side)` stand-in rather than a
  full `SignalHit` reconstruction (the candidates frame doesn't carry
  `pctb`/`k_full`, and `debounce_key` only ever reads `ticker`, `ts`,
  `side`).

Module-level `_REQUIRED_INDICATOR_FIELDS = ("bb_lower", "bb_upper", "k_full")`
and `_CANDIDATE_COLUMNS` mirror `compute.py:739` and the brief's named
columns.

## Controller Ruling C3 applied

`scan_candidates` pairs by date, not position: for each ticker it indexes
both `bars` and `indicators` by `ts.dt.date`, then for each bar looks up
`ind_group.index[ind_group.index < bar_date].max()` — the shipped rule at
`jobs/compute.py:731-738`. It does not touch `compute.py`; the logic is a
fresh implementation in `candidates.py`, citing the source lines in
comments.

## Where I put the null check, and why

In `scan_candidates`, immediately after resolving the t-1 indicator row and
before calling `detect()` — mirroring `run_events`'s own Step 2
(`compute.py:740-743`). Reasoning:

- The null check needs the raw indicator row's `bb_lower`/`bb_upper`/
  `k_full` values. `apply_eligibility` never receives the indicators frame
  (per the brief's pinned signature), and the candidates frame doesn't carry
  those fields either — so by the time a row reaches `apply_eligibility`,
  the information needed to reject it for nullness no longer exists in an
  accessible form.
- This mirrors `run_events`'s existing precedent exactly: it does the same
  check at the same point in the loop, for the same reason.

**Deviation flagged for controller review:** the brief's interfaces section
pins `scan_candidates -> pd.DataFrame`. I widened it to
`tuple[pd.DataFrame, list[dict]]` so the null-check reject records (required
by the brief's own Step 1: "a null ... drops the row and produces a reject
record") have somewhere to go. The task's own clarification note explicitly
invited this: *"Place it wherever it reads cleanest and say where you put
it."* I read that as license to adjust the return shape if the natural
placement demanded it, but it is a real interface change from what the
brief printed, so I'm calling it out rather than treating it as settled.

## Confirmation the t-1 test genuinely distinguishes t from t-1

`TestScanCandidatesReadsTMinus1::test_uses_the_prior_dated_indicator_row_not_the_bars_own_date`:

- One bar, dated `2026-07-30`, `low=94.0`.
- Two indicator rows for the same ticker: `2026-07-29` has
  `bb_lower=95.0, k_full=15.0` (touch: `94 <= 95` true; oversold: `15 <= 20`
  true → fires `CONFLUENCE_LOW`). `2026-07-30` (the bar's **own** date) has
  `bb_lower=50.0, k_full=90.0` (touch: `94 <= 50` false; oversold: `90 <= 20`
  false → fires **nothing**).
- If the implementation paired the bar with its own-date row (the t bug),
  `scan_candidates` would return zero candidates. It returns one, with
  `signal_type == "confluence_low"` and, more precisely,
  `touch_level == pytest.approx(95.0)` — pinning the *value* to the t-1
  row's `bb_lower`, not merely asserting "a signal fired."

A second test, `test_gap_between_frames_does_not_shift_the_pairing`,
targets Ruling C3's specific concern: a bar exists at `2026-07-29` with no
matching indicator that day, and the only indicator row is `2026-07-28`; a
second bar at `2026-08-03` (a gap) must still pair with the same
`2026-07-28` row rather than a positionally-shifted one. `k_full` is held
neutral (50) in this fixture so only the touch condition — which depends on
each bar's own `low` — decides whether a hit fires, isolating the pairing
question from the stochastic condition.

## The `_in_trade` duplication

`apply_eligibility` calls a module-level `_in_trade` in `candidates.py`
that reimplements `jobs/compute.py:624-635`'s v1 fail-open semantics
(no evaluation on or before the date ⇒ `True`). This is now a **second
copy** of that rule, per the task's explicit instruction not to import a
private from `compute.py` and not to consolidate unilaterally. Flagging for
the controller: the two implementations are identical today, but any future
edit to one and not the other silently diverges eligibility between the
`events` job and the backtest engine.

## TDD Evidence

**RED** — `uv run pytest capitalscan/tests/unit/test_backtest_candidates.py -q`
before `candidates.py` existed:

```
ImportError while importing test module '...test_backtest_candidates.py'.
E   ModuleNotFoundError: No module named 'capitalscan.research.candidates'
=========================== short test summary info ===========================
ERROR capitalscan/tests/unit/test_backtest_candidates.py
Interrupted: 1 error during collection
```

Expected: the module under test didn't exist yet — a collection error, not
a logic failure, which is the correct RED state before writing any
implementation.

**GREEN** — same command after implementing `candidates.py`:

```
collected 15 items
capitalscan\tests\unit\test_backtest_candidates.py ...............   [100%]
============================= 15 passed in 0.09s ==============================
```

**Full gate** — `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`:

```
collected 498 items
...
============================ 498 passed in 24.17s =============================
```

(This includes `test_signature_guarantee.py`, unaffected — `detect()`'s
signature was never touched.)

## Files changed

- Created: `capitalscan/research/candidates.py`
- Created: `capitalscan/tests/unit/test_backtest_candidates.py`
- Not touched: `capitalscan/jobs/compute.py` (per instructions)

Commit: `fe8c921` — "Add candidate scan, eligibility, debounce (Session 9
Task 3)"

## Self-review

- **Completeness against brief-as-amended**: all three functions present
  with the named behaviors; date-based pairing per Ruling C3; null check
  produces reject records; eligibility drops on window and in-trade;
  debounce collapses same `(ticker, bound, date)` and keeps distinct bound
  or distinct date as separate rows.
- **Naming**: matches the brief (`scan_candidates`, `apply_eligibility`,
  `debounce`, `_CANDIDATE_COLUMNS`) and mirrors `compute.py`'s
  `_REQUIRED_INDICATOR_FIELDS`/`_in_trade` naming for a reader who already
  knows that module.
- **YAGNI**: did not add cluster tagging, entry/exit resolution, or cost
  application — those are later tasks (DESIGN §5.2 steps 6+). Did not add
  a `today` default of anything other than `date.today()`, and did not add
  configurability beyond what the brief and CLAUDE.md invariant 9 (no magic
  numbers outside `core/config.py`) require — there are no new numeric
  constants in this module.
- **Do tests verify real behavior**: yes — the t-1 test pins a specific
  value (`touch_level == 95.0`), not just "a candidate exists"; the gap
  test constructs an actual pairing ambiguity rather than a trivial case;
  the null test checks both the drop and the reject reason string; the
  debounce tests distinguish same-day-same-bound collapse from
  same-day-different-bound and different-day-same-bound non-collapse.
- **Pristine output**: ran only the two required test directories, no
  integration tests touched, no database or clock accessed by default in
  any test (all pass `today=` explicitly).

## Issues or concerns

1. **`scan_candidates`'s return-type widening** (documented above) is a
   real deviation from the brief's printed interface. I believe it's the
   correct call given where the data actually lives, but it's a design
   decision, not a mechanical implementation detail, and should get an
   explicit yes/no from the controller before Task 4 builds on it.
2. **`_in_trade` duplication** (documented above) — now two copies of the
   same v1 fail-open rule. Not consolidated per instructions; flagging for
   a controller ruling on whether it should move to a shared home (e.g.
   `core/universe.py` — currently no IO, so the check itself could live
   there, or a small `jobs`-and-`research`-shared IO-free helper module).
3. The brief's Step 1 description of the debounce test case ("collapses two
   lower-bound touches on one ticker **one day apart**") reads as
   inconsistent with `debounce_key`'s definition, which keys on the exact
   `signal_date` — two touches a day apart carry different keys and would
   never collapse under the shipped `debounce_key`. I treated this as
   wording drift and tested the semantics `debounce_key` actually
   implements (same ticker, same day, same bound → collapse), which also
   matches the DESIGN §5.2 step 5 description ("one per
   ticker/bound/day"). Flagging in case "one day apart" was deliberate and
   I'm missing an intended second dedupe key.
