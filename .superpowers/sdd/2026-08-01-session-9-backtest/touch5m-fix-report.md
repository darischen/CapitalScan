# TOUCH_5M gap-day fix — report

Commit: `f6dd524`, on `core/returns.py` and its unit tests only. HEAD was `c5e588f`.

## Root cause, confirmed

Both the selection and the anchor were examined. Only the anchor was wrong.

`_first_hourly_touch` scans hourly bars for the first one whose extreme (low
for long, high for short) reaches `touch_level`. On a gap day the level sits
outside the whole session's range, so the very first hourly bar already
satisfies that condition trivially — its low (long) or high (short) clears a
level the market gapped straight through before the session opened. That
selection is **correct**: the first hourly bar of the session genuinely is
where a fill would happen, exactly the same reasoning `TOUCH`'s gap rule uses
for the daily open. I checked this against the traced MSFT 2025-07-31 short
example and against my own constructed gap cases: in every case the selected
bar is the session's first bar, and that is the right bar to fill from.

The anchor was wrong. `entry_price_for` interpolated
`touch_level + (close - touch_level) * (5/60)`, unconditionally anchoring on
`touch_level` even when that level never traded inside the selected bar (or
anywhere that session). Because the weight toward `close` is small (0.083),
the result stays close to `touch_level`, which on a gap day sits outside the
bar's own `[low, high]` and therefore outside the daily bar's range too.

## The fix

Applied DESIGN §5.4's TOUCH gap principle one level down, at the hourly bar
instead of the daily bar:

```python
open_ = float(hbar["open"])
bound = Bound.LOWER if side is Side.LONG else Bound.UPPER
anchor = open_ if _breach(open_, float(touch_level), bound) else float(touch_level)
weight = _TOUCH_5M_MINUTES / _MINUTES_PER_HOURLY_BAR
return anchor + (close - anchor) * weight
```

If the hourly bar's own open already breached `touch_level` (in the gapped
direction), the level was never available inside that bar either — the only
honest anchor is the open, a price that actually traded. Otherwise the bar
genuinely straddles the level intrabar, and `touch_level` remains the correct
anchor, unchanged from before.

This keeps the fill a convex combination of two real numbers on the gap path
(`open` and `close`, both inside `[low, high]` by construction), and of
`touch_level` and `close` on the straddle path — same as before, and
`touch_level` is provably inside `[low, high]` whenever the bar straddles it
(the case the old code got right). Either way the result stays within the
hourly bar's range, and routes through `core.signals._breach`, not a new
comparison (invariant 2).

Reused `EntryKind.TOUCH`'s wording ("never traded... invents a better price")
verbatim in the new comment, since it's the same principle one level down.

## TDD evidence

RED — regression tests against the original code (`core/returns.py` at
`c5e588f`, tests added on top):

```
uv run pytest capitalscan/tests/unit/test_returns.py -k "gap" -v
...
test_touch_5m_long_fill_stays_inside_the_hourly_bar_on_a_gap FAILED
    assert 88.0 <= price <= 93.0
    E   assert 94.75 <= 93.0
test_touch_5m_short_fill_stays_inside_the_hourly_bar_on_a_gap FAILED
    assert 108.0 <= price <= 112.0
    E   assert 108.0 <= 105.33333333333333
2 failed, 2 passed, 25 deselected
```

Both failures are the exact defect: the old formula lands outside the
hourly bar's `[low, high]`.

GREEN — same tests against the fix:

```
uv run pytest capitalscan/tests/unit/test_returns.py -v
29 passed
```

## Over-broadness test

`test_touch_5m_short_still_interpolates_from_the_band_on_a_genuine_straddle`
constructs an hourly bar where `open` (100) has *not* breached `touch_level`
(102) for a short, but the bar's high (103) does. It asserts the result
equals the exact old formula (`102 + (101 - 102) * 5/60`), proving the
straddle path is byte-for-byte unchanged. Combined with the pre-existing
`test_touch_5m_interpolates_between_the_band_and_that_bars_close` (long
straddle, also unchanged and still passing), both sides of the non-gap path
are covered.

## Effect on TOUCH_30M

None. `TOUCH_30M` returns `close` directly, before the branch that computes
`open_`/`anchor`/`weight` — that code is only reached when `kind is
EntryKind.TOUCH_5M`. `test_touch_30m_uses_the_close_of_the_first_breaching_hourly_bar`
and `test_hourly_kinds_pick_the_first_breaching_bar_not_the_deepest` (which
runs against `TOUCH_30M`) both still pass unmodified.

## Existing tests

None changed. All 29 tests in `test_returns.py` pass, including every
pre-existing one, unmodified.

## Full suite

`uv run pytest capitalscan/tests/unit capitalscan/tests/property`:
739 passed, 1 failed. The failure is
`TestReturnIdentity::test_numeric_12_4_rounding_noise_at_a_low_entry_price_does_not_violate`
in `capitalscan/tests/unit/test_backtest_harness.py`. This file and
`capitalscan/research/harness.py` are both outside this task's scope (owned
by the concurrent agent fixing the harness `return_identity` tolerance bug
per the diagnosis doc) and were already modified in the working tree before
I started — not touched by this commit. Confirmed via `git status` that only
`core/returns.py` and `test_returns.py` are in my commit.

## Concerns

- `_first_hourly_touch` still iterates hourly bars with `.iterrows()` and a
  Python-level loop; unrelated to this bug, left as-is per scope.
- The fix assumes `hbar["open"]` is always present and within `[low, high]`
  on real hourly data. If upstream hourly ingestion ever produced a bar
  where `open` falls outside its own `[low, high]` (a data quality bug, not
  a pricing one), the invariant this fix establishes would not hold — that
  would need to be caught by a bar-validity check elsewhere, not re-derived
  here.
- I did not verify against live Postgres data (out of scope per the SAFETY
  section — SELECT-only, and I judged it unnecessary since the unit tests
  reproduce the exact traced MSFT example's mechanism). If a maintainer
  wants to re-run `entry_sanity` against `config_hash='3e598c59e7d71eae'`
  once the harness fixes land, that would give live confirmation of zero
  `touch_5m` survivors.
