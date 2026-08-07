# Task 8 report: Costs, context tagging, split assignment

## What I implemented

`enrich_context(event, ind_row, market_row, sp, splits, cp) -> dict` in
`capitalscan/research/enrich.py`, plus three private helpers:
`_dd_bucket_label`, `_era`, `_earnings_in_window`.

Deviation from the brief's one-line interface sketch: I added `cp:
CostParams` as a sixth parameter. `core.costs.apply_costs` requires a
`CostParams` to compute slippage/commission/borrow, the brief's signature
line did not list one, and no other parameter in scope can supply a cost
schedule. Rather than fabricate a default `CostParams()` inside the
function (which would silently apply one specific cost schedule regardless
of what the caller configured — invariant 9's spirit), I added it as a
required argument, matching the convention `resolve_entries` already uses
for the same reason. Flagging this explicitly rather than treating it as
settled.

`enrich_context` computes:
- `gross_ret` via `core.returns.realized_return(entry_price, exit_price,
  side)` — reads `event["entry_price"]`, `event["exit_price"]`,
  `event["side"]`.
- `net_ret` via `core.costs.apply_costs(gross_ret, side, holding_days, cp,
  entry_price=entry_price)`, null when `gross_ret` is null or
  `event["holding_days"]` is `None` (unresolved position).
- `dd_bucket` via `_dd_bucket_label(ind_row.get("dd_52w"), sp)`.
- `bw_regime`: always `None` (see below).
- `era` via `_era(signal_date, sp, splits)`.
- `earnings_in_window` via `_earnings_in_window(ind_row.get("days_to_earnings"),
  holding_days)`.
- `split_key` via `split_key_for(signal_date, splits)`, re-exported from
  `research/backtest.py` (Ruling C2) — imported at module top, not
  reimplemented.
- `vix_close` / `spx_ret_1d` from `market_row`, `None` when `market_row is
  None`, matching `jobs/compute.py:679-680`'s existing pattern exactly.

`event["side"]` is accepted as either a `Side` enum or the lowercase string
form (`"long"`/`"short"`) `research/candidates.py` and `resolve_entries`'s
own candidate dicts use — same tolerance `resolve_entries` already applies.

## What I tested and results

New file: `capitalscan/tests/unit/test_backtest_context.py`, 37 tests in 7
classes:

- `TestCostsAlwaysSubtract` (6 tests) — losing/winning long and short, plus
  a direct short-vs-long borrow-cost comparison, plus the unresolved-position
  null case.
- `TestDdBucketMatchesCompute` (6 tests) — parametrized over 9 representative
  `dd_52w` values plus `None`/`NaN`/`0.40` (fallback), each asserted equal to
  `jobs.compute._dd_bucket`'s own output; one test with a custom
  `StatsParams.dd_buckets` proving the labels move with config.
- `TestSplitKeyMatchesTask2` (6 tests, parametrized) — every `split_key_for`
  boundary from `test_backtest_config.py`, plus the below-`event_start` raise.
- `TestEarningsInWindow` (6 tests) — null-not-False on unknown
  `days_to_earnings`, in-window true, boundary true, out-of-window false,
  null on unresolved `holding_days`.
- `TestEra` (3 tests) — all four ADR 042 eras at both boundaries, plus two
  tests with custom `StatsParams.era_bounds`/`SplitParams.event_start`
  proving the labels are derived, not literal.
- `TestBwRegime` (1 test) — asserts `None`.
- `TestMarketRow` (2 tests) — `None` frame and populated frame.

Full run: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`
→ **587 passed**, no regressions in the pre-existing 550.

## TDD evidence

**RED** — `uv run pytest capitalscan/tests/unit/test_backtest_context.py -q`:

