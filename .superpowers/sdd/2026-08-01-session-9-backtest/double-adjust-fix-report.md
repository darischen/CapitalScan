# Hourly split back-adjustment — double-adjust fix

HEAD at start: `87ef410`. Scope: `capitalscan/jobs/ingest.py`
(`_split_adjustment_factor` / `_back_adjust_hourly`, lines ~532-666) and
`capitalscan/tests/unit/test_bars_hourly_split_adjust.py`. No other file
touched except the pre-existing unrelated `docker-compose.yml` change already
in the working tree before this task started.

## The defect, recap

`_split_adjustment_factor` divided every hourly bar strictly before a split's
`ex_date` by that split's ratio, on the premise "Yahoo's hourly endpoint never
back-adjusts." False for a window of sessions immediately before certain
ex-dates: Yahoo had already back-adjusted those hours, so dividing again
over-corrected by exactly the split ratio. 1,880 bars across 15 tickers,
confirmed in `hourly-residual-diagnosis.md`.

## Detection method derived

For each bar whose date is "pending" a split (naive rule says divide), decide
**per (ticker, day)** whether the vendor already adjusted that day, using the
daily bar as the reliable reference (Yahoo's daily endpoint back-adjusts
consistently — established in the original fix report):

1. Compute the naive factor exactly as before (product of every pending
   split's ratio) — this is now called `naive`, not the final answer.
2. For each day where `naive != 1.0` (a split is pending), aggregate the raw,
   pre-adjustment hourly `high` for that day (`max(high)`) and compare it to
   the matching daily bar's `high`.
3. If `abs(raw_day_high / daily_high - 1.0) <= tolerance`, the raw hourly
   aggregate already sits at the same scale as daily — the vendor pre-adjusted
   that day. Skip division for that day (factor = 1.0), rather than dividing
   again.
4. Otherwise (no match, or no daily bar available for that day), fall back to
   the naive factor — divide, as before.

`tolerance` reuses the existing `HourlySplitGuard.range_escape_tolerance`
(0.50) rather than adding a new config value. That threshold was already
derived from the live database for exactly this separation problem: ordinary
hourly/daily tick noise never exceeds ~33% relative deviation even in its
worst observed case (FTV, WDC), while every confirmed real split's raw
aggregate differs from daily by at least 80% (AMCR's reverse split is the
smallest at ratio 0.2, |0.2-1|=0.8) when unadjusted, and true pre-adjusted
days sit within ordinary noise of 1.0. The same gap that makes 0.50 a safe
guard threshold makes it a safe "is this day already at daily's scale"
threshold — same populations, same separation, no new number invented, no
`config_hash` impact (invariant 9/10: no new `Config` field, and the reused
value already lives in the standalone `HourlySplitGuard` dataclass).

**Limits, stated directly:**

- This is a `high`-only check. I did not additionally require `low` to
  confirm, matching the diagnosis's own verification method (it also used
  `high`). A pathological case where `high` coincidentally lands within
  tolerance of daily while the rest of the bar is still wrong would slip
  through undetected; nothing in the 1,880-row diagnosis showed this shape,
  but it is not structurally ruled out.
- Multiple simultaneously-pending splits on the same day are decided as one
  unit, not independently. If the vendor pre-adjusted only one of two
  overlapping pending splits, this function cannot tell and falls back to
  full naive division (the conservative, previously-existing behavior). DD's
  two splits in the diagnosis were cleanly non-overlapping, so this case is
  untested against real data — it is a known gap, not a silently-guessed
  answer.
- The "already close to 1.0" test only fires for days where a split is
  actually pending (`naive != 1.0`); a ticker with no splits, or a day after
  every relevant `ex_date`, never enters this branch at all — it returns
  `naive` (= 1.0) immediately, same as before the fix.

## What happens when the daily bar is missing

Falls back to full naive division — the same behavior as before this fix,
for that day. This is a deliberate choice, not an oversight: silently
skipping the adjustment for lack of a daily reference is the same shape of
bug this task exists to fix (assuming instead of checking, just in the
opposite direction). Absence of daily data is not evidence the vendor
pre-adjusted; the default stays "assume unadjusted, divide," matching the
pre-fix baseline and invariant 4 (never guess toward a convenient answer,
including "do nothing").

