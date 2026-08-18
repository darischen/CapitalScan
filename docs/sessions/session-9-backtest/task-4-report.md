# Task 4 report: Cluster tagging

## What I implemented

Added `tag_clusters(candidates: pd.DataFrame, max_hold_days: int, trading_dates: dict[str, list[date]]) -> pd.DataFrame` to `capitalscan/research/candidates.py`, plus three private helpers:

- `_deterministic_cluster_id(*parts) -> int` — sha256-based stable id, same construction as `jobs/compute.py:_deterministic_id`, kept as a separate six-line copy rather than a cross-package import (candidates.py must stay importable with zero side effects for the Windows spawn rule, and `compute.py` is a `jobs/` module).
- `_trading_bars_between(dates, start, end) -> int` — counts entries of a sorted, deduplicated date list strictly after `start` and up to/including `end`, via `bisect` (O(log n) per event).
- `_as_date(value) -> date` — defensive `Timestamp` → `date` coercion, matching the pattern already used in `apply_eligibility`/`debounce`.

`tag_clusters` groups candidates by `(ticker, side)`, sorts each group by `signal_date`, and walks forward opening a new cluster whenever the trading-bar gap from the current head exceeds `max_hold_days`. Output columns: `cluster_id` (int), `seq_in_cluster` (1-based int), `is_cluster_head` (bool, true only for seq 1), `days_since_head` (int, trading bars, 0 for the head). The input frame is never mutated — a fresh copy is built and returned.

## What I tested and results

`capitalscan/tests/unit/test_backtest_clusters.py`, 8 tests, all passing:

