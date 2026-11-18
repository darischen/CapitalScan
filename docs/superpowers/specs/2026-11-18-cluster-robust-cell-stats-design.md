# Cluster-robust intervals in `cell_stats` (ADR 166, amended)

Owner decision 2026-11-18: option A from DECISIONS Open items, with
month blocks and a placebo-calibrated inflation. Evidence: RESULTS
2026-11-18 and `scripts/hist/neff-correction-test-2026-11-18/`.

## Why

`cell_stats` builds every interval from ADR 098's
`n_eff = n / (1 + rho_bar (k_bar - 1))`, with `k_bar = mean(cofire_count)`.
`cofire_count` counts every ticker in the generation firing that signal
type that day (about 170). A cell's own events share a date with 4 to 7.
A placebo test on 127,787 null draws measured the shipped SE at 5.4x the
true sampling error. It rejected a true null 0 times. The site suppresses
40 of 64 train and 64 of 64 validate cell-targets for being too thin when
the data supports all of them.

## Goals

- Every pooled and era row in `cell_stats` gets its interval, p-value
  and `n_eff` from a two-way cluster-robust variance (Cameron-Gelbach-Miller)
  over every fire, clustered by calendar month and by `cluster_id`.
- `n_eff` becomes derived: `p (1 - p) / V`. It keeps its column, name and
  reader meaning (invariant 8).
- Each run measures a per-scope inflation factor with the placebo test and
  applies it to the SE. Validate measured about 1.18x, train about 1.0x.
- The `is_cluster_head` filter leaves `cell_stats`.
- BH re-runs on the new p-values. ADR 112's verdict gets restated on them.

## Non-goals

- `benchmarks.py` keeps its head filter. It becomes a BACKLOG follow-up.
- The holdout split stays untouched. Nothing here loads it.
- No change to `events`, `cofire_count`, `rho_era` or the model.
- `config_hash` does not move.

## Anti-goals

- An interval a reader can't rebuild from stored columns. The factor and
  the cluster counts get stored next to the row.
- A date-block estimator. The placebo measured 11.5% false rejections
  on train and 17% on validate.
- Any silent change on serving before research is checked.

## Approaches considered

| | What | Verdict |
|---|---|---|
| A | ADR 166: two-way robust variance over every fire, `n_eff` derived | Chosen, with month blocks instead of dates |
| B | Keep ADR 098, replace `k_bar` with the cell's own same-day count | Rejected. Ignores dependence across dates, which the placebo showed matters |
| C | Read-only tests first | Done 2026-11-18. Produced the evidence above |

Estimator choice within A, from the placebo (train SE/SD, two-sided
false rejections): month blocks 1.04 / 4.7%, Driscoll-Kraay lag 5 0.99 / 5.4%,
quarter blocks 1.03 / 4.7% but worst on validate. Month blocks win on
explicability: two group sums and an intersection.

Validate shortfall: placebo inflation (chosen), t critical with months - 1
degrees of freedom (1.06x, too small), or ship raw (rejected).

## Design

### Settings

`core/config.py` gets a standalone `VarianceParams` dataclass, not a field
of `Config`, matching `RhoParams`, `BaselineParams` and `SweepParams`:

- `time_block = "month"`
- `placebo_draws = 1000`
- `placebo_min_shift_days = 60`
- `placebo_window = ("2010-01-04", "2023-12-15")`, inside train and validate
- `placebo_seed`
- `inflation_floor = 1.0`: the factor never narrows an interval

### `core/` (pure, tests first)

- `cluster_robust_variance(y, block, cluster) -> (v, v_block, v_cluster)`:
  variance of the mean. Falls back to the larger one-way term when the
  two-way sum is not positive.
- `robust_n_eff(p, v)`: `p (1 - p) / v`, `nan` when `v <= 0`.
- `placebo_inflation(se_draws, edge_draws)`: median(SD of edges / median SE)
  across cell-targets, floored at `inflation_floor`.
- `wald_interval(p, se, factor)`, clipped to [0, 1].
- `one_sided_p_value` stays. The caller passes the robust `n_eff` after
  inflation, so the test and the interval agree.

### `research/cell_stats.py`

- `load_grid_events` keeps the head filter for `benchmarks`, gains
  `cluster_id`, and `cell_stats` calls it with `cluster_heads_only=False`.
- `_cell_row` swaps `cell_n_eff` for the robust variance on hit flags.
  `mean_cofire` stays as a diagnostic.
- A new `measure_inflation(engine, events, cfg, vp)` runs the placebo once
  per scope (split pooled, and each reported era) from the adj-close panel
  `build_baselines` already loads, and returns one factor per scope.
  Placebo hit = `1[dir * close(t+5)/close(t) - 1 >= target]` minus the
  ticker-year baseline, as in `placebo_estimators.py`.
- Suppression stays `n_eff < min_n_eff`, now on the inflated robust `n_eff`.

### Schema (one Alembic migration, both stores)

`cell_stats` gains:

- `variance_method text`: `'cgm_month_cluster'`, null on old rows
- `n_time_blocks int`, `n_clusters int`
- `se_inflation numeric(8,4)`: the factor applied

Old rows stay readable. No view changes are needed for the numbers.

### Web copy (approved 2026-11-18)

`web/components/Research.tsx` carries two headers that become false: the
`n_eff` tooltip ("after ADR 098's autocorrelation correction") and the CI
header ("Wilson interval on the hit rate"), plus the `RateBar` docstring.
They change to name the month x cluster Wald interval and ADR 209. The
`?stats=1` default stays off (ADR 114). Nothing else on the site changes
shape: the validate grid, the kill banner, the era table, the drawdown
slice and the screener's Cell column fill in with numbers where they show
suppress reasons today.

### Run order

1. Unit tests and the four CI gates.
2. Migration on research and serving.
3. `cscan cell-stats --split train` and `--split validate` on `wivie`,
   generation `f183b0f5209a4677`. Compare against the stored rows.
4. `cscan sync`, then `cscan db sync-config`, then restart
   `capitalscan-web`. Count rendered rows on `/research` before and after.

### Documents

- ADR 209: amends 166 (month blocks, placebo inflation), supersedes 098's
  `k_bar` correction, restates 112 on the new p-values.
- RESULTS 2026-11-18: the placebo section.
- BACKLOG: close "A serial `n_eff`", add the `benchmarks` follow-up.
- DECISIONS Open items: mark ADR 166 resolved.

## Left to the builder

- How the placebo shares the adj-close panel with `build_baselines`
  without a second load.
- Whether era rows get their own factor or inherit the split's. Measure
  both. Inherit if they agree within 5%.
- Runtime budget: the script took 2m40s for both splits at 12 workers.
  `cell-stats` should stay under 10 minutes on `wivie`.
