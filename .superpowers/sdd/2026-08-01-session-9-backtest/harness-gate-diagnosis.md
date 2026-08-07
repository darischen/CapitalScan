# Phase 3 harness gate diagnosis — HEAD c5e588f, config_hash 3e598c59e7d71eae

Run: `cscan backtest --tickers AAPL,MSFT --workers 1`, 9,112 rows (2,278 signals x 4 entry kinds).

| Check | Verdict | Root cause |
|---|---|---|
| `no_lookahead` | PASS, trustworthy | n/a |
| `entry_sanity` | **ENGINE bug** | `TOUCH_5M` interpolates from a `touch_level` that can sit outside the day's traded range on a gap day |
| `exit_sanity` | PASS, trustworthy | n/a |
| `return_identity` | **HARNESS bug** | 1e-9 tolerance is unsatisfiable across a `numeric(12,4)` price round-trip; needs a derived, price-scaled tolerance |
| `non_overlap` | **HARNESS bug** | check doesn't dedupe the 4 entry-kind rows sharing one cluster head before running the gap test |

All three failures were verified by direct SQL against the live `events`/`bars` rows for this run, not by reading code alone.

---

## 1. `return_identity` — HARNESS bug, tolerance is structurally unsatisfiable

**Evidence.** Recomputing `gross_ret` in SQL directly from stored `entry_price`/`exit_price` and diffing against stored `gross_ret`:

```
max_abs_diff = 0.0000098813   avg_abs_diff = 0.0000004595
rows > 1e-4: 0     rows > 1e-6: 355     rows > 1e-9 (i.e. harness violations): 3838
```

The 10 largest-diff rows are all AAPL, all `signal_date` in Jan-Mar 2010, all with `entry_price` between $6.87 and $8.02:

```
ticker side  signal_date entry_kind entry_price exit_price gross_ret  recomputed                diff
AAPL   long  2010-02-04  next_open       6.8817     7.1569  0.040000  0.03999011872066495197   9.881e-6  <- max
AAPL   short 2010-02-23  next_open       7.0775     7.3046 -0.032097 -0.03208760155422112328  -9.398e-6
AAPL   short 2010-03-04  touch           7.5077     7.7089 -0.026808 -0.02679915286972042037  -8.847e-6
```

`min(entry_price)` over the whole run is $6.8725 (AAPL, split-adjusted, pre-2014 7:1 and 2020 4:1 splits collapse the 2010 price into single digits). `max(entry_price)` is $555.06 (MSFT, 2026).

**Derivation.** `entry_price`/`exit_price` are `numeric(12,4)`: each independently rounds the engine's full-precision float to the nearest $0.0001, an error of at most `±5e-5`. `gross_ret` is `numeric(12,6)`: it is written from the engine's full-precision `(exit-entry)/entry`, independently rounded to the nearest `1e-6`, error at most `±5e-7`.

The harness's `_check_return_identity` does not have access to the engine's original float64 prices — it reads `entry_price`/`exit_price` back off Postgres (`_load_events_for_run` in `jobs/cli.py`, a plain `SELECT *`), i.e. it recomputes from the **already-rounded** prices, then compares to the **separately, already-rounded** stored `gross_ret`. These are two independent roundings of two different quantities, not one value re-derived from itself.

For a long position with entry `E`, exit `X`, rounding errors `e_E, e_X` (`|e_E|,|e_X| ≤ 5e-5`):

```
G_recomputed = (X' - E') / E'   where X' = X + e_X, E' = E + e_E
G_true       = (X - E) / E
```

First-order:

```
ΔG ≈ (e_X - e_E)/E - G · e_E/E
```

The dominant term is `(e_X - e_E)/E`, bounded by `(|e_X| + |e_E|)/E ≤ 1e-4 / E`. Adding the stored `gross_ret`'s own `5e-7` rounding budget:

```
tolerance(E) = 1e-4 / E + 5e-7
```

