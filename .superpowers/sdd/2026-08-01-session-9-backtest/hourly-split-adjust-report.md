# Hourly split back-adjustment — report

HEAD at start: `b1d78b5`. Scope: `capitalscan/jobs/ingest.py` (`run_bars_hourly`, `validate_bars`
call site), `capitalscan/core/config.py` (one standalone dataclass), plus tests.

## The `ratio` convention, verified

`corporate_actions.ratio` is `new_shares / old_shares`. Verified directly against the live
table, not inferred:

```
SELECT ticker, ex_date, action_type, ratio FROM corporate_actions WHERE ticker='KLAC' AND action_type='split';
...
KLAC | 2026-06-12 | split | 10
```

KLAC's confirmed 10-for-1 split carries `ratio = 10`. Cross-checked against seven more of the
named mismatched tickers — AMCR (`ratio = 0.2`, a 1-for-5 reverse split), BKNG (`ratio = 25`),
CRWD (`ratio = 4`), CVNA (`ratio = 5`), NOW (`ratio = 5`), TPL (two splits in-window, `ratio = 3`
each, correctly compounding) — every one matches the measured hourly/daily mismatch factor
reported in the task (KLAC ~10x, CRWD ~4x, AMCR ~0.2x, ...) exactly. A bar strictly before
`ex_date` needs dividing by `ratio`; a bar on or after does not. This is what
`_split_adjustment_factor` implements.

**One ticker in the named list, BNY, has no split row in `corporate_actions` at all** within the
hourly window (or within 730 days of today) — see Concerns.

## The back-adjustment

`capitalscan/jobs/ingest.py`:

- `_split_adjustment_factor(ts, splits)` — per-bar cumulative product of `ratio` over every
  split with `ex_date > ts.date()`. Empty/no-match splits return 1.0 for every row (never
  fabricated). A null or non-positive `ratio` is filtered out before use, not guessed
  (invariant 4).
- `_back_adjust_hourly(raw, splits)` — divides `open/high/low/close` by that factor, rounds to
  4dp (project convention), on a copy.
