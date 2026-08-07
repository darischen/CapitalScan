# Task 5 report: Entry resolution, four kinds

## What I implemented

Created `capitalscan/research/enrich.py`, the new module the brief asked me
to start — its docstring names all of DESIGN §5.2 steps 7-11 (entry, exit,
path metrics, costs, context) as the file's eventual scope, with a note
that Task 5 only supplies step 7 (`resolve_entries`) and Tasks 6-8 add exit
resolution, path metrics, and cost/context tagging on top.

`resolve_entries(candidate: pd.Series, bars: pd.DataFrame, hourly:
pd.DataFrame | None, cp: CostParams) -> list[dict]`:

- Reads `ticker`, `side`, `signal_date`, `touch_level` off the candidate row
  (the shape `research/candidates.py` produces).
- Slices `bars` to the candidate's ticker and finds the signal's own bar
  (by calendar date, date-based indexing, matching the `candidates.py` /
  `compute.py` precedent already in the repo) plus the next session's bar,
  if any.
- Slices `hourly`, if given, to the candidate's ticker **and** the signal's
  own calendar day, before handing it to `entry_price_for` — an unscoped
  hourly frame would let an unrelated day's (or ticker's) breach silently
  win the "first breaching bar" search inside `core.returns._first_hourly_touch`.
- Calls `core.returns.entry_price_for` once per `EntryKind`, every price
  decision delegated there. Nothing in this module compares a price to a
  band level except the one `entry_gapped` computation described below,
  which routes through the same `core.signals._breach` the rest of the
  system uses.
- Applies slippage via `core.costs.slippage`, adverse to the side, on top
  of whatever price came back (skipped when the price is already NaN, so
  a NaN doesn't get spuriously "slipped" — though NaN + NaN offset would
  stay NaN either way, this makes the skip explicit rather than relying on
  float propagation).
- Shapes one dict per kind: `entry_kind`, `entry_date`, `entry_price`,
  `entry_gapped`.

## What I tested and results

16 new tests in `capitalscan/tests/unit/test_backtest_entry.py`, covering:

- Shape: exactly one row per `EntryKind`.
- `TOUCH` gap rule, long and short, both directions (fills at band when
  not gapped, at open when gapped, `entry_gapped` set correctly both ways).
