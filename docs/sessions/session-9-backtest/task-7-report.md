# Task 7 report: Path metrics

## What I implemented

`path_metrics(entry_price, side, fwd_bars, exit_idx, exit_price, targets, adj_close_fwd, horizons) -> dict`
in `capitalscan/research/enrich.py`, producing `mfe`, `mae`, `time_to_mfe`,
`capture_ratio`, `touched_2pct`/`touched_3pct`/`touched_5pct`/`touched_10pct`,
`day_touched_2pct`/.../`day_touched_10pct`, and `fwd_ret_1d`/`fwd_ret_2d`/
`fwd_ret_3d`/`fwd_ret_5d`/`fwd_ret_10d`.

Two windows, per DESIGN §5.6:
- MFE/MAE via `core.returns.mfe_mae` over `fwd_bars.iloc[:exit_idx + 1]`
  (`exit_idx` is 0-based *within* `fwd_bars`, the same convention
  `ExitResult.exit_idx` / Task 6's `resolve_exit_for_entry` already use —
  `fwd_bars` itself already starts at t+1).
- Reachability over the full, un-sliced `fwd_bars`, via `core.signals._breach`
  (no second band comparison — invariant 2).

`capture_ratio = realized_return(entry_price, exit_price, side) / mfe`, null
when `mfe <= 0` (not `< 0`) or `mfe` is NaN.

`exit_idx is None` (unresolved position — never filled, or the forward
window was empty) nulls every exit-dependent field (`mfe`/`mae` -> NaN,
everything else -> `None`), matching the `_unresolved_exit` convention
already in this file. `fwd_ret_*d` is the one field computed regardless,
since DESIGN calls it "unconditional... for baseline comparison" — it
depends only on the price series, not on this entry ever filling.

## Files changed

- `capitalscan/research/enrich.py` — added `_pct_suffix` and `path_metrics`;
  imported `forward_returns`, `mfe_mae`, `realized_return` from
  `core.returns`; updated the module docstring's Task 7 line.
- `capitalscan/core/config.py` — added `StatsParams.fwd_ret_horizons =
  (1, 2, 3, 5, 10)`. Not in the brief's file list; justified below.
- `capitalscan/tests/unit/test_backtest_path.py` — new, 13 tests.

## TDD evidence

**RED** — `uv run pytest capitalscan/tests/unit/test_backtest_path.py -q`,
before `path_metrics` existed:

```
ImportError while importing test module '...test_backtest_path.py'.
E   ImportError: cannot import name 'path_metrics' from 'capitalscan.research.enrich'
=========================== short test summary info ===========================
ERROR capitalscan/tests/unit/test_backtest_path.py
```
Failed for the right reason: the function under test didn't exist yet.