- Wired into `run_bars_hourly`: `corporate_actions` is now read once for the whole ticker list
  before the per-ticker loop (same shape as `run_bars_daily`'s `_read_corporate_actions` call),
  filtered to `action_type == 'split'`, and each ticker's slice is applied to its own fetch
  before `adj_close`/`interval`/etc. are set. `adj_close = close` and `adj_factor = 1.0` are
  **unchanged lines** — they now inherit the back-adjustment for free because `close` is already
  adjusted by the time they run. Hourly still carries no dividend adjustment; that pre-existing
  limitation is untouched, per the task's instruction not to touch adj_close/adj_factor
  semantics beyond what the split fix requires.

## `corporate_actions` in `validate_bars`, and hourly safety

It enables the split-explained-large-move rule: a close-to-close move beyond `LARGE_MOVE_PCT`
(40%) is checked against `SPLIT_RATIOS`; if it lands near a split ratio **and** an ex-date
lands on or one day before that bar, it's flagged `large_return_explained_by_split` rather than
rejected `unexplained_split_like_move`. `corporate_actions=None` (the bug) meant every hourly
batch had this disabled — a genuine split still landing unadjusted in a live feed would either
pass silently (`abs(chg) <= 0.40`, unlikely) or get hard-rejected as unexplained.

Safe on hourly, and now safer than before: after back-adjustment, a true split no longer
produces a jump at all (both sides of the ex-date are on the same scale), so this rule rarely
fires on split days going forward. What it remains useful for is the same artifact
`unresolved_rejects`'s docstring already documents for daily — a batch fetched across a split
boundary before the local adjustment lands, or before `corporate_actions` itself has the row —
where it prevents a hard reject on a bar that's actually fine. A 40% single-*hour* move is far
rarer than a 40% single-*day* move, so this rule is if anything more conservative on hourly than
on daily, not less.

## The guard

`_flag_range_escape(hourly, daily_range, guard)` in `ingest.py`, called from `run_bars_hourly`
right after `validate_bars`, against the *already-clean* frame. It aggregates hourly bars to
`(ticker, day)` and compares `max(high)`/`min(low)` against the matching `bars` daily row's
`high`/`low`. Escaping rows are rejected (`bar_rejects`, rule `hourly_daily_range_escape`) and
dropped from what gets upserted — not just flagged, since an unadjusted split corrupts every
`TOUCH_5M`/`TOUCH_30M` fill price on that side of the ex-date, and this is exactly the failure
mode the task's blast-radius note warns is one universe-threshold change away from going live.

**Tolerance — `core.config.HourlySplitGuard.range_escape_tolerance = 0.50`.** Standalone
dataclass (`DEFAULT_HOURLY_SPLIT_GUARD`), not a `Config` field, same rationale as `SweepParams`/
`SharesPlausibility`: nothing here varies a backtest result. Derived from the live database, not
picked:

- Across 297,790 (ticker, day) pairs with both bars present, ordinary tick-level noise has a
  median absolute gap of ~$0.005 and stays under ~10% relative deviation for the large majority
  of mismatched pairs.
- The largest relative deviation found among pairs with **no** matching split in
  `corporate_actions` was ~33% (FTV, WDC — a separate, smaller daily/hourly data-quality gap,
  not this defect).
- The smallest relative deviation among the confirmed-split tickers was AMCR's reverse split
  (`ratio = 0.2`, an 80% deviation); every other confirmed split deviates 200%+ (CRWD 4x,
  KLAC 10x, BKNG 25x).

`0.50` sits with wide margin above the largest observed non-split anomaly (0.33) and well below
the smallest real split (0.80) — it flags every known split-factor mismatch and leaves every
known non-split anomaly alone.

**Placement — in `run_bars_hourly`, not `run_validate`.** The daily bar the guard needs may not
exist yet on a from-scratch backfill (daily and hourly are separate jobs with no ordering
guarantee) — handled by silently skipping days with no daily counterpart (invariant 4: absence
isn't evidence, don't guess). But the guard needs to reject specific hourly rows and attach them
to `bar_rejects` at ingest time, in the same transaction-scoped batch that already writes
`bar_rejects` for that ticker; `run_validate` only ever sees rejects already on file — it has no
per-bar granularity to create a new one against. In this codebase's normal backfill order
(daily before hourly), the daily rows this guard needs are already on file, so it fires every
time it matters.

## TDD evidence

RED (stashed `ingest.py`/`config.py`/the checkpoint-test patch back to `b1d78b5`, kept the new
test file):

```
$ uv run pytest capitalscan/tests/unit/test_bars_hourly_split_adjust.py -q
...
FAILED ...::test_a_ratio_escaping_the_daily_range_by_more_than_noise_is_rejected
ERROR ...::test_a_10_for_1_split_back_adjusts_pre_split_bars_only
ERROR ...::test_a_reverse_split_adjusts_the_other_way
ERROR ...::test_multiple_splits_compound
ERROR ...::test_a_ticker_with_no_splits_is_untouched
ERROR ...::test_rerunning_does_not_double_adjust
1 failed, 5 errors in 0.71s
```

GREEN (fix restored):

```
$ uv run pytest capitalscan/tests/unit/test_bars_hourly_split_adjust.py -q
......
6 passed in 0.18s

$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
...
752 passed in 26.89s
```

`capitalscan/tests/unit/test_bars_hourly_checkpoint.py` (pre-existing) needed one fixture patch
— its `_FakeEngine` has no `.connect()`, which the two new reads
(`_read_corporate_actions`, `_read_daily_range`) now call before the per-ticker loop — so its
`upserted` fixture now also stubs those two reads to empty frames. All three of its tests still
pass unchanged otherwise.

New coverage in `capitalscan/tests/unit/test_bars_hourly_split_adjust.py`: 10:1 split adjusts
only pre-split bars; a reverse split (ratio 0.2) adjusts the other way; two splits inside one
window compound (divide by the product); a ticker with no splits is untouched (proves the fix
isn't over-broad); re-running produces byte-identical output (idempotency); an unadjusted range
escape with no covering split is rejected by the guard.

## Idempotency

The adjustment is a pure function of the just-fetched `raw` frame and the current
`corporate_actions` snapshot, recomputed on every call — never applied to a value already read
back from `bars`. A rerun re-fetches from Yahoo, re-divides by the same factor from the same raw
input, and rewrites the same rounded numbers via the existing `(ticker, ts, interval)` upsert
key. `test_rerunning_does_not_double_adjust` asserts two successive runs produce byte-identical
`open/high/low/close`.

## `config_hash`

Unchanged. `HourlySplitGuard` is standalone, outside `Config`, so `dataclasses.asdict(config)`
never sees it.

```
$ uv run python -c "from capitalscan.core.config import DEFAULT_CONFIG; from capitalscan.jobs.config import config_hash; print(config_hash(DEFAULT_CONFIG))"
3e598c59e7d71eae
```

Matches the value already set as the Postgres GUC.

## Repair command

```
cscan bars --hourly --backfill --tickers KLAC,CRWD,TPL,AMCR,BKNG,CVNA,NOW,BDX,CMCSA,ETR,FDX,FTV,IBKR,J,LEN,ORLY,PANW,SPGI,WDC
```

**Do not run this now — a `cscan backtest` is live against this database.** Queue it for after
the current run finishes.

At ~13 sixty-day windows per ticker and 0.5 req/s (the rate the hourly fetcher is already
limited to), 19 tickers is ~247 requests, ~8-9 minutes. Rounding up for connection/upsert
overhead: **under 15 minutes.**

## Concerns

1. **I could not reproduce exactly "17 tickers" from the database as given.** Re-deriving the
   mismatch independently (joining hourly/daily range escapes against `corporate_actions` splits
   with `ex_date` in-window, requiring the escape ratio to match the split ratio within 5% over
   20+ days) surfaces **19** names, not 17: the 8 named in the task (KLAC, CRWD, TPL, AMCR,
   BKNG, CVNA, NOW — BNY excepted, see below) plus BDX, CMCSA, ETR, FDX, FTV, IBKR, J, LEN,
   ORLY, PANW, SPGI, WDC. The repair command above uses my 19, not the task's 17 — the
   controller should reconcile against whatever query produced "17" before running it, since
   running it against the wrong list either under- or over-repairs.
2. **BNY has no split row in `corporate_actions` at all**, in-window or otherwise recent — the
   most recent row on file is a 2007 split. Its measured ~0.115x hourly/daily mismatch has the
   shape of a reverse split, but back-adjustment can't fix what `corporate_actions` doesn't
   record. Run `cscan actions --tickers BNY` first and check whether a split row appears before
   including it in the hourly repair; if none appears, this is a distinct
   `corporate_actions`-ingest gap that needs separate investigation, and BNY's hourly bars will
   keep tripping the new guard (correctly) until it's resolved.
3. **The guard rejects, it does not repair.** It stops the *next* unadjusted split from
   silently corrupting live data the way this one did, but it does nothing for the 17-or-19
   tickers already wrong in the database today — that's what the repair command is for. Until
   that repair runs, those tickers' existing hourly rows are still on the old, unadjusted scale.
4. **Guard granularity is per day, not per bar.** A day flagged by the guard has every hourly
   bar for that ticker/day rejected, not just the specific bars that escape — deliberate (an
   unadjusted split corrupts the whole day equally on one side of the ex-date), but worth stating
   since it means one bad day costs more rows than the minimum that technically failed the
   range check.
