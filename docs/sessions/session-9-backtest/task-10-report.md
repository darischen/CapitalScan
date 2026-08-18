# Task 10 report: Validation harness

Commit: `dcc612b` — "Add the validation harness (Task 10, DESIGN §5.10, Phase 3 gate)"

## What I implemented

`capitalscan/research/harness.py`, exposing:

```python
run_harness(events: pd.DataFrame,
            bars_by_ticker: dict[str, pd.DataFrame],
            config: Config) -> HarnessReport
```

`HarnessReport` holds one `CheckResult` per check (`no_lookahead`,
`entry_sanity`, `exit_sanity`, `return_identity`, `non_overlap`), each with
`passed: bool`, `violations: list[dict]`, and `detail: dict` (diagnostics —
e.g. the measured Jaccard values — useful even when a check passes).
`HarnessReport.all_passed` ANDs all five. No check raises; each collects its
own violations and returns, so a caller sees all five verdicts on one run
regardless of how many failed.

**Design decision flagged for review: `bars_by_ticker`'s shape.** The task
interface names `bars_by_ticker: dict[str, pd.DataFrame]` with no separate
indicators parameter, but the no-look-ahead check needs both bars and
indicators to rerun `scan_candidates`. I resolved this by defining
`bars_by_ticker`'s frames as **bars and indicators merged, one frame per
ticker** — every OHLCV column plus every indicator column, sharing
`ticker`/`ts`. This is documented at length in the module's docstring. The
same merged frame is passed as both the `bars` and `indicators` positional
argument to `scan_candidates` (it reads only the columns each role needs),
so this stays one detection path, not two. If Task 11's CLI was designed
around two separate dicts instead, that's a one-line adapter at the call
site (`pd.merge(bars_df, indicators_df, on=["ticker","ts"])` per ticker) —
flagging this now rather than silently guessing wrong.

## What I tested and results

`capitalscan/tests/unit/test_backtest_harness.py`, 18 tests, one class per
check plus a report-shape class. Every check has both a clean-pass test and
a genuine-violation test (see the per-check section below).

**TDD evidence.**

RED — before implementing `harness.py`'s check bodies (only the interface
skeleton with `pass`/`NotImplementedError` existed), I ran:

```
uv run pytest capitalscan/tests/unit/test_backtest_harness.py -v
```

and confirmed collection/attribute errors on every test (`AttributeError:
module has no attribute 'run_harness'` at first, then per-check failures
once the skeleton existed) — i.e., failing for the expected reason
(missing implementation), not a fixture bug. I did not preserve that raw
transcript verbatim in this report, but the same command's current GREEN
run is below; the RED phase was the standard "call the not-yet-implemented
function, watch it fail" cycle before each check body was written, in the
order: entry sanity, exit sanity, return identity, non-overlap, no
look-ahead (deliberately last — it needed the calibrated fixture, described
below).

GREEN:

```
$ .venv/Scripts/python.exe -m pytest capitalscan/tests/unit/test_backtest_harness.py -v
...
18 passed in 1.79s
```

Full safe suite after the harness landed:

```
$ .venv/Scripts/python.exe -m pytest capitalscan/tests/unit capitalscan/tests/property -q
...
651 passed in ~29s
```

(Never ran bare `pytest`; never touched `capitalscan/tests/integration/`.)

## Files changed

- Created `capitalscan/research/harness.py`
- Created `capitalscan/tests/unit/test_backtest_harness.py`

## Per-check: violation constructed, and confirmation the check catches it

**Entry sanity** (`TestEntrySanity`)
- `test_catches_an_entry_price_planted_outside_the_bar_range`: `entry_price
  = 150.0` against a bar with `high = 97.0` → `entry_sanity.passed is
  False`, one violation, `reason == "entry_price_outside_bar_range"`.
- `test_next_open_entry_is_checked_against_the_fill_bar_not_the_signal_bar`:
  a `NEXT_OPEN` entry priced validly for the *signal* bar's range (96.5)
  but the *fill* bar (t+1) trades at ~150 — proves the check reads
  `entry_date`, not `signal_date`, when selecting the bar to compare
  against (CONSTRAINTS.md item 3).

**Exit sanity** (`TestExitSanity`)
- `test_catches_an_exit_price_planted_outside_the_bar_range`: `exit_price =
  200.0` against a bar with `high = 99.0` → caught, `reason ==
  "exit_price_outside_bar_range"`.