## TDD evidence

RED (stashed `capitalscan/jobs/ingest.py` back to the pre-fix version at
`87ef410`, kept the new test):

```
$ uv run pytest capitalscan/tests/unit/test_bars_hourly_split_adjust.py -q
...
FAILED ...::test_a_vendor_preadjusted_window_is_left_alone_while_earlier_bars_still_divide
KeyError: '2024-01-30 09:30'
1 failed, 6 passed in 1.64s
```

(The pre-fix code double-divides the pre-adjusted day so far that its
day-aggregate range escapes the daily range by more than the guard's
tolerance — the row is rejected outright rather than merely wrong, which is
why the assertion fails with a `KeyError` for the missing row rather than a
value mismatch. Either failure shape is a correct RED signal; this one
additionally confirms the guard was already catching the corruption, as the
diagnosis said.)

GREEN (fix restored):

```
$ uv run pytest capitalscan/tests/unit/test_bars_hourly_split_adjust.py -q
.......
7 passed in 0.33s

$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
...
768 passed in 42.70s
```

## The over-broadness test

`test_a_ticker_with_no_splits_is_untouched` (pre-existing, unchanged) proves
the fix is not over-broad: with no splits, `naive` is 1.0 for every bar, the
new day-level check never runs (`pending.any()` is `False`), and every bar
passes through unmodified — exactly the pre-fix behavior. The regression
test itself is a second over-broadness check in the other direction: the day
*far* from the ex-date (2024-01-02, genuinely unadjusted) is still divided
by 4 as before; only the day matching daily's scale is spared.

Also unchanged, still green: `test_a_10_for_1_split_...`, the reverse-split
test (AMCR shape, ratio 0.2), `test_multiple_splits_compound` (DD shape, two
splits compounding), `test_rerunning_does_not_double_adjust` (idempotency),
and `test_a_ratio_escaping_the_daily_range_by_more_than_noise_is_rejected`
(the durable guard, unrelated to this change). All of these use the
`written` fixture's default `_read_daily_range` stub (returns an empty
frame), which makes `daily_range.empty` true and short-circuits
`_split_adjustment_factor` back to the naive factor immediately — identical
to pre-fix behavior for every case that doesn't supply daily data.

## `config_hash`

Unchanged, confirmed:

```
$ uv run python -c "from capitalscan.core.config import DEFAULT_CONFIG; from capitalscan.jobs.config import config_hash; print(config_hash(DEFAULT_CONFIG))"
3e598c59e7d71eae
```

Matches the value already set as the Postgres GUC. No field was added to
`Config` or any of its members; the fix reuses the existing standalone
`HourlySplitGuard.range_escape_tolerance`.

## Idempotency

Unchanged in shape from the original fix: `_split_adjustment_factor` is
still a pure function of the just-fetched `raw` frame, the current
`corporate_actions` snapshot, and now also the current daily `bars` range —
all read fresh on every call, never applied to a value already stored. A
rerun re-fetches from Yahoo, recomputes the same factor from the same three
inputs, and rewrites the same rounded numbers via the existing
`(ticker, ts, interval)` upsert key. `test_rerunning_does_not_double_adjust`
still asserts two successive runs produce byte-identical output; it was not
modified for this task, and still passes.

## The refetch command the controller should run

Once the live `cscan backtest --sweep --workers 8` finishes, per the
diagnosis's confirmed 15-ticker residual list:

```
cscan bars --hourly --backfill --tickers ANET,NFLX,AMCR,CVNA,CRWD,DD,NOW,TSCO,IBKR,KLAC,ORLY,FAST,BKNG,ETR
```

This is the diagnosis's Question 2 residual table, DD counted once despite
appearing as two separate non-overlapping reject blocks (one per split). That
table lists 14 distinct ticker names; the diagnosis's prose calls it "15
tickers" (likely counting DD's two blocks as two), so cross-check against a
live `bar_rejects` query for `rule = 'hourly_daily_range_escape'` before
running, in case a 15th name exists that I don't have in front of me. Do not
reuse the *other* report's 19-ticker list (`hourly-split-adjust-report.md`)
here — that list answers a different question (which tickers needed the
original fix), not this residual.

**Do not run this now** — the sweep is still live against this database.