```
ImportError while importing test module '...test_backtest_context.py'.
E   ImportError: cannot import name 'enrich_context' from 'capitalscan.research.enrich'
=========================== short test summary info ===========================
ERROR capitalscan/tests/unit/test_backtest_context.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

Expected: `enrich_context` did not exist yet, so import fails before any test
body runs — proves the tests were written and executed before implementation.

**GREEN** — `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`:

```
capitalscan\tests\unit\test_backtest_context.py ........................
.............
...
============================ 587 passed in 25.00s =============================
```

## Files changed

- `capitalscan/research/enrich.py` — added `enrich_context`,
  `_dd_bucket_label`, `_era`, `_earnings_in_window`; added imports
  (`SplitParams`, `StatsParams`, `split_key_for` from
  `capitalscan.research.backtest`); updated module docstring for step
  10-12 / Task 8 completion.
- `capitalscan/tests/unit/test_backtest_context.py` (new) — 37 tests, 7
  classes, described above.

Commit: `778ac57` — "Add enrich_context: costs, context tagging, split
assignment (Task 8)". (`docker-compose.yml` had an unrelated pre-existing
modification from before this task started; left uncommitted, not touched.)

## dd_bucket label derivation

`_dd_bucket_label` walks `sp.dd_buckets` (default `(0.10, 0.20, 0.35)`) and
builds each label as `f"{round(prev*100)}-{round(threshold*100)}"`, with
`prev` starting at `0.0` and advancing to each threshold in turn; the
fallback above the last threshold is `f"{round(prev*100)}+"`. `round()`
(not truncation) guards against `0.10 * 100 == 10.000000000000002` in
binary float, the same guard `_pct_suffix` already uses.

This reproduces `jobs.compute._dd_bucket`'s hardcoded
`DD_BUCKETS = ((0.10, "0-10"), (0.20, "10-20"), (0.35, "20-35"))` + `"35+"`
fallback exactly, but derives the strings from `StatsParams.dd_buckets`
rather than restating them — `jobs/compute.py`'s own `DD_BUCKETS` constant
is a second, independently-typed source of the same three numbers, which
is the pre-existing divergence risk the brief calls out (not something
this task fixes on the `compute.py` side; out of scope).

Test proving parity: `test_backtest_context.py::TestDdBucketMatchesCompute`
imports `jobs.compute._dd_bucket` directly and asserts
`enrich_context(...)["dd_bucket"] == _dd_bucket(dd_52w)` for
`dd_52w in [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 1.0]`, plus
separate `None`/`NaN` tests and an explicit `"35+"` fallback check.

## bw_regime: returned None, unimplemented

DESIGN §6.7 (the headline-grid section) states directly: "Everything else
stays continuous and feeds the model. Bandwidth regime, trend, VIX
percentile, and days-to-earnings are features, not cells." That is the only
place in DESIGN.md or DECISIONS.md that discusses bandwidth regime as a
concept, and it says explicitly this stays a *continuous* signal for the
model, never bucketed into categories the way `dd_bucket` is. There is no
`StatsParams` field for regime thresholds, no ADR pinning them, and
`indicators.bb_width_pct` (the raw input a bucketing would key off) has no
documented percentile or absolute cutoffs anywhere.

I considered percentile-binning `bb_width_pct` against its own trailing
history (a "squeeze vs. expansion" regime is a natural reading of
"bandwidth regime"), but any such bucketing needs a window length and
percentile cutoffs — both would be numbers I invented, landing exactly in
invariant 9's "no magic numbers outside `core/config.py`" territory, and
the task brief explicitly instructs: "if you cannot justify a definition
from the docs, return `None` and report that it is unimplemented rather
than inventing a bucketing." I did that. `events.bw_regime` stays `text`
in the schema and gets written as `NULL` by this function until a future
ADR pins the definition and adds a `StatsParams` field for it.

## earnings_in_window window definition

Based on ADR 036 (`docs/DECISIONS.md:1518`): "A 5-day window containing an
earnings report is contaminated regardless of session."

`_earnings_in_window` compares `ind_row["days_to_earnings"]` (calendar days
to the next scheduled report, computed once in
`jobs.compute._merge_days_to_earnings`, always `>= 0` or null — negative
values, i.e. a report already passed, are masked to null by that function's
`.where(idx <= next_report)`) against `event["holding_days"]` — the actual
resolved holding length of *this* trade for *this* exit config, not a
literal 5 or `ExitParams.max_hold_days`.

Reasoning for using `holding_days` rather than `max_hold_days`:
`enrich_context`'s signature (as pinned in the brief) does not receive
`ExitParams`, and `event` — built upstream by steps 7-9 before
`enrich_context` runs — already carries the real, resolved holding period.
A position that exited early on a stop-loss on day 2 was never exposed to
a day-4 earnings report; checking against the trade's actual open window is
a more precise reading of "contaminated" than checking against the
theoretical maximum every trade could have held for. Returns `None` (not
`True`/`False`) whenever `days_to_earnings` is null or `holding_days` is
`None` — never `False` for an unknown, per invariant 4 and the brief's own
framing that an unknown is not a negative. This matters because
`days_to_earnings` is unreliable before ~2014 (thin SEC 8-K coverage), so a
large share of early events would otherwise get a fabricated "clear."

## Costs subtract on both sides — confirmation

`net_ret` always routes through `core.costs.apply_costs`, which the brief
states already applies slippage on both legs unconditionally and borrow on
shorts only. I did not reimplement or touch that function — only proved,
per the brief's requirement, that `enrich_context` calls it correctly for
both sides:

- `test_losing_long_gets_worse_with_costs` — gross `-0.10`, asserts
  `costed["net_ret"] < plain["gross_ret"]`.
- `test_losing_short_gets_worse_with_costs` — same assertion, short side
  (entry 100 → exit 110, a losing short).
- `test_winning_long_is_reduced_by_costs` / `test_winning_short_is_reduced_by_costs`
  — proves costs subtract even from a *winning* trade on both sides, not
  just losing ones (a stricter proof than the brief's minimum ask).
- `test_short_pays_strictly_more_than_an_identical_long_via_borrow` — same
  entry/exit/holding_days, opposite side, asserts the short's `net_ret` is
  strictly lower than the long's, isolating the borrow term.

All five pass with the real, non-zero `CostParams` fixture (`slippage_bps=3.0,
commission_per_share=0.01, borrow_bps_annual=40.0`).

## Self-review

- **Completeness**: all nine required output keys present in every code
  path, including the unresolved-position case (verified by
  `test_net_ret_is_nan_when_position_never_resolved`).
- **Naming**: `_dd_bucket_label`, `_era`, `_earnings_in_window` follow the
  existing module's underscore-private-helper convention
  (`_pct_suffix`, `_touch_level_or_nan`, `_as_date`).
- **YAGNI**: did not build a `bw_regime` bucketing scheme on speculation;
  did not add an `ExitParams` parameter just to use `max_hold_days` when
  the resolved `holding_days` already answers the question more precisely.
- **Tests verify real behavior, not tautology**: the dd_bucket tests import
  and call the actual `jobs.compute._dd_bucket` rather than restating its
  expected labels as string literals, so a future change to either
  function's boundary logic would break the test rather than being
  invisible to it. The cost tests use a real, non-zero `CostParams` and
  compare against a zero-cost baseline rather than asserting a specific
  numeric constant, so they'd catch a sign error regardless of the exact
  bps values.
- **No mutation**: `enrich_context` reads from `event`/`ind_row`/
  `market_row` and returns a new dict; nothing is mutated in place.
- **Ran the exact required command**: `uv run pytest capitalscan/tests/unit
  capitalscan/tests/property` (never bare `pytest`), per CONSTRAINTS.md.

## Issues or concerns

1. **`cp: CostParams` added to the signature.** Documented above and in the
   function's own docstring. This is the one place I diverged from the
   brief's literal interface line, because the alternative (a bare
   `apply_costs` call with a fabricated default `CostParams()`) would
   silently pick one specific cost schedule no matter what the actual run
   configured — worse than an explicit required parameter. If Task 9's
   caller wiring expects a different shape (e.g. `cp` folded into a wider
   `Config` object), that's a one-line signature change, not a redesign.
2. **`bw_regime` is unimplemented.** Returns `None` always. `events.bw_regime`
   stays `text` and nullable in the schema, so this does not break any
   downstream write — but any Phase 4 analysis that expects this column
   populated will need a follow-up ADR pinning the bucketing definition
   first.
3. **`era`'s open-ended label deviates from ADR 042's literal "2024-2026."**
   I label the last era `"2024+"` instead, to avoid hardcoding a year the
   code will silently outlive. If the team wants the literal ADR 042
   wording preserved verbatim regardless, that's a product decision I'm
   flagging rather than deciding unilaterally — happy to change to a fixed
   string if that's preferred, but doing so would reintroduce exactly the
   kind of stale literal invariant 9 warns about.
4. **`earnings_in_window` uses `event["holding_days"]`, not
   `ExitParams.max_hold_days`.** Documented above as the more precise
   reading given the available interface. If a future consumer wants
   "would this signal's fixed decision window touch an earnings report
   regardless of how the trade actually resolved," that's a different,
   also-defensible definition requiring `ExitParams` in the signature —
   flagging this fork in case it doesn't match what Task 9's orchestrator
   expects.

---

## Fix report: code review Finding 1 (`earnings_in_window` wrong window)

**Finding (Important):** `_earnings_in_window` compared `days_to_earnings`
against `event["holding_days"]` (the trade's *realized* exit length), but
`touched_5pct` / `day_touched_5pct` on the same row are computed by Task 7's
`path_metrics` over the FULL fixed `[t+1, t+max_hold_days]` window
regardless of when the exit actually fired (DESIGN section 5.6). Concrete
failure: a position stopped out on day 2, with earnings on day 4, got
`earnings_in_window = False` (since `4 > 2`) even though the row's own
reachability columns already reflect an earnings-driven move on days 3-5.
That is the exact silent inversion the task brief's autonomous-run findings
warned about: a contamination flag reading "clear" when a report was days
away.

**What I changed:**

- `_earnings_in_window(days_to_earnings, ep)` now takes `ep: ExitParams`
  instead of `holding_days: int | None`, and compares `days_to_earnings`
  against `ep.max_hold_days` — the fixed analysis window, read from config
  rather than a literal `5` (invariant 9).
- `enrich_context` gained a required `ep: ExitParams` parameter (in
  addition to the `cp: CostParams` parameter added in the original round,
  also absent from the brief's interface sketch) and passes it straight to
  `_earnings_in_window`. `event["holding_days"]` is no longer read for this
  computation at all — `gross_ret`/`net_ret` still use it correctly, since
  that reasoning (a trade stopped early wasn't exposed past its own exit)
  is correct for *returns*, just not for this flag.
- Null discipline is unchanged: `None` when `days_to_earnings` is itself
  unknown (`_isnan`). The "unresolved position" null case is gone, since
  the flag no longer depends on `holding_days` — a position's realized
  exit no longer bears on whether the *fixed* window contains a report.
- Rewrote both docstrings (`_earnings_in_window` and `enrich_context`) to
  explain the fixed-window reasoning and to record, in-line, why the
  earlier `holding_days` comparison was wrong and for whom it was right
  (`gross_ret`/`net_ret`, not this flag).

**Files touched:** `capitalscan/research/enrich.py`,
`capitalscan/tests/unit/test_backtest_context.py`.

**`_era`/`era`: untouched**, per the coordinator's instruction — Finding 2
goes to the human partner for a ruling between two pinned decisions and is
out of scope for this fix.

### Covering tests

`test_backtest_context.py::TestEarningsInWindow` rewritten:

- `test_null_when_days_to_earnings_is_null` / `test_not_false_when_days_to_earnings_is_null`
  — null discipline unchanged.
- `test_true_when_earnings_falls_inside_the_fixed_window`,
  `test_true_at_the_max_hold_days_boundary`,
  `test_false_when_earnings_falls_outside_the_fixed_window` — boundary
  behavior against `ep.max_hold_days` rather than `holding_days`.
- `test_true_when_position_never_resolved_but_earnings_is_within_the_fixed_window`
  — proves the flag no longer nulls out on an unresolved position, since it
  no longer depends on `holding_days` at all.
- `test_earnings_window_moves_with_ep_max_hold_days_not_a_literal` — a
  shrunk `max_hold_days=3` with `days_to_earnings=4` flips a previously
  in-window report to out-of-window, proving the comparison reads
  `ep.max_hold_days` rather than a hardcoded `5`.
- **`test_stopped_out_early_with_earnings_still_inside_the_fixed_window_is_true`**
  — the exact regression case from Finding 1: `holding_days=2`,
  `days_to_earnings=4`, default `ExitParams(max_hold_days=5)`. Asserts
  `earnings_in_window is True`. This is the test that distinguishes the two
  rules: it would have failed (returned `False`) under the pre-fix
  `holding_days` comparison and passes under the fixed `ep.max_hold_days`
  comparison.

### Commands and output

`uv run pytest capitalscan/tests/unit/test_backtest_context.py -v`:

```
collected 39 items
...
capitalscan/tests/unit/test_backtest_context.py::TestEarningsInWindow::test_stopped_out_early_with_earnings_still_inside_the_fixed_window_is_true PASSED [ 84%]
...
============================= 39 passed in 0.14s ==============================
```

`uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`:

```
============================ 589 passed in 25.10s =============================
```

No regressions; 2 net new tests (one old test replaced by three new ones
reflecting the corrected semantics, per the class rewrite above).
