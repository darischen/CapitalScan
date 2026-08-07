# Hourly range-escape residual — diagnosis

Read-only. No code changed, nothing committed, nothing written to the database. All
queries run with `SET max_parallel_workers_per_gather=0` against the live DB while
`cscan backtest --sweep --workers 8` was running.

HEAD: `87ef410`.

## Question 1 — BNY: symbol reuse hypothesis, REFUTED (as stated); something adjacent is true

The controller's hypothesis was that Yahoo's hourly endpoint is serving a *different
company's* history under the `BNY` symbol (the classic FB/PCLN/PCS/Q pattern from
`reports/AUTONOMOUS_RUN_FINDINGS.md`, where a splice shows a price-range ratio in the
thousands). That specific mechanism does not fit:

- Daily `BNY` (2005-10-11 to 2026-07-31, 5,233 rows) has a range ratio of only **10.6x**
  (`$15.44`-`$163.77`) — a completely ordinary 21-year equity history, and it climbs
  smoothly and continuously the whole way (checked visually across the run). Nothing
  spliced.
- `tickers.name` for `BNY` is `BNY Mellon`, one row, `first_bar` 2005-10-11 — consistent
  with the daily series, no second identity on file.
- `corporate_actions` has no split row for `BNY` at all inside or near the hourly
  window (confirms the earlier report's Concern #2) — only dividends back to 2021.

But the hourly series is not right either, and not in the "spliced company" shape:

- Hourly `BNY` from 2024-08-06 to 2026-02-06 sits **flat between $9.30 and $11.10** —
  a range ratio of **1.19x over 18 months**. That is far too tight for a real bank
  stock (BNY's own daily series moves 10.6x over a comparable multi-year span); it is
  the signature of a frozen/stale quote, not a second real company's price history.
- Compared day-by-day against daily `BNY` over the same calendar dates, the mismatch
  ratio **drifts continuously**: ~5.8x in Aug 2024, ~8x by May 2025, ~12x by
  Feb 2026 — not a fixed multiple, which rules out an unrecorded/missing split (a
  split produces a constant ratio, not one that grows over 18 months).
- The ratio does not stay wrong. Bars from 2026-05-22 onward suddenly track daily
  almost exactly (ratio ~1.00-1.01). The transition is a hard discontinuity, not a
  gradual convergence.
- This is two separate ingest runs, not one continuous defect: `run_id
  bars_hourly_20260801T075043_e42a231b` (the older, pre-guard run) wrote the bad
  2024-08-06 → 2026-02-06 rows still sitting in `bars` today. `run_id
  bars_hourly_20260803T003645_5074d8eb` (the current, guarded run) re-fetched the
  same date range, correctly rejected almost all of it
  (`hourly_daily_range_escape` = 2,545, matching the task's number exactly), and
  only wrote the 2026-05-22 → 2026-07-31 window that now agrees with daily.

**Verdict: not the classic symbol-reuse splice (no thousand-x range, no second
identifiable company, no acquisition-boundary discontinuity of that shape). It is a
distinct vendor defect: the Yahoo hourly endpoint served flat, badly-scaled, low-
volatility data for `BNY` for roughly 18 months, then started returning correct data
around 2026-05-22, with no split or corporate action to explain either the mismatch
or the correction.** Functionally it behaves like the vendor was quoting the wrong
instrument (or a dead/stale feed) under this ticker for that window — same failure
*class* as symbol reuse (wrong data under the right name) — but I could not identify
what the $9-11 series actually was; there is nothing in this database that names a
donor security. I could not fully settle "what it is," only "what it isn't." A
process-of-elimination case, not a confirmed identity.

This also means: **the stale bad rows for BNY (2024-08-06 → 2026-02-06) are still
sitting in `bars` right now**, upserted by the older pre-guard run and never
retracted — the guard stops new bad rows from landing, it does not delete old ones.
Whatever `TOUCH_5M`/`TOUCH_30M` computations ran against `bars` for BNY before this
session's re-backfill would have used this bad data silently. This is a repair item
distinct from the split-guard fix and should be flagged to whoever owns cleanup: BNY's
whole pre-2026-05-22 hourly range needs deletion, not just guarding against re-entry.

## Question 2 — the residual 1,880 (15 tickers): single root cause, confirmed by direct measurement

**All 1,880 rows, across all 15 tickers, are one defect: `_back_adjust_hourly` /
`_split_adjustment_factor` in `capitalscan/jobs/ingest.py` (lines 532-591) double-
adjusts a window of trading days immediately before certain splits' `ex_date`,
because Yahoo's hourly endpoint has, for those specific days, already returned
split-adjusted OHLC — contradicting the fix's premise that "Yahoo's hourly endpoint,
unlike its daily one, does not back-adjust."**

Evidence: for every one of the 271 distinct (ticker, day) reject groups (1,880 bars),
I computed `daily.high / rejected_hourly_high` from the `bar_rejects.payload` values
already stored (i.e., *after* our code's adjustment ran) and compared it to that
ticker's nearest split ratio. They match, almost exactly (all within ~1.5%, most
exact to 3 decimal places), for every single row:

| Ticker | Reject days | Ratio observed | Split ratio | Ex-date |
|---|---|---|---|---|
| ANET | 41 (2024-10-07→12-03) | 4.000 | 4 | 2024-12-04 |
| NFLX | 33 (2025-10-01→11-14) | 10.000 | 10 | 2025-11-17 |
| AMCR | 31 (2025-12-01→2026-01-14) | 0.200 | 0.2 | 2026-01-15 |
| CVNA | 28 (2026-03-30→05-07) | 5.000 | 5 | 2026-05-08 |
| CRWD | 23 (2026-05-29→07-01) | 4.000 | 4 | 2026-07-02 |
| DD (block 1) | 23 (2025-10-01→10-31) | 2.390 | 2.39 | 2025-11-03 |
| DD (block 2) | 18 (2026-05-29→06-23) | 0.333 | 0.333 | 2026-06-24 |
| NOW | 13 (2025-12-01→12-17) | 5.000 | 5 | 2025-12-18 |
| TSCO | 11 (2024-12-05→12-19) | 5.000 | 5 | 2024-12-20 |
| IBKR | 11 (2025-06-03→06-17) | 4.000 | 4 | 2025-06-18 |
| KLAC | 10 (2026-05-29→06-11) | 10.000 | 10 | 2026-06-12 |
| ORLY | 5 (2025-06-03→06-09) | 15.000 | 15 | 2025-06-10 |
| FAST | 4 (sparse: 04-04,21,22,25) | 2.000 | 2 | 2025-05-22 |
| BKNG | 4 (2026-03-30→04-02) | 25.000 | 25 | 2026-04-06 |
| ETR | 1 (2024-12-06) | 2.000 | 2 | 2024-12-13 |

DD is the clean disambiguator for the "multiple splits compounding" candidate: it has
two splits inside the window (ratio 2.39, then 0.333), and each reject block matches
*only its own* split's ratio, not the product (2.39 × 0.333 = 0.796, not observed) —
so compounding is not the bug. `_split_adjustment_factor`'s per-split independent
`factor.where` loop is correct on that count.

### Traced examples

**ANET, 2024-10-07** (from `bar_rejects.payload`, which stores the value *after*
our adjustment): hourly `high = 24.9675`, `low = 24.6938`. Daily `bars` row for the
same date: `high = 99.8700`, `low = 97.5575`. `99.87 / 24.9675 = 4.0004`. Working
backward: the raw value Yahoo returned before our code touched it must have been
`24.9675 × 4 = 99.87` — already equal to the correctly-scaled daily price. ANET's
splits are 2021-11-18 (×4) and 2024-12-04 (×4); `_split_adjustment_factor` correctly
determines `2024-10-07 < 2024-12-04` and divides by 4 — but the raw fetch was *already*
divided by 4 by Yahoo, so the stored value ends up divided by 16 total relative to the
true pre-split price, i.e. 4x too low relative to the (correct) daily figure. The
guard catches exactly this and rejects it. Checked the days immediately before the
reject block (2024-08-07 → 2024-10-04) and confirmed those *are* correctly single-
adjusted (hourly matches daily within noise) — so the raw-unadjusted assumption holds
for the bulk of ANET's pre-split history and only breaks in the ~41 sessions nearest
the ex-date.

**NFLX, 2025-10-01**: hourly `high = 11.79` (post-adjustment, in the reject payload),
daily `high = 117.914`. `117.914 / 11.79 = 10.0012`. NFLX's relevant split is
2025-11-17 at ×10; same mechanism, same conclusion — raw Yahoo hourly was already at
~117.9 (correctly split-adjusted) for this date, and our code's unconditional
`ts.date() < ex_date → divide by 10` over-corrected it.

### Candidates ruled out

- **Pre/post-market bars** — no. Every rejected day still has 6-7 regular-session
  bars (checked counts in the group-by above); nothing outside 09:30-16:00 ET is in
  the reject set, and the ratio match to the split factor across *every* row (not a
  scattered subset) is inconsistent with session-boundary noise.
- **Half-days/holidays** — no. AMCR's 2025-12-24 (a half day, 3 bars instead of 7)
  is inside its reject block but is not the cause; the surrounding full 7-bar days
  reject identically. No clustering on holiday dates specifically.
- **Multiple splits compounding, applied once when it should be twice (or vice
  versa)** — ruled out by DD's two independent, cleanly-separated blocks (see above).
- **Genuine vendor bad ticks (the PGR-style one-off)** — no. A bad tick is a single
  anomalous bar; here every bar on every affected day, for every one of 15 tickers,
  differs from daily by *exactly* the ticker's own split ratio. That precision is a
  systematic adjustment error, not tick noise.
- **Ex-date boundary off-by-one** (`<` vs `<=`) — not the mechanism. An off-by-one
  would misclassify one or two days right at the boundary. Reject blocks run 4-41
  trading days, and the boundary day itself (the last day before `ex_date`, e.g.
  ANET 2024-12-03, NFLX 2025-11-14) is inside the reject block along with everything
  back to several weeks earlier — the comparison direction (`row_dates < ex_date`)
  is doing what the docstring says; the input assumption (raw Yahoo hourly is never
  pre-adjusted before ex_date) is what's false, and only for a window that varies
  per ticker/split (4 to 41 sessions, plus FAST's sparse 4-of-~33 pattern) rather
  than a fixed lookback — this is vendor-side inconsistency in *when* Yahoo's cache
  picks up the adjustment, not a bug in our comparison operator.

## Question 3 — corruption vs. defect, quantified

| Group | Rows | % of 4,425 | Classification | Why |
|---|---|---|---|---|
| BNY | 2,545 | 57.5% | **Corruption** (of a novel kind — not the hypothesized symbol reuse, but genuinely bad vendor data with no fix available in this codebase: no split to apply, no donor identity to correct to) | The guard is right to reject; there is nothing to back-adjust against, and the underlying vendor series for that 18-month window is not a valid price history for BNY under any known transformation. Coverage loss is the correct outcome until/unless the vendor data itself is understood or replaced. |
| 15-ticker residual | 1,880 | 42.5% | **Defect** | The underlying vendor data for these specific rows is fine (already correctly split-adjusted). Our own code corrupts it by adjusting a second time. The guard is doing its job — it is catching corruption our code introduces — but the fix belongs in `_back_adjust_hourly`/`_split_adjustment_factor`, not in accepting the loss. |

Recommendation: **fix the 1,880 (15 tickers), prune-and-flag the 2,545 (BNY).**

For the fix: `_split_adjustment_factor` needs to stop assuming raw Yahoo hourly is
uniformly unadjusted before `ex_date`. The cheapest correct approach, given the guard
already computes the daily-range comparison: for each candidate split whose `ex_date`
falls after a bar's date, check whether *not* dividing already brings that bar within
the guard's tolerance of the matching daily range before applying the factor — i.e.
let the guard's own signal decide per-day whether the vendor pre-adjusted that
window, rather than applying the division unconditionally from `corporate_actions`
alone. (This is a design call for whoever takes the fix, not something I've
implemented — diagnosis only, per instructions.)

For BNY: separate track. `cscan actions --tickers BNY` (queued once the fix task
runs it) still won't produce a split row that explains this, because the mismatch
ratio isn't constant. This needs either accepting the coverage loss going forward, or
someone with vendor access confirming what Yahoo's hourly endpoint is actually
returning for BNY in that window. Also flag: the stale 2024-08-06→2026-02-06 rows
from the pre-guard run (`e42a231b`) are still live in `bars` — see Question 1's last
paragraph — that's a cleanup item independent of the guard question.

## Blast radius on `events` (`config_hash='3e598c59e7d71eae'`)

Two `events` run_ids exist under this config_hash: `backtest_20260802T183304_6b1c5b52`
(243,196 rows, the stable/mainline run) and `backtest_20260803T004905_4de2686b`
(2,920 rows, small, currently live — this is very likely part of the sweep the user
has running right now, so treat these numbers as a snapshot, not final).

**None of the 16 tickers (BNY + the 15) appear at all in the 243,196-row mainline
run** — zero events, any entry_kind. Whatever universe/date filter produced that run
excluded all of them already, independent of this defect.

All exposure is in the small live run (2,920 rows), which is entirely these 16
tickers (nothing else). Within it, `touch_5m`/`touch_30m` null `entry_price`:

| Ticker | events (touch_5m + touch_30m) | null entry_price |
|---|---|---|
| BNY, AMCR, BKNG, CVNA, DD, ETR, FAST, KLAC, NFLX, ORLY, TPL, TSCO | 10-134 each | **100% null**, every one |
| ANET | 206 (103×2) | 84 null (42 per entry_kind, 41%) |
| IBKR | 240 (120×2) | 122 null (61 per entry_kind, 51%) |
| CRWD, NOW | 0 rows found | n/a — no touch_5m/touch_30m events exist for these two under this config at all |

I could not construct a genuine "before" number — no prior `events` run exists for
these tickers to diff against; the only stored `events` rows for them were computed
after this session's hourly re-backfill (bad data included), so "before" isn't
observable from this database. Flagging that gap rather than guessing at it.

## Is this actually load-bearing for the backtest?

Checked `universe.in_trade` history for all 16 tickers (66 quarters where available):

```
ANET   4 of 49 quarters in_trade
DD     1 of 66
IBKR   3 of 66
all other 13 tickers (incl. BNY)   0 of every quarter observed
```

12 of the 16 tickers — including BNY, the single biggest reject count — never clear
`in_trade` in the observed history at all. Their hourly data quality has effectively
no downstream exposure regardless of this bug. ANET and IBKR do clear it
occasionally (and both are the two tickers with the highest per-ticker
`touch_5m`/`touch_30m` null rates below 100%, i.e. they have real non-rejected events
too) — those two are where the defect could plausibly touch a live backtest result.
The other 13 are lower priority for urgency, though the code defect should still be
fixed since it will recur on the next split for any currently-`in_trade` ticker.

## Ruled out / already checked, for the next agent

- Guard tolerance (0.5) is not the problem — every residual ratio is 2.0-25.0,
  nowhere near the tolerance boundary; tightening or loosening 0.5 would not change
  any of these outcomes.
- `corporate_actions` has no duplicate rows for any of the 15 tickers' relevant
  splits (checked ANET, NFLX, DD, KLAC, CRWD explicitly) — not a double-counted-row
  bug.
- The `< ex_date` vs `<= ex_date` comparison direction in `_split_adjustment_factor`
  is correct per its own docstring and consistent with how the *bulk* of each
  ticker's pre-split history resolves correctly (checked ANET's Aug-Oct 2024 window
  explicitly) — the residual is not a comparison-operator bug, it's a false premise
  about vendor behavior near the ex-date.
- Session-timing and holiday-clustering hypotheses checked and rejected (see Q2).
