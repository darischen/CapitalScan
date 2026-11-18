# Cluster-robust `cell_stats` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cell_stats` intervals, p-values, suppression and `n_eff` come from a two-way cluster-robust variance (month x `cluster_id`) over every fire, inflated by a per-scope placebo factor.

**Architecture:** Pure estimators in a new `core/variance.py`. A new `research/placebo.py` measures the inflation factor from the adj-close panel. `research/cell_stats.py` swaps ADR 098's `cell_n_eff` for both and drops the head filter. One migration adds four provenance columns. `config_hash` does not move.

**Tech Stack:** Python 3.13, pandas, numpy, scipy, Alembic, Next.js (copy only).

**Spec:** `docs/superpowers/specs/2026-11-18-cluster-robust-cell-stats-design.md`. Evidence: `scripts/hist/neff-correction-test-2026-11-18/placebo_estimators.py` (the reference implementation of every estimator and of the placebo).

## Global Constraints

- `core/` performs no IO and imports nothing from `jobs/` or `research/` (invariant 1). `core/config.py` imports only `dataclasses` (invariant 10).
- `VarianceParams` is a standalone dataclass, never a field of `Config`. `config_hash(resolve_config())` must still return `f183b0f5209a4677`.
- `time_block = "month"`, `placebo_draws = 1000`, `placebo_min_shift_days = 60`, `placebo_window = ("2010-01-04", "2023-12-15")`, `placebo_seed = 20261118`, `inflation_floor = 1.0`, `placebo_workers = 8`.
- The holdout split is never loaded. The placebo window ends 2023-12-15.
- Never run bare `pytest`. Only `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.
- All four CI gates before every commit that ends a task: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, the pytest line with `--cov=capitalscan/core --cov-fail-under=90`.
- Commits carry no co-author or Claude attribution trailer.
- Workers use spawn semantics: module importable with no side effects, workers open no DB connections (they receive arrays).

## Review Focus

- A cell-target where every fire missed (10% target): variance 0. Expected: `n_eff` null, suppressed with a reason, no exception. Test in Task 1 and Task 4.
- Two-way variance non-positive: falls back to the larger one-way term. Test in Task 1.
- A cell with one month or one cluster: the estimator still returns a finite variance or suppresses. Test in Task 1.
- A placebo draw with under 30 non-null outcomes is skipped, and a scope with no usable draws gets factor `inflation_floor`, logged. Test in Task 3.
- Era rows: factor per era, inherited from the split when within 5%. Test in Task 3.

---

### Task 1: Pure estimators and `VarianceParams`

**Files:**
- Modify: `capitalscan/core/config.py` (add `VarianceParams` after `RhoParams`)
- Create: `capitalscan/core/variance.py`
- Test: `capitalscan/tests/unit/test_variance.py`

**Interfaces:**
- Produces:
  - `VarianceParams` with the Global Constraints values.
  - `cluster_robust_variance(y: np.ndarray, block: np.ndarray, cluster: np.ndarray) -> tuple[float, float, float]` returning `(v, v_block, v_cluster)`, variance of `mean(y)`. CGM: `v_block + v_cluster - v_intersection`, each `sum_g (sum_{i in g} r_i)^2 / n^2` with `r = y - mean(y)`. If `v <= 0`, `v = max(v_block, v_cluster)`.
  - `month_block(dates: pd.Series) -> np.ndarray` (int64, `year * 12 + month - 1`).
  - `robust_n_eff(p: float, v: float) -> float | None`: `p (1 - p) / v`, `None` when `v <= 0` or `p` in {0, 1}.
  - `inflate(v: float, factor: float) -> float`: `v * factor**2`.
  - `wald_interval(p: float, v: float, z: float = 1.959963984540054) -> tuple[float, float]`, clipped to [0, 1].
  - `inflation_factor(placebo_edges: np.ndarray, placebo_ses: np.ndarray, groups: np.ndarray, floor: float) -> float`: per group, `SD(edges) / median(ses)`; returns `max(floor, median over groups)`.

- [ ] **Step 1: Write the failing tests** in `test_variance.py`:
  - `test_iid_reduces_to_textbook`: 10,000 iid Bernoulli(0.3), every block and cluster unique; `v` within 5% of `0.21 / 10000`.
  - `test_perfectly_clustered_month`: 50 months x 100 identical outcomes per month; `robust_n_eff` within 10% of 50.
  - `test_negative_two_way_falls_back`: construct a case where `v_block + v_cluster - v_inter <= 0`; returns `max(v_block, v_cluster)`.
  - `test_zero_variance_gives_none`: all zeros → `robust_n_eff(0.0, 0.0) is None`.
  - `test_single_month_single_cluster_finite`: all fires in one month and one cluster; `v >= 0`, no exception.
  - `test_wald_clips`: `wald_interval(0.01, 0.01)[0] == 0.0`.
  - `test_inflation_floor`: SD/SE ratios of 0.8 everywhere → `inflation_factor(..., floor=1.0) == 1.0`; ratios of 1.2 → 1.2 within 1e-9.
  - `test_matches_reference_script`: a seeded synthetic panel run through `cluster_robust_variance` equals `cgm_se(...)**2` copied from `placebo_estimators.py` to 1e-12.
  - `test_variance_params_not_in_config`: no field of `Config` (via `dataclasses.fields`) has type `VarianceParams`, so `config_hash` cannot move.
- [ ] **Step 2: Run** `uv run pytest capitalscan/tests/unit/test_variance.py -v`. Expected: FAIL, module missing.
- [ ] **Step 3: Implement** `VarianceParams` and `core/variance.py`. Use `np.unique(..., return_inverse=True)` plus `np.bincount` for group sums, as the reference script does.
- [ ] **Step 4: Run** the test file. Expected: PASS. Then all four gates.
- [ ] **Step 5: Commit** `feat(core): cluster-robust variance estimators and VarianceParams (ADR 209)`.

### Task 2: Migration adding provenance columns

**Files:**
- Create: `db/migrations/versions/<rev>_cell_stats_variance_provenance.py`, `down_revision` = current head (`cscan db status`).

**Interfaces:**
- Produces: `cell_stats.variance_method text NULL`, `cell_stats.n_time_blocks int NULL`, `cell_stats.n_clusters int NULL`, `cell_stats.se_inflation numeric(8,4) NULL`.

- [ ] **Step 1: Write** the migration by hand (no `--autogenerate`). `upgrade()`: four `ALTER TABLE cell_stats ADD COLUMN`. `downgrade()`: four `DROP COLUMN`. The module docstring states why (ADR 209) and that `v_stats` needs no change because it lists columns explicitly.
- [ ] **Step 2: Verify** on the workstation copy only: `cscan db migrate --research-only` (or the single-target flag `cscan db migrate --help` names), then `\d cell_stats` shows the four columns, then `cscan db rollback --yes`, `\d` shows them gone, then migrate again.
- [ ] **Step 3: Run** the four gates (catches E501 in `db/migrations/`).
- [ ] **Step 4: Commit** `feat(db): cell_stats variance provenance columns (ADR 209)`.

### Task 3: Placebo inflation factor

**Files:**
- Create: `capitalscan/research/placebo.py`
- Test: `capitalscan/tests/unit/test_placebo.py`

**Interfaces:**
- Consumes: Task 1's `cluster_robust_variance`, `month_block`, `inflation_factor`, `VarianceParams`.
- Produces:
  - `null_edge_matrix(panel: pd.DataFrame, target: float, direction: int, horizon: int, window: tuple[str, str]) -> tuple[pd.DatetimeIndex, list[str], np.ndarray]`: dates x tickers float32 of `1[dir * fwd_h >= target] - ticker_year_mean`, NaN where undefined. Same definition as `placebo_estimators.build`.
  - `measure_inflation(panel: pd.DataFrame, events: pd.DataFrame, sp: StatsParams, vp: VarianceParams, horizon: int, scopes: dict[str, pd.Series]) -> dict[str, float]`: `scopes` maps a scope name (`"pooled"`, each era) to a boolean mask over `events`. For each scope, every headline cell x target in it is shifted `vp.placebo_draws` times (circular within `vp.placebo_window`, shift uniform in `[min_shift, T - min_shift]`), the month x cluster SE and edge recorded, and `inflation_factor` returned per scope. Era factor within 5% of pooled is replaced by pooled. Parallel over cell-targets with `ProcessPoolExecutor(vp.placebo_workers)`, matrices handed to workers through `np.save` + `mmap_mode="r"` in a temp dir.

- [ ] **Step 1: Write failing tests:**
  - `test_null_edge_mean_zero`: synthetic 3-ticker 2-year panel; the per-ticker-year mean of the matrix is 0 to 1e-6.
  - `test_iid_panel_factor_near_one`: synthetic panel with iid returns and independent events, 200 draws, workers=1; factor in [1.0, 1.15].
  - `test_common_shock_panel_factor_floor`: synthetic panel where all tickers share returns, events clustered by date; factor returned is finite and ≥ `inflation_floor`.
  - `test_sparse_draws_skipped`: a cell with 10 events yields no draws; that scope returns `inflation_floor`.
  - `test_era_inherits_pooled_within_5pct`.
  - `test_deterministic`: two calls with the same seed return identical dicts.
- [ ] **Step 2: Run** `uv run pytest capitalscan/tests/unit/test_placebo.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** by porting `placebo_estimators.work` and `build`, restricted to the month x cluster estimator. Keep `if __name__ == "__main__"` out of the module; the worker function is top-level.
- [ ] **Step 4: Run** tests and all four gates. Expected: PASS.
- [ ] **Step 5: Commit** `feat(research): placebo-calibrated variance inflation (ADR 209)`.