**Return identity** (`TestReturnIdentity`)
- `test_catches_a_gross_ret_that_disagrees_with_realized_return`: stored
  `gross_ret = 0.5` against real prices whose true return is ~3.97% →
  caught, `reason == "gross_ret_mismatch"`.
- `test_one_side_null_the_other_not_is_a_violation`: `entry_price = NaN`
  (unresolved) but stored `gross_ret = 0.04` (claims a resolved trade) →
  caught. This is the specific NaN-asymmetry case the brief asked me to
  reason through explicitly (see below).

**Non-overlap** (`TestNonOverlap`)
- `test_catches_two_cluster_heads_whose_windows_overlap`: two
  `is_cluster_head=True` rows on ticker TSM, 2 trading bars apart, against
  `ExitParams().max_hold_days == 5` → caught, `reason ==
  "cluster_head_windows_overlap"`. This is a scenario `tag_clusters` itself
  would never produce (a correct tagger would have merged these into one
  cluster with only the first as head) — the test constructs the violation
  by hand, standing in for a tagger regression or hand-built input.
- `test_non_head_rows_are_ignored` confirms the converse isn't a false
  positive: a head and its own non-head cluster member overlapping is
  expected and must not be flagged.

**No look-ahead** (`TestNoLookahead`)
- `test_catches_a_blind_detector_that_ignores_indicators`: monkeypatches
  `harness.scan_candidates` (the module-local bound name — patching
  `research_candidates.scan_candidates` directly does *not* affect it,
  since `harness.py` does `from ... import scan_candidates`; I hit this as
  a real bug during TDD and fixed the test, not the module) with a
  detector that fires on every bar regardless of the indicator frame
  handed to it. Result: `no_lookahead.passed is False`, and specifically
  the `shift1_material_change` bound fires (the blind detector's shifted
  event set is identical to its base set, since it never reads the
  shifted column at all).
- `test_no_bars_supplied_is_a_violation_not_a_silent_pass`: empty
  `bars_by_ticker` → `no_lookahead.passed is False`, not a vacuous pass.
  This directly answers the task's "a check that cannot fail is worse than
  no check" concern for the one check with no natural "empty means clean"
  reading.
- `test_passes_on_a_market_shaped_fixture_that_genuinely_reads_indicators`:
  a synthetic Ornstein-Uhlenbeck price/band universe (below) that
  satisfies all four TESTS.md §3.1 bounds with real margin.

## How I handled slippage in the entry/exit sanity comparison

Read `core/exits.py`, `research/enrich.py`, and `core/costs.py` end to end
before writing either check. Finding: **only `entry_price` has slippage
baked into it.** `research.enrich.resolve_entries` computes `price = raw ±
raw * slippage_bps/1e4` (adverse to side) for every `EntryKind`, including
`NEXT_OPEN`. `core/exits.py`'s `resolve_exit` never applies slippage —
every returned `exit_price` is `open_`, a stop/target level the bar's own
extreme actually reached, or `bar["close"]`, all of which are necessarily
inside that bar's `[low, high]` already. The exit-side "slippage" the
project's docstrings refer to (`core/costs.py`: "slippage applies on both
legs") is charged in return-space by `apply_costs` when `net_ret` is
computed, not baked into `exit_price` itself.

So: entry sanity reverses slippage (`_pre_slippage_price` — divides by `(1
± slippage_bps/1e4)` depending on side) before comparing to `[low, high]`;
exit sanity compares `exit_price` exactly, with no slippage adjustment.
Both use a `_PRICE_TOL = 1e-4` tolerance for float rounding (CLAUDE.md:
"round prices to 4 decimals before any comparison" — two independently
rounded prices can differ by up to `2 * 0.00005` before the comparison even
starts).

## How I treated NaN prices in the return-identity check

`realized_return` returns NaN whenever either price is NaN. Both stored and
recomputed NaN is treated as a match, not a violation — that is the honest
answer for a genuinely unresolved position (pre-2024 hourly entry kinds, or
a terminal-bar `NEXT_OPEN`), and DESIGN §5.4 documents that these rows are
still written, never dropped. A mismatch where exactly one side is NaN
*is* a violation: it means the recomputation disagrees with the engine
about whether the position resolved at all, which a naive
`np.isclose(..., equal_nan=True)`-style shortcut would treat as either
always-pass (masking the case above) or always-fail (rejecting every
legitimately unresolved row) depending on which way it leaned. I
implemented the asymmetric-NaN case explicitly rather than relying on a
single tolerant comparison function, and covered it with a dedicated test
(`test_one_side_null_the_other_not_is_a_violation`).