At `E = 6.8725` (the dataset minimum): `tolerance = 1.455e-5`. The observed max diff, `9.881e-6`, is comfortably under this bound and was produced at almost exactly this `E`. The 355 rows exceeding `1e-6` are consistent with `1e-4/E > 1e-6 ⇔ E < $100` — plausible for AAPL's split-adjusted price for a multi-year stretch before 2014, implausible for MSFT (never traded under ~$20 split-adjusted in this window), which is exactly the ticker/era split the top-10 table shows (all AAPL, all 2010).

**Verdict: harness wrong.** The 1e-9 tolerance in DESIGN §5.10 was written assuming a same-precision round-trip; it does not hold once entry/exit prices go through `numeric(12,4)` storage and get read back for recomputation. No row exceeds the derived, price-scaled bound — there is no evidence of an engine defect here.

**Recommended fix.** Replace the fixed `1e-9` with `tolerance(E) = 1e-4/E + 5e-7`, evaluated per-row against that row's own `entry_price` (never a fitted constant, and never picked to match this run's own 9.88e-6 — CLAUDE.md's rule against calibrating a check to its own measured output). Cite this derivation in DESIGN §5.10 and in the check's docstring so the next person doesn't reintroduce `1e-9`.

---

## 2. `non_overlap` — HARNESS bug, missing dedup across entry kinds

**Evidence.** `is_cluster_head = true` rows for this run:

```
n_head_rows = 2128
n_distinct_head_signals (ticker, side, signal_date) = 532
```

`2128 = 4 x 532` exactly — every cluster head signal produced exactly 4 rows (one per `entry_kind`), all sharing the identical `cluster_id`, `signal_date`, `ticker`, `side`. Sample (AAPL long):

```
ticker side signal_date entry_kind cluster_id          seq_in_cluster is_cluster_head
AAPL   long 2010-01-22  touch      415276157036450714  1              t
AAPL   long 2010-01-22  next_open  415276157036450714  1              t
AAPL   long 2010-01-22  touch_5m   415276157036450714  1              t
AAPL   long 2010-01-22  touch_30m  415276157036450714  1              t
```

`_check_non_overlap` groups by `(ticker, side)` — correct per Ruling C5 — but does **not** further group or dedupe by `signal_date`/`cluster_id` before sorting and walking consecutive pairs. Four rows at the identical `signal_date` sort adjacently and produce 3 zero-gap consecutive pairs (`gap = 0 ≤ max_hold_days` fires the `cluster_head_windows_overlap` violation), even though they are the same underlying event, not two overlapping clusters.

Per group of `H` distinct head signals with 4 entry-kind rows each, the walk produces `4H - 1` total consecutive pairs: `3H` of them are always the zero-gap same-signal duplicates, and `H - 1` are genuine cross-cluster pairs. Summed over all `(ticker, side)` groups, `3 x sum(H_g) = 3 x 532 = 1596` — **exactly** the reported violation count. That means every single one of the 1596 reported violations is this duplicate-row artifact; zero residual violations are left over for a genuine cross-cluster overlap.

**Checked and ruled out:** the entry-lag hypothesis in the brief (NEXT_OPEN's t+1 entry offset letting a position from a 01-22 signal stay open through the 02-01 signal date, since `_trading_bars_between` measured a gap of exactly 6 there). This does **not** hold up: because `_trading_bars_between` is an ordinal distance in the trading-date list, and `NEXT_OPEN`'s entry date is always `signal_date`'s trading-bar successor for **both** signals being compared, the offset cancels — `gap(entry_A, entry_B) = gap(signal_A, signal_B)` exactly, for any constant per-kind offset. Re-deriving the true holding-window arithmetic: entry bar `E`, terminal exit no later than `E+5` (§5.5's `i == t+5` timeout), i.e. the window spans `[E, E+5] = [S+1, S+6]` in signal-relative terms for `NEXT_OPEN`. A new signal at `S' = S+6` lands exactly on the old window's last bar — right at the boundary tag_clusters already treats as "new cluster" (`gap > max_hold_days` i.e. `gap ≥ 6`). Whether that boundary case is a real statistical overlap is a judgment call (same calendar bar, but the old position already exits at that bar's close and the new position's own entry is the bar after), but it is irrelevant here: the actual data shows **zero** cross-cluster pairs at `gap ≤ 5` once duplicates are removed, so this boundary question never triggers a violation in this run either way.