## Expected rejects after refetch

Near zero for these 15 tickers under `hourly_daily_range_escape`, per the
task's stated acceptance signal. The guard's tolerance (0.50) is untouched,
so any residual after refetch would indicate either a 16th affected ticker
not yet diagnosed, or a case this fix's `high`-only / non-overlapping-splits
limits (above) don't cover — worth a second pass over `bar_rejects` after
the refetch rather than assuming exactly zero.

BNY is explicitly out of scope (separate vendor-feed defect per the
diagnosis, not a split problem, not touched by this fix, and not included in
the refetch command above).

## Concerns

1. **`high`-only detection.** Stated above under Limits — did not cross-check
   `low`, matching the diagnosis's own verification method but not
   structurally exhaustive.
2. **Overlapping pending splits.** Two splits pending on the same day, with
   the vendor pre-adjusting only one, is not something the diagnosis observed
   and is not covered by a test — falls back to full naive division, which
   preserves the historical (safe) default rather than guessing.
3. **The exact 15-ticker list.** I derived it from
   `hourly-residual-diagnosis.md`'s Question 2 table; the controller should
   verify against a live `bar_rejects` query for `rule =
   'hourly_daily_range_escape'` before running the refetch, since the
   original fix report separately found 19 names by a different derivation
   method for a related-but-not-identical question (the tickers needing the
   *first* fix, not this residual). Don't reuse that list for this refetch.
4. **Scope discipline.** I did not touch `core/`, `research/`, or
   `jobs/compute.py`, and made no database writes — all verification was via
   the stubbed unit tests and a local `config_hash` computation, no `cscan`
   command was run, consistent with the safety constraints for this task.

---

## Addendum: round-2 fix — the borrowed-tolerance finding

Coordinator review (2026-08-02) found a real gap in the round-1 fix above:
`abs(raw_day_high/daily_high - 1.0) <= 0.50` (borrowed from
`HourlySplitGuard.range_escape_tolerance`) cannot discriminate a small-ratio
split from noise. Measured live: `SELECT count(*) FILTER (WHERE ratio
BETWEEN 0.667 AND 1.5) FROM corporate_actions WHERE action_type='split' AND
ex_date > '2024-08-01'` returns **11 of 33** splits in the hourly window. For
a ratio like 1.2, an *unadjusted* day's true ratio-to-daily is 1.2, and
`|1.2 - 1.0| = 0.2 <= 0.50` — the round-1 code reads that as "already
adjusted" and wrongly skips division. The old code (pre-any-fix) was right
on unadjusted days and wrong on pre-adjusted ones; round-1 flipped that
without closing the gap. Both single-threshold designs fail on the same
class of split.

### Detection method, corrected

Per (ticker, day) with a pending naive factor `F != 1.0`, the observed ratio
`raw_day_high / daily_high` is now scored against **two competing
hypotheses** instead of one absolute band:

- `already adjusted` predicts `observed ~= 1.0`
- `not yet adjusted` predicts `observed ~= F`

Whichever hypothesis `observed` sits nearer to wins, **but only when the
margin between the two distances exceeds `HourlySplitDetection.
resolution_margin`** (a new standalone dataclass in `core/config.py`, value
`0.10`). If neither hypothesis is decisively nearer, the day is
**unresolved**: dropped from what gets adjusted and upserted, and logged to
`bar_rejects` under a new rule, `hourly_split_adjustment_unresolved`,
severity `reject`, rather than guessed either way.

`0.10` is not invented for this task — it is the same "large majority of
mismatched pairs stays under ~10% relative deviation" figure already in
`HourlySplitGuard`'s own docstring, characterizing the live 297,790-pair
noise measurement. What changed is *which* question that noise floor
answers: `range_escape_tolerance` (0.50) still answers "is this
post-adjustment bar's range clean or split-sized" (untouched, per the
coordinator's instruction); `resolution_margin` (0.10) answers "is the
pre-adjustment observed ratio decisively closer to 1.0 or to the pending
split factor" — a genuinely different comparison at a different scale, so it
needed its own field rather than a second use of the same one.

### Why unresolved days are dropped, not guessed either way