### Task 4: `cell_stats` on the robust estimator

**Files:**
- Modify: `capitalscan/research/cell_stats.py` (`GRID_CLUSTER_HEADS_ONLY`, `CELL_STATS_COLUMNS`, `load_grid_events`, `_cell_row`, `compute_grid`, `run_cell_stats`)
- Modify: `capitalscan/jobs/cli.py` (the `stats cells` command: `--placebo-workers` option)
- Modify: `capitalscan/research/benchmarks.py` only to keep passing `cluster_heads_only=True` explicitly
- Test: `capitalscan/tests/unit/test_cell_stats.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `compute_grid(..., inflation: float = 1.0)`; `run_cell_stats(..., vp: VarianceParams | None = None)`. Rows carry `variance_method = "cgm_month_cluster"`, `n_time_blocks`, `n_clusters`, `se_inflation`.

Row semantics in `_cell_row`:
- Population: every fire (`cluster_heads_only=False` for `cell_stats`; `benchmarks` keeps heads). `load_grid_events` selects `cluster_id`.
- `p_hit` over non-null hit flags. `v = inflate(cluster_robust_variance(hits, month_block(dates), cluster_codes)[0], inflation)`.
- `n_eff = robust_n_eff(p_hit, v)`. `None` → suppressed, reason `"no variance: every fire {hit|missed}"`, `n_eff` stored as 0.
- Suppression: `suppression_reason(n_eff, sp)` unchanged.
- `ci_low, ci_high = wald_interval(p_hit, v)`. `p_value_parametric = one_sided_p_value(p_hit, baseline_empirical, n_eff)`.
- `mean_cofire` still stored. `cell_n_eff` stays defined (selfvalidation and the integration test read it) but `_cell_row` no longer calls it.
- `run_cell_stats` builds the panel once, passes it to `build_baselines` (refactor to accept a panel) and to `measure_inflation`, and passes each scope's factor into `compute_grid`.

- [ ] **Step 1: Update and add tests:**
  - Replace assertions that pin ADR 098's `n_eff` in `_cell_row` with: `test_n_eff_is_robust` (a cell of 600 fires across 60 months, 10 per month perfectly correlated within month → `n_eff` within 10% of 60 at `inflation=1.0`).
  - `test_inflation_widens_interval`: same cell, `inflation=1.2` → interval width ratio 1.2 within 1e-9, `n_eff` ratio `1/1.44`.
  - `test_all_miss_cell_suppressed`: every hit flag False → `suppressed`, reason starts `"no variance"`, no exception.
  - `test_every_fire_counted`: `n_events` equals fires, not heads.
  - `test_provenance_columns`: `variance_method == "cgm_month_cluster"`, `n_time_blocks` and `n_clusters` equal the distinct counts, `se_inflation` equals the passed factor.
  - `test_benchmarks_keep_heads`: `benchmarks` load still filters `is_cluster_head`.
- [ ] **Step 2: Run** `uv run pytest capitalscan/tests/unit/test_cell_stats.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the changes above.
- [ ] **Step 4: Verify on the workstation copy**, `--no-write`: `uv run cscan stats cells --config-hash f183b0f5209a4677 --split-key validate --no-write` and `uv run cscan stats cells --config-hash f183b0f5209a4677 --split-key train --no-write`. Expected: validate 0 suppressed at the 3% target, factor about 1.18 pooled; train factor about 1.0; survivors at q < 0.05: train 4-6, validate 0. Runtime under 10 minutes each.
- [ ] **Step 5: Run** all four gates. **Commit** `feat(stats): cell_stats on month x cluster robust variance (ADR 209)`.