**Verdict: harness wrong.** `tag_clusters` is self-consistent by construction — it can never emit two heads with `gap ≤ max_hold_days`, so a correctly-deduped `non_overlap` check would pass trivially against this run's data. The 1596 "violations" are 100% a grouping bug in the check, not a signal of anything wrong in `tag_clusters` or the backtest engine.

**Recommended fix.** Dedupe `heads` to one row per `(ticker, side, signal_date)` — or equivalently per `cluster_id` — before the sort/walk, since the check's actual unit of interest is the cluster head event, not the `events` table's `(config_hash, ticker, signal_date, signal_type, entry_kind)` row grain. This is the same defect class as the earlier `ticker`-vs-`(ticker, side)` grouping bug already fixed in this harness (per the brief): the checker's grouping didn't match the contract of the thing it's checking, this time at the entry-kind axis instead of the side axis.

---

## 3. `entry_sanity` — ENGINE bug, confined to `TOUCH_5M`

**Evidence, slippage-explained majority.** Reversing slippage (`raw = price / (1 ± 3bps)`, `CostParams.slippage_bps = 3.0`) and re-checking against the daily bar's `[low, high]`:

```
entry_kind | raw violations | survivors after slippage reversal
next_open  | 72             | 0
touch      | 15             | 0
touch_30m  | 1              | 0
touch_5m   | 40             | 37
```

All 37 surviving violations are `touch_5m`. All 37 have `entry_gapped = true`. All 37 have `touch_level` **outside** the entry date's daily `[low, high]` range (verified by direct join, not sampled).

**Root cause.** `core/returns.py::entry_price_for`, `TOUCH_5M`/`TOUCH_30M` branch, calls `_first_hourly_touch(hourly, touch_level, side)`:

```python
def _first_hourly_touch(hourly, touch_level, side):
    price_col, bound = ("low", Bound.LOWER) if side is Side.LONG else ("high", Bound.UPPER)
    for _, hbar in hourly.iterrows():
        if _breach(float(hbar[price_col]), touch_level, bound):
            return hbar
    return None
```

On a normal day, `touch_level` sits inside the day's range and this correctly finds the hourly bar where price first crosses it. On a **gap day** — the open already trades through `touch_level` before the session starts — `touch_level` is outside the entire day's `[low, high]`, so the breach condition is trivially true on the **very first** hourly bar of the session (its `low`/`high` already satisfies `_breach` against a level the whole day traded past). `_first_hourly_touch` then returns that first bar regardless of whether a genuine intraday "touch" happened.

`TOUCH_5M` then does:

```python
weight = 5/60
return touch_level + (close - touch_level) * weight
```

interpolating between `touch_level` (a level the market never actually traded at that session) and the first hourly bar's close. Because `touch_level` is the dominant term (weight only `0.083` toward `close`), the result stays close to `touch_level` — which is outside the day's real range — so the computed fill price is itself outside the day's real range.

**Traced example — the largest violator.**

```
MSFT short, signal_date = 2025-07-31, entry_date = 2025-07-31, entry_kind = touch_5m
touch_level (t-1 bb_upper) = 517.9528
daily bar 2025-07-31:  open=555.2300  high=555.4500  low=531.9000  close=533.5000
entry_price (stored, with slippage) = 519.4426
raw (slippage-reversed)             = 519.5985
```

`touch_level = 517.9528` is **below the entire day's range** (`low = 531.90`) — the stock gapped up hugely overnight, opening at `555.23`, and traded down all day, never coming close to the previous day's upper-band level. Because `side = short` uses `high >= touch_level` as the breach test, and `517.95` is below even the day's `low`, every hourly bar's `high` trivially clears it — `_first_hourly_touch` returns the **first** hourly bar of the session. Interpolating `touch_level (517.95) → first_hbar.close (~537.75)` at weight `0.083` lands near `519.60` — 12.30 below the day's actual `low` of `531.90`.