## Which measure I used for non-overlap, and why it agrees with `tag_clusters`

Trading bars, via `research.candidates._trading_bars_between` — imported
and called directly, not reimplemented. `tag_clusters` (Ruling C5) counts
trading bars, not calendar days, because `ExitParams.max_hold_days` counts
forward bars; a calendar-day gap test would disagree with the tagger across
every weekend or holiday (a 5-calendar-day gap spanning a weekend is only 3
trading bars). Reusing the tagger's own gap function is what keeps this
check from silently disagreeing with the data it's checking — the
`config.exits.max_hold_days` comparison is literally the same comparison
`tag_clusters` makes when it decides whether to start a new cluster.

One deliberate divergence from `tag_clusters`, called out in the code: I
group by **ticker alone**, not `(ticker, side)`. The task brief phrases the
check as "no two cluster-head events *on one ticker*"; `tag_clusters`
itself keys by `(ticker, side)`, so a long cluster's head and a short
cluster's head on the same ticker are never checked against each other by
the tagger. I check them against each other anyway — two overlapping
positions on one ticker are not independent samples regardless of side,
which is exactly the kind of cross-cutting invariant a validation harness
should catch that a per-side tagger structurally cannot see. Flagging this
explicitly in case the controller wants it scoped to `(ticker, side)`
instead.

## The look-ahead bounds I used and where they came from

Exactly TESTS.md §3.1's four bounds, cited in a module-level comment
(`harness.py:49-65`), not the task brief's paraphrase:

```
jc  = jaccard(base, shuffled_control)   < 0.15
j[1] = jaccard(base, shift_1)           < 0.80
j[1] > j[2] > j[5] > j[20]              (monotonic decay)
j[5]                                    < 0.50
j[20]                                   < 2 * jc
```

Implemented as named module constants (`_JACCARD_CONTROL_FLOOR = 0.15`,
etc.), each commented with its TESTS.md source, per CONSTRAINTS.md's
allowance for judgment-call thresholds that have no natural home in
`core/config.py`. `_SHIFT_LEVELS = (1, 2, 5, 20)` and `_SHUFFLE_SEED` (a
fixed constant, for ADR 060 determinism — no wall-clock read) are the other
two.

**Calibrating the "clean pass" fixture.** TESTS.md's own numbers come from
real bars, where a one-bar shift alone lands near 0.59 (not below 0.5) —
band drift (~0.35%/day) versus a bar's own range (~1.59%) makes adjacent
days correlated by construction. I built a synthetic universe
(`_synthetic_bars_with_bands` in the test file) reproducing that same
mechanism rather than hand-picking numbers to satisfy the assertions: an
Ornstein-Uhlenbeck (mean-reverting) price path under a real rolling
Bollinger-band computation (`.rolling(window).mean()` / `.std(ddof=0)`),
with the stochastic conditions held neutral (`k_full = 50.0` throughout) so
only the band-touch condition — the one TESTS.md §3.1's example is about —
decides the event set. I tuned the free parameters (mean-reversion
strength, band window, band-width multiplier, intraday noise) empirically
against `_event_set`/`_jaccard` directly, outside the test suite, until all
four bounds held with margin; that search is not saved anywhere but is
described in the fixture's own docstring, and the resulting seed (7) and
parameters are fixed in the test for reproducibility. Measured values on
that fixture: `j1≈0.46, j2≈0.32, j5≈0.17, j20≈0.12, jc≈0.13` — comfortably
inside every bound, both because those are the actual numbers a real
mean-reverting band series produces and because I picked parameters that
gave margin rather than sitting on the boundary.

Running the full ladder against this ~800-row synthetic universe takes
~1.5s (6 detection passes: base, 4 shifts, 1 shuffled control); the whole
harness test file runs in 1.79s.

## Self-review findings

- **Ruff clean**: `ruff check` on both new files reports no issues
  (fixed several `E501` line-length violations and two unused imports
  during self-review — `pytest` and `ExitParams`/`CostParams` that ended up
  unneeded in the test file).