- `NEXT_OPEN`: normal case (next session's open), terminal-bar null price
  and null `entry_date` (not the current close), and `entry_gapped is None`
  (not applicable — see decision below).
- `TOUCH_5M`/`TOUCH_30M`: NaN price when `hourly is None`, row still
  produced; correct price from a real hourly frame; correct scoping to the
  signal day (a prior day's hourly bars that would also "breach" the level
  must not win).
- Slippage: raises the long's fill, lowers the short's fill, applies to
  `NEXT_OPEN` too, magnitude checked exactly against `slippage_bps`.
- `touch_level = None` (the stochastic-only-signal case) does not raise
  and produces NaN price / `entry_gapped = None` for `TOUCH`.
- A missing signal bar raises `ValueError` (caller-side input mismatch,
  distinct from the terminal-bar case which is a valid null).

### TDD Evidence

**RED** — before `enrich.py` existed:

```
uv run pytest capitalscan/tests/unit/test_backtest_entry.py -q
```

```
ERROR collecting capitalscan/tests/unit/test_backtest_entry.py
ImportError while importing test module ...test_backtest_entry.py.
...
E   ModuleNotFoundError: No module named 'capitalscan.research.enrich'
=========================== short test summary info ===========================
ERROR capitalscan/tests/unit/test_backtest_entry.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.63s ===============================
```

Expected: the test file imports `resolve_entries` from a module that does
not exist yet, so collection fails with `ModuleNotFoundError` — the right
kind of failure for "implementation doesn't exist," not a logic bug.

**GREEN** — after implementing `enrich.py`:

```
uv run pytest capitalscan/tests/unit/test_backtest_entry.py -q
```

```
collected 16 items
capitalscan\tests\unit\test_backtest_entry.py ................           [100%]
============================= 16 passed in 0.10s ==============================
```

**Full safe suite** (unit + property, the only sanctioned invocation):

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
```

```
collected 525 items
... (all green) ...
============================ 525 passed in 25.16s =============================
```

No integration tests were run (forbidden — they truncate live tables).

## Files changed

- Created: `capitalscan/research/enrich.py`
- Created: `capitalscan/tests/unit/test_backtest_entry.py`

## `entry_gapped` for `NEXT_OPEN`

Set to `None`, not `False`. `NEXT_OPEN` prices off the *next* session's
open and never compares anything to a band level — there is no "did it gap
past the band" question to answer for that fill. `False` would misreport
"checked, and it didn't gap"; `None` honestly says the check doesn't apply.
This mirrors the same reasoning `core.types.SignalHit.touch_level` already
uses for `None` vs `NaN` (never fabricate a value where the honest answer
is "not applicable" / "no data").

## `touch_level = None` handling

`_touch_level_or_nan` normalizes `candidate["touch_level"]` to `float("nan")`
via `core.signals._isnan`, which tolerates `None`. This single conversion
happens once, before any of the four `entry_price_for` calls and before the
one `entry_gapped` computation this module makes directly (`core.signals._breach`
also needs an actual float `nan`, not `None`, from the same tolerance).
`entry_price_for` itself then sees a real `NaN` for `TOUCH` and returns
`NaN` through its own existing `_isnan(touch_level)` check — no new logic
added there, no second gap rule written.

## Slippage moves the price adversely on both sides

Confirmed by `test_slippage_raises_the_fill_price_for_a_long` and
`test_slippage_lowers_the_fill_price_for_a_short` in
`test_backtest_entry.py`: same bar, same touch level, compared with
`CostParams(slippage_bps=0.0)` against the default `CostParams()`
(`slippage_bps=3.0`). The long's slipped price is strictly greater and
matches `plain * (1 + bps/1e4)` exactly; the short's slipped price is
strictly less and matches `plain * (1 - bps/1e4)` exactly. A third test
(`test_slippage_applies_to_next_open_too`) confirms slippage is not
special-cased to `TOUCH` alone.

## Self-review findings

- Completeness: all four `EntryKind` values always produced, per the
  brief's explicit requirement that `TOUCH_5M`/`TOUCH_30M` rows survive
  even with `hourly is None`.
- Naming: matches the brief's pinned interface and field names exactly
  (`entry_kind`, `entry_date`, `entry_price`, `entry_gapped`).
- YAGNI: did not add exit resolution, path metrics, or cost/context
  columns — those are Tasks 6-8, called out as such in the module
  docstring so a later reader knows this is deliberate scoping, not an
  oversight.
- No second band comparison: the only place this module calls `_breach`
  directly is the one `entry_gapped` computation, which is the same
  open-vs-level check `entry_price_for`'s own `TOUCH` branch already makes
  — not a second implementation of the gap *rule* (which still lives
  entirely inside `entry_price_for`), just a re-read of the same fact for
  reporting purposes.
- Do the tests verify real behavior: yes — each test asserts on the actual
  numeric fill price and boolean flag, not just "did not raise," and the
  hourly-scoping test specifically constructs a decoy prior-day bar that
  would produce a wrong-but-plausible answer if the day-scoping were
  missing.
- Output cleanliness: no debug prints, no commented-out code.

## Issues or concerns

None. One judgment call worth flagging to the controller if it disagrees:
I attach `entry_gapped` (computed once from the bar's own open vs. the
band) to `TOUCH_5M` and `TOUCH_30M` as well as `TOUCH`, on the reasoning
that "did the signal day gap past the band" is a fact about the day, not
about which entry-timing kind is being priced, and is worth carrying even
when the hourly fill price itself is NaN for lack of hourly coverage. If
the intended semantics are narrower (`entry_gapped` meaningful for `TOUCH`
only), that's a one-line change to gate it the same way `NEXT_OPEN` is
gated.
