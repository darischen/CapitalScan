# RESULTS.md

Every result gets recorded here with its `run_id`, **including null results** (ADR 033).

This file exists so that a null result is a recorded finding rather than a conversation that never happens. A project reporting "I tested this rigorously and found no edge, here is the infrastructure that proved it" is stronger than a suspiciously profitable backtest.

**Rules for this file**

- Append, never edit a past entry. Corrections go in a new entry referencing the old one.
- Every quantitative claim cites a `run_id`.
- Every reported rate carries `n_eff` and a confidence interval.
- Negative and inconclusive results are recorded with the same detail as positive ones.
- Holdout is evaluated **once**, at the end, and the result is published whatever it says.

---

## Template

```markdown
### <YYYY-MM-DD> — <short title>

- run_id:       <run_id>
- git_sha:      <sha>
- config_hash:  <hash>
- split:        train | validate | holdout
- universe:     <n tickers>, <date range>

**Question**

<what this run was meant to answer, in one sentence>

**Method**

<config in one paragraph: signal, entry kind, exits, target, stop>

**Result**

| Cell | n | n_eff | p_hit | baseline | edge | CI | q |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

**Benchmarks**

| Arm | Total ret | Sharpe | Max DD | Deployed | Cap. eff. |
|---|---|---|---|---|---|
| Buy and hold | | | | 100% | |
| Random entry (200 reps, mean) | | | | | |
| Signal entry | | | | | |

**Interpretation**

<what this does and does not support>

**Follow-up**

<what to run next, or none>
```

---

## Phase 1 — Data and detection

### 2026-07-31 — Sessions 1-3: schema, indicators, signals and exits

- commits:   through a9247d0
- sessions:  1 (schema), 2 (indicators), 3 (signals + exits)
- state:     241 passed, 1 skipped, 97% coverage on core/ against a 90% gate
- lint:      ruff check, ruff format --check, mypy all clean repo-wide

**Defects found that would have produced wrong output**

*`bar.ticker` silent fallback.* `detect` fell back to `""` when `ticker` was absent
from the bar. `events.ticker` is NOT NULL, so an empty string passes the constraint
and writes an event attributed to no ticker. Nothing would have failed. Now raises
KeyError, covering absent, None, NaN (pandas coerces None to NaN on a float column,
which the first fix missed), and empty string. Resolution runs only after a signal
fires, so scanning stays cheap on the ~96% of bars that are non-events.
Violated DESIGN §3.11.

*NaN equality in SignalHit.* A stochastic-only signal carried `touch_level = nan`.
Since `nan != nan`, structurally identical hits compare unequal, while frozen
dataclasses derive `__hash__` from fields and `hash(nan)` is stable, so those same
hits hash identically. A set keeps both. This was a silent duplicate-event bug
waiting in the session 6 debounce. Fixed in two parts, both required:
`touch_level` is now `float | None`, and debounce keys on `(ticker, signal_date,
bound)` rather than the dataclass. See ADR 090.

**Spec conflicts resolved**

*`cell_id` missing from `events` (session 1).* `v_screen` joined `events` to
`cell_stats` on a column `events` did not have. Adding it would have been wrong
twice: `cell_stats` carries `horizon_days` and `target_pct`, which are report
parameters absent from `events`, so one event maps to many cells. And a live event
carries `split_key = 'holdout'`, so inheriting it into the join would have surfaced
holdout statistics in the screener daily, destroying the once-only guarantee in
ADR 019 and 033. Resolved by making `cell_id` derived via an immutable `cell_key()`
function, with `v_screen` joining on components and hardcoding
`split_key = 'validate'`. Holdout firewall test added. See ADR 088.

*MFE clamping.* TESTS asserted `mae <= 0 <= mfe`, contradicting DESIGN §5.6, whose
`capture_ratio` null-clause exists precisely because MFE goes negative when a
position gaps down and never trades back above entry. Clamping would have made that
clause dead code and inflated every capture ratio. TESTS

### Backfill record

*(Append after session 7. Ticker count, bar count, date range, reject counts by rule, coverage gaps, tickers dropped with reasons.)*

### Indicator verification

*(Append after the external reference check. Five dates × two tickers, computed vs external, max deviation.)*

---

## Phase 3 — Engine validation

### Default config run

*(Append after session 9. Event counts, exit mix, ambiguity rate, hand-inspection notes on the 20 reviewed events.)*

### Entry timing sweep

*(Touch vs next-open on the full window. Touch+5m vs touch+30m on the hourly subset only, with the coverage limitation stated.)*

---

## Phase 4 — Statistics

### Statistical self-validation

*(Random-walk null test and known-drift recovery test. These must pass before any real result below is trusted.)*

### Baseline table

*(Per ticker-year, empirical and parametric, with disagreement flags.)*

### Headline grid

*(12 cells, all targets, validate split, with BH correction.)*

### Three-arm comparison

*(Equity curves and summary table.)*

### DCA comparison

*(Fixed schedule vs signal-triggered vs hybrid vs lump sum.)*

### Drawdown slice

*(Edge by drawdown bucket. ADR 015 calls this the project's central claim.)*

### Era breakdown

*(Same cells across 2010-14, 2015-19, 2020-23, 2024-26. A cell appearing in one era only is a regime artifact regardless of pooled significance.)*

### Cluster sequence

*(Does `seq_in_cluster = 2` outperform `seq_in_cluster = 1`? This is the averaging-down question, answered with a number.)*

### Long vs short asymmetry

*(ADR 016's band-walking hypothesis, tested before any short is recommended.)*

---

## Phase 6 — Model

### Calibration

*(Reliability diagrams, Brier, ECE, coverage per quantile, pinball loss. Against the cell-lookup baseline.)*

### Promotion history

*(One entry per model version: what changed, gate results, promoted or held.)*

### Forward log

*(Rolling 90-day calibration. Model vs cell lookup vs reality, out of sample. This is the artifact most projects cannot show.)*

---

## Holdout

**Evaluated once. Published whatever it says.**

*(Empty until the end. Do not look.)*

---

## Kill criteria status

| Criterion | Threshold | Status |
|---|---|---|
| No cell beats baseline at sufficient `n_eff` after FDR | — | Not yet evaluated |
| Validation edge under half of training edge | — | Not yet evaluated |
| Holdout edge negative | — | Not yet evaluated |

If any fires, record it here with the same detail as a positive result, then pivot per ADR 033: keep the engine, swap the input signal. Volatility term structure, earnings drift, cross-sectional momentum residuals, and volume-price divergence are all testable in the same framework with no rewrite.