- **Empty-events guard**: entry sanity, exit sanity, return identity, and
  non-overlap all short-circuit on `events.empty` before touching a column
  that wouldn't exist on a zero-column empty frame (`_events([])` in the
  no-look-ahead tests, which only care about `bars_by_ticker`, hits this
  path). Caught by running the full suite, not anticipated in advance —
  worth flagging since it means an empty-events harness run reports those
  four checks as a vacuous pass (`n_checked: 0`), which is visible in
  `detail` for a caller that wants to distinguish "checked and clean" from
  "nothing to check."
- **No YAGNI additions beyond `all_passed`**: I added one convenience
  property (`HarnessReport.all_passed`) beyond the five named fields the
  brief asked for; it's a one-line `all(...)` with no new state, useful for
  any future CLI gate check (Task 11, out of scope here) and cheap enough
  not to be scope creep.
- **Naming**: `CheckResult`/`HarnessReport` mirror the brief's "a bool per
  check and per-violation detail" language directly; check function names
  (`_check_entry_sanity`, etc.) match the DESIGN §5.10 table's row names.
- One test-file bug I want on record because CONSTRAINTS.md specifically
  calls out this failure mode: my first version of
  `test_catches_a_blind_detector_that_ignores_indicators` monkeypatched
  `research_candidates.scan_candidates` and the test **passed even though
  the patch never took effect** — no, actually it *failed* first (the
  patch didn't take effect, so the real detector ran and correctly passed
  the ladder, making my "expect failure" assertion fail) — which is what
  caught the bug immediately rather than shipping a check-that-cannot-fail
  test silently. Fixed by patching `harness.scan_candidates` (the
  module-local bound name) instead.

## Issues or concerns

- **The `bars_by_ticker` shape decision is a judgment call**, not something
  pinned in CONSTRAINTS.md or the brief. I've documented my reasoning at
  length in the module docstring and above; if Task 11's CLI plan already
  assumes two separate dicts, this needs reconciling before Task 11 lands,
  not after.
- **Non-overlap grouped by ticker, not `(ticker, side)`**: also a judgment
  call, documented in the function's own docstring, following the brief's
  literal wording over `tag_clusters`'s own grouping key. Worth a explicit
  controller ruling if the two are meant to disagree.
- The no-look-ahead check's fixture-calibration process (parameter search)
  isn't itself codified as a repeatable script — it's a one-time tuning
  captured in the fixture's docstring and this report. If TESTS.md's
  bounds are ever revisited, that search would need to be redone by hand
  again rather than rerun from a saved script.

---

# Fix report: review findings on non-overlap

Commit: (pending — see below)

## Finding 1 — non-overlap grouped by the wrong key

**Fixed.** `_check_non_overlap` now groups cluster heads by `(ticker,
side)`, matching `research.candidates.tag_clusters`'s own grouping key
exactly (Ruling C5). The reviewer's argument is correct and is now
recorded in the function's own docstring: DESIGN §5.3's stated purpose for
`is_cluster_head` filtering is "non-overlapping, clean standard errors" —
avoiding double-counting *same-side* observations — not merging
opposite-side trades into one statistical unit, and the primary-statistics
consumer already separates cells by `signal_type`, which is itself
side-bearing (`confluence_low`/`bb_lower_touch` vs
`confluence_high`/`bb_upper_touch`). A long head and a short head land in
different cells, so the correlation concern behind my original ticker-only
grouping never arises. My prior defense of ticker-only grouping was wrong
and is retracted; the code that produced the data (`tag_clusters`'s own
key) settles the question.

**Practical effect of the bug**: a stock touching both bands in one choppy
window — normal, expected mega-cap behavior, and exactly what
`tag_clusters` itself produces as two independent single-event clusters —
was flagged as `cluster_head_windows_overlap`, which would have failed the
Phase 3 gate on correctly-produced data.

**Covering tests**, both directions per the review's requirement:
- `TestNonOverlap::test_a_long_head_and_a_short_head_overlapping_is_not_a_violation`
  — a long head and a short head on TSM, 1 trading bar apart (well inside
  `max_hold_days=5`) → `non_overlap.passed is True`. This is the exact
  scenario the bug flagged incorrectly; it now passes.
- `TestNonOverlap::test_same_side_overlap_is_still_caught_after_the_ticker_side_fix`
  — the converse, restated explicitly on `side="long"` for both events, 2
  trading bars apart → still caught, `reason ==
  "cluster_head_windows_overlap"`. Confirms the `(ticker, side)` fix did
  not weaken the check's original purpose.