### Task 5: Web copy

**Files:**
- Modify: `web/components/Research.tsx:56-80` (RateBar docstring), `:133` (n_eff tooltip), `:138` (CI header title)

- [ ] **Step 1: Edit** the three strings. Tooltip: `"effective sample size implied by the month x cluster robust variance (ADR 209)"`. CI header: `"95% Wald interval on the hit rate from a month x cluster robust variance, with the baseline marked. No edge interval is stored."` Docstring: replace the `wilson_ci(p_hit * n_eff, n_eff)` sentence with the same description.
- [ ] **Step 2: Run** `cd web && npm test` and `npx next build`. Expected: both pass, `.next/BUILD_ID` exists.
- [ ] **Step 3: Commit** `docs(web): research grid copy names ADR 209's interval`.

### Task 6: Records

**Files:**
- Modify: `docs/DECISIONS.md` (ADR 209 after 208; index row; Open items row for ADR 166 struck through and resolved; ADR 166 and 098 status lines point to 209)
- Modify: `docs/RESULTS.md` (2026-11-18 entries: correction test and placebo)
- Modify: `docs/BACKLOG.md` ("A serial `n_eff`" closed; new item: `benchmarks` head filter)
- Modify: `docs/DESIGN.md` §6.3 (formula replaced, pointer to ADR 209)
- Add: `scripts/hist/neff-correction-test-2026-11-18/` (scripts and CSVs, no parquet over 10 MB)