This is the exact scenario `TOUCH`'s gap rule already exists for (§5.4: `P_entry = open_t if open_t ≤ L else L` for a long, mirrored for a short) — `TOUCH_5M`/`TOUCH_30M` have no equivalent branch.

`TOUCH_30M` is exposed to the same mis-selection of the first hourly bar, but since it returns that bar's `close` directly (no blend with `touch_level`), the result is still a real traded price from inside the session and almost always lands inside the daily range anyway — consistent with it contributing 0 of the 37 survivors (its 1 raw pre-slippage-reversal violation didn't survive the reversal, i.e. was within the `1e-4` tolerance, most likely an ordinary rounding/boundary case, not this mechanism).

**One defect, not several.** All 37 survivors share the identical signature: `entry_gapped = true` and `touch_level` outside the daily bar's range, with violation magnitude scaling with how far `touch_level` sits outside that range (times the fixed `5/60` weight). The candidates raised in the brief that are *not* the cause: hourly bar belonging to a different session (checked — the AAPL 2024-08-06 hourly bars are a complete, correctly-timestamped 13:30-19:30 UTC session; this is also not near the coverage boundary story once you see the same defect on 2025/2026 dates far from 2024-08-06), and hourly/daily adjustment-basis mismatch (not needed to explain any of the 37 — the gap-through-band mechanism alone accounts for all of them exactly).

**Verdict: engine bug.** `entry_price_for`'s `TOUCH_5M`/`TOUCH_30M` branch (and `_first_hourly_touch`) needs the same gap-fill logic `TOUCH` has: when the touch level sits outside the entry date's actual traded range (equivalently, `entry_gapped` would be true for this kind too), fill at the first available real traded price for that side (e.g. the session's first hourly bar's open, mirroring `TOUCH`'s `open_t` gap fill) instead of interpolating toward a level the market never reached.

**What it corrupts.** `TOUCH_5M` `entry_price`, and therefore `gross_ret`, `net_ret`, `mfe`, `mae`, and every downstream statistic for the `touch_5m` entry kind specifically, on any gapped signal since hourly coverage began (2024-08-06 onward — DESIGN §5.4's coverage window). `NEXT_OPEN`, `TOUCH`, and `TOUCH_30M` are unaffected; this is confined to one entry kind's gap-day fills.

---

## Does anything here invalidate `no_lookahead` or `exit_sanity`?

**No, both remain trustworthy.**

- `no_lookahead` reruns `scan_candidates`/`detect` from scratch on `bars_by_ticker` and never reads `entry_price`, `exit_price`, `gross_ret`, or the cluster columns — none of the three defects found (a `TOUCH_5M`-only entry pricing bug, a `non_overlap` grouping bug, a `return_identity` tolerance bug) touch signal detection or the t-1 indicator pairing.
- `exit_sanity` checks `exit_price` against the daily bar only. `core/exits.py`'s exit prices are always one of `open_`, a stop/target level the bar's own extreme reached, or `bar["close"]` — none of them route through `_first_hourly_touch` or hourly interpolation, so the `TOUCH_5M` mechanism above cannot reach exit pricing. Its 100% pass is independent evidence, not a check that happened to dodge the same bug.

## Summary of recommended fixes (not applied — diagnosis only)

1. **`core/returns.py`** — add a gap-fill branch to `entry_price_for` for `TOUCH_5M`/`TOUCH_30M`, mirroring `TOUCH`'s rule, keyed off whether `touch_level` falls outside the relevant hourly/daily range.
2. **`research/harness.py::_check_non_overlap`** — dedupe cluster-head rows to one per `(ticker, side, signal_date)` (or `cluster_id`) before the pairwise gap walk.
3. **`research/harness.py::_check_return_identity`** and **DESIGN §5.10** — replace the fixed `1e-9` tolerance with the derived `1e-4/entry_price + 5e-7` bound, per-row.