1. Two events within `max_hold_days` (1 trading bar apart) share a `cluster_id`, seq 1/2, head only on seq 1.
2. Events 10 trading bars apart (max_hold_days=5) get distinct clusters, both heads.
3. Two tickers, identical signal_date, same side: never share a cluster.
4. Long and short on one ticker, identical signal_date: never share a cluster (matches `compute.py`'s `(ticker, side)` key).
5. `days_since_head` counts trading bars, not calendar days — Friday 2026-07-24 to Monday 2026-07-27 (weekend gap): trading_dates = `[Fri, Mon]` only, `max_hold_days=1`. Trading-bar gap is 1 (same cluster, `days_since_head=1`); a calendar-day gap test would see 3 calendar days and wrongly split into two clusters. This discriminates the two counting schemes directly.
6. `cluster_id` is deterministic across repeated calls with identical input (ADR 060).
7. Empty candidates frame returns an empty frame carrying the four new columns.
8. Input frame's column list is unchanged after the call (no mutation).

## TDD Evidence

**RED** — `uv run pytest capitalscan/tests/unit/test_backtest_clusters.py -v`, before implementation:

```
ImportError while importing test module '...\test_backtest_clusters.py'.
E   ImportError: cannot import name 'tag_clusters' from 'capitalscan.research.candidates'
```

Failed for the expected reason: the function didn't exist yet, not a fixture or import-path mistake elsewhere.

**GREEN** — same command, after implementation:

```
collected 8 items
test_backtest_clusters.py::TestTagClusters::test_two_events_within_max_hold_days_share_a_cluster PASSED
test_backtest_clusters.py::TestTagClusters::test_events_further_apart_than_max_hold_days_get_distinct_clusters PASSED
test_backtest_clusters.py::TestTagClusters::test_two_tickers_never_share_a_cluster_even_on_identical_dates PASSED
test_backtest_clusters.py::TestTagClusters::test_long_and_short_on_one_ticker_never_share_a_cluster PASSED
test_backtest_clusters.py::TestTagClusters::test_days_since_head_counts_trading_bars_not_calendar_days PASSED
test_backtest_clusters.py::TestTagClusters::test_cluster_id_is_deterministic_across_reruns PASSED
test_backtest_clusters.py::TestTagClusters::test_empty_candidates_returns_empty_frame_with_new_columns PASSED
test_backtest_clusters.py::TestTagClusters::test_does_not_mutate_the_input_frame PASSED
8 passed in 0.09s
```

Full safe suite, `uv run pytest capitalscan/tests/unit capitalscan/tests/property`: **506 passed**, no regressions.

## Files changed

- `capitalscan/research/candidates.py` — added `tag_clusters` and three private helpers.
- `capitalscan/tests/unit/test_backtest_clusters.py` — new file, 8 tests.

## The trading-date parameter shape I chose, and why

`trading_dates: dict[str, list[date]]` — one sorted, deduplicated list of trading dates per ticker.

I considered passing the raw `bars` DataFrame instead and deriving the per-ticker date lists inside `tag_clusters`. I rejected that: it would make the function redo a groupby/sort/dedup on every call even when a caller (e.g. a backtest driver looping over many parameter sweeps with the same underlying data) invokes `tag_clusters` repeatedly, and it would widen the function's input surface for no benefit — the function only ever needs sorted dates, never OHLCV. A plain `dict[str, list[date]]` is also the simplest thing to construct by hand in a unit test, which matters given the brief's requirement that the trading-bar-vs-calendar-day distinction be independently testable.

The caller builds it once from bars it already has, e.g.:
```python
trading_dates = bars.groupby("ticker")["ts"].apply(lambda s: sorted(s.dt.date.unique())).to_dict()
```
and can reuse the same dict across multiple `tag_clusters` calls in one run.

## The `compute._tag_clusters` divergence, stated explicitly

`jobs/compute.py:_tag_clusters` measures both the cluster-break gap test and `days_since_head` in **calendar days** (`compute.py:559`, `(event["signal_date"] - prev["last_date"]).days`; `compute.py:569`, `(event["signal_date"] - head_date).days`). `research/candidates.py:tag_clusters` measures both in **trading bars** instead, per Ruling C5, because `ExitParams.max_hold_days` counts forward bars and a calendar-day gap test silently shrinks across a weekend (5 calendar days spanning a weekend is only 3 trading bars). Concretely: two events on a Friday and the following Monday are 3 calendar days but 1 trading bar apart. With `max_hold_days=1`, `compute._tag_clusters` would open a new cluster (3 > 1); `research.candidates.tag_clusters` keeps them in the same cluster (1 is not > 1). **The two jobs will disagree on cluster boundaries for the same events** whenever a gap spans a weekend or holiday, until a later task gives cluster tagging a single writer at the database layer (per the controller's ruling, that writer is this backtest module — `compute.py` was intentionally left unchanged).

Both functions agree on: keying clusters on `(ticker, side)` (not ticker alone), and computing `cluster_id` as a deterministic hash of `(ticker, side, head_date)` via the same sha256-prefix construction (duplicated rather than imported, for the reason given in `_deterministic_cluster_id`'s docstring — flagged for the controller if a shared home for both is later wanted).

## Self-review findings

- **Completeness**: all four brief-required test cases present, plus determinism, empty-input, and no-mutation checks that mirror the existing style in `test_backtest_candidates.py`.
- **Naming**: `tag_clusters` matches the brief's pinned interface name exactly aside from the added `trading_dates` parameter, which the ruling explicitly requires.
- **YAGNI**: no extra parameters or return shapes beyond the four brief-specified columns. Removed an initially-added `_CLUSTER_COLUMNS` constant that ended up unused.
- **No magic numbers**: `max_hold_days` is a caller-supplied parameter throughout, never a literal; the `15`/`60-bit` slice in `_deterministic_cluster_id` is a hash-encoding detail (identical to the shipped `compute.py` version), not a domain threshold, so invariant 9 doesn't apply to it.
- **Tests verify real behavior**: the weekend test is constructed so the trading-bar and calendar-day answers actually diverge (1 vs 3), not just present in the same fixture — I checked this by hand before writing the assertion.
- **Determinism**: no wall-clock reads; `cluster_id` reruns identically (tested).
- **Windows spawn rule**: no new module-level side effects; the module remains importable with none.

## Issues or concerns

- None blocking. The stated `compute.py`/`research/candidates.py` divergence is by design per Ruling C5 and is documented in the `tag_clusters` docstring itself as well as here, so it stays visible to whoever wires the eventual single writer.
- `docker-compose.yml` shows as locally modified in `git status` but is unrelated to this task (pre-existing working-tree change from before this session); I left it untouched and out of my commit.

---

## Fix report: code review finding (silent single-cluster collapse)

### What I changed

`candidates.py`'s `sorted_dates` construction used `trading_dates.get(ticker, [])`, silently defaulting to an empty list when a ticker in `candidates` had no entry in `trading_dates`. With `dates = []`, `_trading_bars_between` always returns 0 (`bisect` on an empty list gives `lo == hi == 0`), so the cluster-break condition `gap > max_hold_days` never fires and every candidate for that ticker collapses into one endless cluster, with `days_since_head == 0` on every row — a plausible-looking, silently wrong answer.

Fixed in `capitalscan/research/candidates.py`:

1. Building `sorted_dates` now raises `ValueError` if a ticker present in `candidates` has no entry, or an empty entry, in `trading_dates`.
2. Inside the per-event walk, before computing the gap, a `bisect.bisect_left` lookup confirms the event's own `signal_date` is actually present in that ticker's supplied date list; if not, raises `ValueError` naming the ticker and date.
3. Minor, same file: the `else` branch (event stays in the existing cluster) previously called `_trading_bars_between(dates, head_date, signal_date)` a second time to fill `days_since_head`, recomputing the exact value already produced by the `gap` check one line above (since `head_date` does not change in that branch). Now both branches set a single `days_since_head` local from the already-available value (`0` for a new head, the already-computed `gap` otherwise), and only one `_trading_bars_between` call exists per event.
4. Minor, test file: `test_events_further_apart_than_max_hold_days_get_distinct_clusters`'s docstring said "ten trading bars apart" for a July 1 → July 15 gap against a fixture listing every calendar day of July as a trading date. The real gap is fourteen trading bars (calendar days and trading bars coincide only because the fixture has no weekends removed). Corrected the comment; the assertions were already correct and unchanged.

### Reasoning on which checks to raise on

I raise on both the ticker-level and the date-level mismatch, because they are the same failure class at different granularities and either one alone leaves a hole:

- **Ticker missing (or empty) in `trading_dates`.** This is the exact case the reviewer found: a caller builds `trading_dates` from a bars frame that is missing a ticker `candidates` has events for (e.g. a universe/ticker-set drift between the two inputs). Catching only this case would still let a *partially* wrong `trading_dates` (right ticker, wrong or truncated date range) through silently — the ticker-level check alone doesn't verify the dates it does have actually cover the candidates.
- **`signal_date` absent from the ticker's own supplied dates.** This catches the partial case: `trading_dates` was built from a narrower window than `candidates` (e.g. a read-window/write-window mismatch, the same class of bug Ruling C3 exists to prevent for bar/indicator pairing in this same module). Without this check, a `trading_dates` entry that's present but incomplete would still silently misdate the trading-bar count for any event whose day falls in the gap.

I did not weaken either to a warning — both `raise ValueError` unconditionally, per the coordinator's explicit instruction not to soften this to a log line. Both messages name the offending ticker and (for the second) the offending date, so the exception is actionable without a debugger.

I considered validating this once up front (e.g. a full pass checking every candidate's `(ticker, signal_date)` against `trading_dates` before the main loop) rather than inline in the per-event walk. I kept the date-level check inline instead: it reuses the same `bisect` call shape as `_trading_bars_between` (no new O(n) scan), and it fails on the first offending row rather than requiring a second full pass over `candidates` — cheaper and just as loud.

### Covering tests

Added `TestTagClustersRaisesOnInputMismatch` to `capitalscan/tests/unit/test_backtest_clusters.py`, three tests:

- `test_a_ticker_missing_from_trading_dates_raises` — `trading_dates = {}`, ticker has two candidates, expects `ValueError` matching the ticker name.
- `test_a_ticker_with_an_empty_trading_dates_list_raises` — `trading_dates = {"TSM": []}`, expects the same.
- `test_a_signal_date_absent_from_the_tickers_trading_dates_raises` — ticker has a `trading_dates` entry that does not include the candidate's own `signal_date`, expects `ValueError` matching the offending date string.

### Commands and output

`uv run pytest capitalscan/tests/unit/test_backtest_clusters.py -v`:

```
collected 11 items
test_backtest_clusters.py::TestTagClusters::test_two_events_within_max_hold_days_share_a_cluster PASSED
test_backtest_clusters.py::TestTagClusters::test_events_further_apart_than_max_hold_days_get_distinct_clusters PASSED
test_backtest_clusters.py::TestTagClusters::test_two_tickers_never_share_a_cluster_even_on_identical_dates PASSED
test_backtest_clusters.py::TestTagClusters::test_long_and_short_on_one_ticker_never_share_a_cluster PASSED
test_backtest_clusters.py::TestTagClusters::test_days_since_head_counts_trading_bars_not_calendar_days PASSED
test_backtest_clusters.py::TestTagClusters::test_cluster_id_is_deterministic_across_reruns PASSED
test_backtest_clusters.py::TestTagClusters::test_empty_candidates_returns_empty_frame_with_new_columns PASSED
test_backtest_clusters.py::TestTagClusters::test_does_not_mutate_the_input_frame PASSED
test_backtest_clusters.py::TestTagClustersRaisesOnInputMismatch::test_a_ticker_missing_from_trading_dates_raises PASSED
test_backtest_clusters.py::TestTagClustersRaisesOnInputMismatch::test_a_ticker_with_an_empty_trading_dates_list_raises PASSED
test_backtest_clusters.py::TestTagClustersRaisesOnInputMismatch::test_a_signal_date_absent_from_the_tickers_trading_dates_raises PASSED
11 passed in 0.10s
```

`uv run pytest capitalscan/tests/unit capitalscan/tests/property` (full safe gate): **509 passed** (506 previously + 3 new raise tests), no regressions.

### Files changed (this fix)

- `capitalscan/research/candidates.py` — added the two `ValueError` checks, deduplicated the redundant `_trading_bars_between` call, updated `tag_clusters`'s docstring to document the raise conditions.
- `capitalscan/tests/unit/test_backtest_clusters.py` — added `TestTagClustersRaisesOnInputMismatch` (3 tests), corrected the misleading "ten trading bars" comment.

Commit: `c432f5e` — "Fix silent single-cluster collapse in tag_clusters on missing trading_dates"