- [ ] **Step 1: Write** the entries. ADR 209 states: decision (owner, 2026-11-18), estimator, inflation, no `config_hash` move, the placebo table, ADR 112's verdict restated on the new p-values.
- [ ] **Step 2: Commit** `docs: ADR 209, placebo results, backlog`.

### Task 7: Ship

- [ ] **Step 1:** Push, open PR, CI green, merge.
- [ ] **Step 2:** On `wivie`, check no job is running (`systemctl list-timers`, `pg_stat_activity`), `git pull`, `uv run cscan db migrate` (both stores), read the output for both targets.
- [ ] **Step 3:** `uv run cscan stats cells --config-hash f183b0f5209a4677 --split-key train` then `uv run cscan stats cells --config-hash f183b0f5209a4677 --split-key validate` on `wivie`.
- [ ] **Step 4:** `uv run cscan sync`, then `uv run cscan db sync-config`, then on the Pi `git pull`, `npx next build`, confirm `.next/BUILD_ID`, then `sudo systemctl restart capitalscan-web` as a separate command.
- [ ] **Step 5: Verify** by rendered rows: `/research` validate grid: 15 of 16 rows at 3% render numbers and 1 suppresses (`bear_close_above_upper` short 10-20, n_eff about 23); the banner reads `0 of 15`. Serving `SELECT count(*) FROM cell_stats WHERE variance_method IS NOT NULL` equals research's.