**GREEN** — same command, after implementation (one intermediate failure in
`test_reachability_short_uses_lows_against_a_level_below_entry` was a bad
fixture on my part — the low sequence didn't touch −5% until bar 3, not bar
2 as I'd asserted; fixed the fixture, not the code):

```
capitalscan\tests\unit\test_backtest_path.py .............               [100%]
============================= 13 passed in 0.09s ==============================
```

Full safe suite: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`
→ **550 passed** (no regressions).

## Column-name derivation from `reach_targets`

`_pct_suffix(target) = f"{round(target * 100)}pct"`. `round()`, not
truncation or a raw f-string on the float, because `0.10 * 100 ==
10.000000000000002` in binary floating point — formatting that directly
would write `touched_10.000000000000002pct`, a column that doesn't exist.
For the default `StatsParams.reach_targets = (0.02, 0.03, 0.05, 0.10)` this
yields exactly `2pct, 3pct, 5pct, 10pct`, proven by
`test_reach_target_column_names_match_the_events_schema`, which asserts all
eight `touched_*`/`day_touched_*` names are present in the output dict.

## `fwd_ret_10d` window resolution

The pinned signature (`path_metrics(entry_price, side, fwd_bars, exit_idx,
targets) -> dict`) is insufficient, as flagged in the task: `fwd_bars` is
bounded at `ExitParams.max_hold_days` (5), and `fwd_ret_10d` needs a 10-bar
horizon `forward_returns` genuinely can't compute from that frame — not a
matter of slicing more carefully, the data isn't there.

Resolution: two new parameters.
- `adj_close_fwd: pd.Series | None` — total-return adjusted close (DESIGN
  §2.2: `forward_returns` measures return, dividends are real return),
  whose `iloc[0]` is the entry bar's own close and which must extend
  forward at least `max(horizons)` bars. `path_metrics` calls
  `forward_returns(adj_close_fwd, list(horizons))` and reads only its first
  row — no second `shift(-h)/close - 1` implementation.
- `horizons: tuple` — sourced from a new config field,
  `StatsParams.fwd_ret_horizons = (1, 2, 3, 5, 10)`.

**Why `core/config.py` changed** (outside the brief's file list): invariant
9 forbids a magic-number literal `[1, 2, 3, 5, 10]` inside `enrich.py`.
`reach_targets` already set the precedent for exactly this category of
value — a tuple that both drives sweep/output logic *and* names schema
columns — living in `StatsParams`, so `fwd_ret_horizons` follows the same
pattern rather than inventing a second convention. This is scope beyond
what the brief listed; flagging it explicitly rather than silently editing
a file outside the stated diff.

Caller obligation this creates (for whichever Task 8/9 orchestrator wires
`path_metrics` up): it must slice the per-ticker adjusted-close series
starting at the entry bar's position and hand over enough rows to cover
horizon 10, separately from the `fwd_bars` window it slices for
`resolve_exit_for_entry`. `test_fwd_ret_is_null_at_the_tail_never_filled`
documents the truncated-series case (fewer rows than a horizon needs -> NaN
for that horizon only, per `forward_returns`'s own existing contract).

## `capture_ratio`'s `R_exit`

Taken via `core.returns.realized_return(entry_price, exit_price, side)`,
never recomputed by hand (invariant 2 — one realized-return implementation).
This required adding `exit_price: float` as an explicit parameter to
`path_metrics` (also not in the pinned signature, also necessary — DESIGN
§5.6 defines `η = R_exit / MFE`, and `R_exit` cannot be derived from
`entry_price`, `side`, and `fwd_bars`/`exit_idx` alone; the caller already
has `resolve_exit_for_entry`'s `exit_price` in hand and is the natural
source).

## Reachability test — exit before the touch

`test_reachability_uses_full_window_past_an_early_exit`: 5 forward bars,
`exit_idx=1` (exit fires on the 2nd bar the position was open for — the MFE
window is bars 1-2, highs `[101.0, 102.0]`, giving `mfe == 0.02`). A +5%
touch (level 105.0) sits only on bar 4 (`high=105.5`); bars 1-2 never reach
it. The test asserts `touched_5pct is True` and `day_touched_5pct == 4`,
then separately asserts `mfe == pytest.approx(0.02)` — proving the MFE
window genuinely never saw the touch that reachability still finds. This is
the brief's own pinned fixture shape (exit on bar 2, touch on bar 4), not a
same-bar or end-of-window case that would pass even with a scoping bug.

## `day_touched_*` is `None`, not `NaN`, when never touched

`test_reachability_target_never_touched_is_false_with_null_day` asserts
`result["day_touched_2pct"] is None` and
`not isinstance(result["day_touched_2pct"], float)` — a real NaN would pass
an `== None` check as False but this explicitly rules out a float NaN
slipping through, since `events.day_touched_2pct` is `integer` in the
schema and NaN is a write error there, not a null. The implementation
initializes `day: int | None = None` and only ever assigns a Python `int`
(`day_number`) to it, never `np.nan`, so there's no code path that could
produce a float NaN here — verified structurally, not just by the assert.

## Self-review

- **Completeness**: all fields the brief lists are present; verified via
  `test_reach_target_column_names_match_the_events_schema` and
  `test_fwd_ret_columns_present_for_every_configured_horizon`.
- **Naming**: `_pct_suffix`, `path_metrics`, `adj_close_fwd`, `held_window`
  read consistently with this module's existing `_touch_level_or_nan`,
  `_unresolved_exit`, `_assert_entry_idx_matches` style.
- **YAGNI**: no caching, no vectorized-but-unused alternate reachability
  path, no speculative parameters beyond the three genuinely required
  additions (`exit_price`, `adj_close_fwd`, `horizons`), each justified
  above and in the docstring.
- **Tests verify real behavior, not tautology**: every reachability/MFE
  assertion carries a hand-computed expected value in a comment (e.g. "MFE
  over bars 0-1 -> max high 106 -> MFE = 0.06... capture_ratio = 0.5"), not
  just presence/absence checks.
- **Pristine output**: `ruff check` on all three changed files passes
  (caught and fixed one E501 in a test literal, committed separately —
  `4d474f1`).

## Issues or concerns

- The pinned `path_metrics` signature in the task brief and in
  `docs/superpowers/plans/2026-08-01-session-9-backtest.md` line 208 is now
  stale (it omits `exit_price`, `adj_close_fwd`, `horizons`). Whoever wires
  Task 8/9's caller needs to read this module's actual signature, not the
  plan doc's.
- `core/config.py` gained a field outside this task's stated file list.
  Flagged above; I judged it the only way to satisfy invariant 9 without
  inventing a second, undocumented convention for where such tuples live.
  If the controller prefers `fwd_ret_horizons` to be a plain parameter
  supplied ad hoc by the caller instead of a config field, that's a small
  revert — but it would leave the horizon values with no single source of
  truth, which invariant 9 exists to prevent.
- No caller wires `path_metrics` into a database/events row yet — that's
  Task 8/9's job per the module docstring's own roadmap; this task is
  correctly scoped to the pure function only.
