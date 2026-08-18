# Harness fixes — `return_identity` tolerance and `non_overlap` dedupe

Scope: `capitalscan/research/harness.py` and `capitalscan/tests/unit/test_backtest_harness.py` only. `core/returns.py` and `entry_sanity` untouched — confirmed by full test run below and by the diff (`git diff --stat` shows only the two harness files changed by this work).

## Defect 1 — `return_identity`'s `1e-9` tolerance

**Fix:** added `_return_tolerance(entry_price, recomputed_gross_ret)` and used it in `_check_return_identity` in place of the flat `1e-9` comparison.

**Derivation, worked through.** `entry_price`/`exit_price` are `numeric(12,4)` (stored `E`, `X`, each rounded from the engine's float64 value, error `e_E, e_X`, `|e_E|,|e_X| <= 5e-5`). `gross_ret` is `numeric(12,6)`, independently rounded, `|e_G| <= 5e-7`. The harness only ever sees the rounded `E`, `X` — it recomputes `G_recomp = (X-E)/E` from *already-rounded* prices and compares to the *separately, already-rounded* `gross_ret`. Exact first-order propagation:

```
G_recomp - G_true = (e_X - e_E)/(E+e_E) - G_true * e_E/(E+e_E)
```

Replacing `E+e_E` with `E` (the correction is ~7e-6 relative at the dataset's minimum `E=$6.87`, itself second-order against the terms it would adjust) and bounding:

```
|G_recomp - G_true| <= 1e-4/E + |G_true| * 5e-5/E
```

Adding `gross_ret`'s own `5e-7` rounding budget:

```
tolerance(E, G) = 1e-4/E + |G| * 5e-5/E + 5e-7
```

**I did not use the diagnosis's suggested `1e-4/E + 5e-7` as-is.** My derivation keeps a `|G|*5e-5/E` term the diagnosis drops as second-order. At the dataset's observed minimum-`E` row (AAPL, `E=6.8817`, `G=0.04`) the term is `~2.9e-7` — under half the `5e-7` rounding budget, so dropping it is fine *for that row*. But it is not a bound that holds for every possible `G`; a future run with a larger realized return at a low entry price would make it non-negligible. Since it costs nothing to compute (it's already available as the recomputed return), I kept it so the tolerance is a genuine bound for any `G`, not one calibrated to what this run happens to measure. Verified against the diagnosis's worst row: my formula gives `1.532e-5` vs. the diagnosis's `1.503e-5`, both comfortably above the measured `9.881e-6` diff. Full derivation is in the `_return_tolerance` docstring in `harness.py`.

**TDD evidence.**

RED (`uv run pytest capitalscan/tests/unit/test_backtest_harness.py -k "numeric_12_4 or diff_larger"`), before the fix:
```
test_numeric_12_4_rounding_noise_at_a_low_entry_price_does_not_violate FAILED
  AssertionError: [{'reported_gross_ret': 0.04, 'recomputed_gross_ret': 0.039990118720664936,
                     'reason': 'gross_ret_mismatch'}]
test_a_diff_larger_than_the_derived_tolerance_is_still_a_violation PASSED  (already correct pre-fix)
```
The rounding-noise row failed under the old `1e-9` tolerance for exactly the diagnosed reason (diff `9.88e-6` >= `1e-9`).

GREEN (`uv run pytest capitalscan/tests/unit/test_backtest_harness.py -v`), after the fix: all 27 tests in the file pass, including both new tests.

**Test proving the check still catches genuine violations:** `test_a_diff_larger_than_the_derived_tolerance_is_still_a_violation` plants `gross_ret = recomputed + 5e-5` at the same low-`E` row (derived tolerance there is `~1.53e-5`, so `5e-5` is >3x the bound) and asserts `gross_ret_mismatch` still fires. The pre-existing `test_catches_a_gross_ret_that_disagrees_with_realized_return` (off by `0.5`) also still passes, unmodified.

## Defect 2 — `non_overlap` missing dedupe across entry kinds

**Fix:** in `_check_non_overlap`, before the sort/gap-walk, added `.drop_duplicates(subset="_signal_date")` after computing `_signal_date`, within each `(ticker, side)` group — one row survives per distinct `signal_date`; which of the (up to) four entry-kind rows survives doesn't matter since only `ticker`/`side`/`signal_date` drive the gap test.

**Reasoning:** confirmed via the diagnosis's exact math — `events`' grain is `(config_hash, ticker, signal_date, signal_type, entry_kind)`, so one signal produces four rows (`touch`, `touch_5m`, `touch_30m`, `next_open`) sharing `cluster_id`/`signal_date`/`is_cluster_head`. Without dedupe, four identical-date heads sort adjacently and the walk reports 3 zero-gap pairs per head — `3 x 532 = 1596`, exactly the reported violation count on the live run, with zero genuine cross-cluster overlaps left over. I did not implement the entry-lag ("NEXT_OPEN t+1 offset") theory the brief flagged as ruled out — the diagnosis shows the offset cancels identically between any two rows of the same entry kind, so it was correctly excluded from the fix.

**TDD evidence.**

RED (`uv run pytest ... -k duplicate_entry_kind`), before the fix:
```
test_duplicate_entry_kind_rows_at_one_head_do_not_count_as_overlap FAILED
  AssertionError: assert False
  non_overlap: 3 violations, all 'cluster_head_windows_overlap',
  first_head_date == second_head_date == 2026-01-05, trading_bar_gap=0
```
Four entry-kind rows for one head alone produced exactly 3 zero-gap violations — reproducing the diagnosed `3H` artifact shape at `H=1`.

GREEN: after adding the dedupe, the same test passes both halves — zero violations for one head's four duplicate rows, and exactly one violation when a second, genuinely overlapping head (2 trading bars later, itself also duplicated across all four entry kinds) is added. This proves the dedupe removes the artifact without swallowing a real cross-cluster overlap.

## `entry_sanity` — confirmed unchanged

`_check_entry_sanity` was not touched. Full run of `capitalscan/tests/unit capitalscan/tests/property` (741 tests) passes, including all `entry_sanity` tests, with no modification to that function or its tests in this session's diff (`git diff --stat` on the two files I touched shows only `harness.py` and `test_backtest_harness.py`; `core/returns.py` and `test_returns.py` are untouched by this work — `test_returns.py`'s pending modification belongs to the concurrent engine-bug fix).

## Concerns

- `_return_tolerance`'s `|G|` term uses the *recomputed* return, not the *stored* one — deliberate (avoids using the very value under test to size its own bound in a way that could mask a mismatch), but worth a second look if a reviewer wants the stricter choice of `max(|reported|, |recomputed|)`.
- The dedupe keeps an arbitrary surviving row per `(ticker, side, signal_date)` (whichever `entry_kind` `drop_duplicates` picks first, which is insertion order after the group's own ordering — not guaranteed stable across pandas versions in principle, though in practice deterministic for a given input). Since only `ticker`/`side`/`signal_date` are read past that point, this doesn't affect the check's output, only which row's other columns would show up in a violation dict — irrelevant here since deduped rows never appear in a violation.
- Did not touch DESIGN §5.10's `1e-9` documentation; the diagnosis recommends updating it there too — flagging in case that's expected as part of this task's scope (I read it as harness-only per the assignment).

## Verification commands

```
uv run pytest capitalscan/tests/unit/test_backtest_harness.py -v   # 27 passed
uv run pytest capitalscan/tests/unit capitalscan/tests/property    # 741 passed
```