- The pre-existing `test_catches_two_cluster_heads_whose_windows_overlap`
  (both events default to `side="long"` via `_event_row`) continues to
  pass unmodified, for the same reason.

## Finding 2 — non-overlap silently skipped tickers missing from `bars_by_ticker`

**Fixed.** A `(ticker, side)` group with `is_cluster_head` events but no
entry in `indexed_bars` now emits a violation
(`reason: "no_bars_for_ticker"`) instead of being skipped with `continue`.

**Option chosen: emit a violation, not just a `detail` note.** The review
offered a `detail`-only alternative (e.g. `tickers_skipped_no_bars`) as
acceptable *only if* a caller could distinguish "checked, clean" from "not
checked" from the report alone. I chose the violation because that
distinction is exactly what `passed` already means for every other check
in this module: `_check_entry_sanity` and `_check_exit_sanity` treat the
identical condition (a row needing a bar that doesn't exist) as a
violation (`no_bar_for_entry_date` / `no_bar_for_exit_date`), not a detail
note. A `detail`-only fix would have made `non_overlap` the only one of
the five checks where "the input was incomplete" and "the input was
checked and clean" both read as `passed=True`, differing only if a caller
remembers to inspect `detail` — which is precisely the "check that cannot
fail" failure mode this task exists to prevent. Emitting a violation keeps
all five checks consistent: `passed=False` whenever the check could not
actually verify what it claims to verify. `detail["n_groups_no_bars"]`
still exists alongside the violation, for a caller that wants the count
without parsing violation reasons.

**Covering test**:
`TestNonOverlap::test_a_ticker_with_heads_but_no_bar_data_is_a_violation_not_a_silent_skip`
— two cluster-head events on TSM against an empty `bars_by_ticker` ({}) →
`non_overlap.passed is False`, `"no_bars_for_ticker"` present among the
violation reasons, `detail["n_groups_no_bars"] == 1`.

## Minor fixes

- **Empty-`events` coverage for all four events-dependent checks**: added
  `TestEmptyEvents::test_a_genuinely_empty_events_frame_vacuously_passes_the_four_events_checks`,
  asserting `entry_sanity`, `exit_sanity`, `return_identity`, and
  `non_overlap` all report `passed=True` with a `detail` that says zero
  rows were examined (`{"n_priced": 0, "n_validated": 0}` for entry/exit,
  `{"n_checked": 0}` for return identity, `{"n_heads": 0,
  "n_pairs_checked": 0, "n_groups_no_bars": 0}` for non-overlap) — not
  exercised only incidentally through `TestNoLookahead`'s `_events([])`
  anymore.
- **Ticker entirely absent from `bars_by_ticker`, entry/exit sanity
  specifically**: added
  `TestEntrySanity::test_ticker_entirely_absent_from_bars_by_ticker_is_a_violation`
  and the exit-sanity equivalent — both assert `passed=False` with the
  existing `no_bar_for_entry_date`/`no_bar_for_exit_date` reason (the code
  path was already shared with the "date missing, ticker present" case via
  `_bar_row`'s `indexed.get(ticker)` returning `None` either way; this is
  now asserted directly instead of only implied).
- **`n_checked` ambiguity**: split into two `detail` keys on
  `entry_sanity`/`exit_sanity`: `n_priced` (rows with a non-null
  entry/exit price — what invariant 4 permits this check to look at) and
  `n_validated` (the subset that actually found a fill bar and was
  compared against it). A row hitting `no_bar_for_entry_date` now
  increments `n_priced` but not `n_validated`, so the two numbers answer
  "how many rows could this check have examined" and "how many did it
  actually compare" separately, rather than one count doing both jobs
  under one name. `return_identity`'s `n_checked` is unchanged — it has no
  analogous bar lookup, so the ambiguity didn't apply there.

## Commands and output

```
$ .venv/Scripts/python.exe -m pytest capitalscan/tests/unit/test_backtest_harness.py -v
...
24 passed in 1.84s

$ .venv/Scripts/python.exe -m ruff check capitalscan/research/harness.py capitalscan/tests/unit/test_backtest_harness.py
All checks passed!

$ .venv/Scripts/python.exe -m pytest capitalscan/tests/unit capitalscan/tests/property -q
...
657 passed in 28.38s
```

(Never ran bare `pytest`; never touched `capitalscan/tests/integration/`.)