The coordinator's framing: a bar the range-escape guard rejects preserves
the prior good row (nothing gets overwritten); a bar that passes silently
overwrites it. For the ambiguous band specifically, a wrong guess in either
direction produces an error of magnitude `|F - 1|` — for `F = 1.2` that is a
20% deviation, comfortably inside the guard's 50% tolerance, so the guard
would not catch it. Guessing here is not just risky, it is *invisible*: the
downstream safety net that would normally catch a bad split adjustment
cannot fire on an error this small. Dropping the row and logging it under
its own named rule keeps the failure visible in `bar_rejects` (greppable,
auditable, distinct from the range-escape rule so the two failure modes
aren't conflated) instead of landing a silently-wrong number in `bars`.

### `Config` / `config_hash` impact

`HourlySplitDetection` is a new standalone dataclass, same shape and same
rationale as `SweepParams`, `SharesPlausibility`, and `HourlySplitGuard`
itself: not a field of `Config`, so `dataclasses.asdict(Config())` never
sees it. Confirmed unchanged:

```
$ uv run python -c "from capitalscan.core.config import DEFAULT_CONFIG; from capitalscan.jobs.config import config_hash; print(config_hash(DEFAULT_CONFIG))"
3e598c59e7d71eae
```

Same value as before this addendum and as the live Postgres GUC.

### TDD evidence, round 2

RED — reconstructed the round-1 (tolerance-borrowing) implementation in a
scratch edit (via `git stash` on `ingest.py` back to `87ef410`, then
reapplying only the round-1 detection logic, keeping the already-updated
test file), and ran the two new small-ratio tests:

```
$ uv run pytest capitalscan/tests/unit/test_bars_hourly_split_adjust.py -q -k small_ratio
.F
FAILED ...::test_a_small_ratio_split_vendor_unadjusted_still_divides
assert np.float64(144.0) == 120.0 ± 1.0e-04
1 failed, 1 passed in 0.84s
```

The "vendor already adjusted" case passes even under round-1 (it reaches the
right answer for the wrong reason — anything within 0.50 of 1.0 reads as
adjusted, and this day genuinely is). The paired "vendor did NOT adjust"
case at the same ratio (1.2) is the one that exposes the bug: raw stays at
144.0 instead of dividing down to 120.0, exactly the coordinator's
prediction.

GREEN (round-2 fix restored via `git checkout` + `git stash pop`):

```
$ uv run pytest capitalscan/tests/unit/test_bars_hourly_split_adjust.py -q
.........
9 passed in 0.36s

$ uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
...
770 passed in 30.19s
```

(770 = 768 from the round-1 GREEN run + the 2 new small-ratio tests.)

### New tests added

- `test_a_small_ratio_split_vendor_already_adjusted_is_left_alone` — ratio
  1.2, raw day-high already at daily's scale (~120); must NOT divide.
- `test_a_small_ratio_split_vendor_unadjusted_still_divides` — same ratio,
  raw day-high at ~1.2x daily (~144); must divide down to ~120. This is the
  one that fails against round-1.

Both use single-bar days at exact hypothesis values (no added noise) so the
nearer-hypothesis test is unambiguous by construction — decisive under
`resolution_margin = 0.10` regardless of the exact noise distribution,
since `|dist_adjusted - dist_unadjusted| = 0.2 > 0.10` in both cases. No new
test exercises the *unresolved* path directly (a day genuinely equidistant
between the two hypotheses); the reasoning above and the docstring in
`core/config.py`'s `HourlySplitDetection` state that limit, but I did not
add a bar_rejects-level test for the unresolved branch specifically —
noted here rather than left silent.

### Residual limit (restated from `core/config.py`)

A split whose ratio sits within roughly `2 * resolution_margin` of 1.0
(around ratio 0.80-1.20 for a forward split, symmetric for reverse) can
still land in the unresolved band on an unusually noisy day even though
most days for that split resolve cleanly — inherent to distinguishing two
nearby points under real measurement noise, not fixable by moving the
margin without trading off against the opposite failure (a margin too wide
starts misreading genuine noise as decisive, recreating this same class of
bug one level up).

### Commit

Committed on `session-9-backtest`, both rounds combined into one commit
(nothing was committed between round 1 and round 2 — the intermediate
tolerance-borrowing version never existed as a separate commit, only as a
working-tree state reconstructed for the RED test above).
