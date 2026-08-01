# Session 9 — Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `research/backtest.py`, a deterministic function from config to an enriched event table, so Phase 3's gate can run.

**Architecture:** The engine is **orchestration, not new logic**. Every hard decision already lives in `core/` and is covered by the five load-bearing tests: `core.signals.detect`, `core.exits.resolve_exit`, `core.returns.entry_price_for` / `mfe_mae` / `forward_returns`, `core.costs.apply_costs`, `core.universe.is_tradeable`. This session wires them together in the fixed order of DESIGN §5.2, adds per-ticker parallelism with deterministic dispatch, and writes to `events`. Writing a second implementation of anything in `core/` is a plan violation (CLAUDE.md invariant 2).

**Tech Stack:** Python 3.11+, pandas (float64), SQLAlchemy + psycopg, `ProcessPoolExecutor` (spawn), pytest, hypothesis.

## Global Constraints

- **`core/` performs no IO.** `research/` owns all IO. Invariant 1.
- **One signal implementation.** Import `core.signals`. Never write a second band comparison. Invariant 2.
- **Indicators read at t−1, never t.** Invariant 3. Enforced again here.
- **Never fill, forward-fill, or interpolate a null.** Drop the row, log the reason. Invariant 4.
- **`split_key` assigned at event creation, never at query time.** Invariant 5. Values: `train` ≤ 2021-12-31, `validate` ≤ 2023-12-31, else `holdout`, from `SplitParams`.
- **No view or query may join statistics on an event's own `split_key`.** Invariant 5b.
- **Every generated row carries `run_id` and `git_sha`.** Invariant 6.
- **No magic numbers outside `core/config.py`.** Invariant 9. The exit path reads `ep.exit_stoch_threshold`, never `80.0`.
- **Determinism (ADR 060):** sort tickers before dispatch; sort the collected frame by `(ticker, signal_date, entry_kind)` before writing; no wall-clock reads inside the engine — `run_id` and timestamps are injected by the caller.
- **Windows spawn:** every module importable with no side effects; entry points guarded by `if __name__ == "__main__":`; workers open their own connections (engines are not picklable — pass a URL string, and use `engine.url.render_as_string(hide_password=False)`, since `str(engine.url)` masks the password).
- **Grain:** one `events` row per `(config_hash, ticker, signal_date, signal_type, entry_kind)`.
- **`db_io.upsert` overwrites every non-key column.** A partial-column write nulls the rest. Write complete rows.
- **Testing:** write the test first for anything touching `core/`. Coverage gate 90% on `core/` only.
- **Never run bare `pytest`.** `pyproject.toml` sets `testpaths = ["capitalscan/tests"]`, which collects the integration suite and runs `TRUNCATE TABLE bars CASCADE` against 4.6M rows of real data. Use `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.

## Known data limitations (do not "fix" these here)

- `days_to_earnings` is unreliable before ~2014 for most tickers; SEC 8-K coverage is thin that far back. `earnings_in_window` inherits that weakness.
- `TOUCH_5M` and `TOUCH_30M` need hourly bars, which exist only from 2024-08-06. Events before that get null for those kinds — expected, per DESIGN §5.4's coverage column. `entry_price_for` returns NaN rather than raising.
- 208 former members have null `sector` and `cik`.
- `crit_rev_growth` is permanently `None`; `UniverseParams.required_criteria` excludes it deliberately.

## File Structure

| File | Responsibility |
|---|---|
| `capitalscan/research/backtest.py` | Pipeline orchestration, per-ticker worker, parallel dispatch, write |
| `capitalscan/research/candidates.py` | Steps 3-6: scan, eligibility, debounce, cluster tagging |
| `capitalscan/research/enrich.py` | Steps 7-11: entry, exit, path metrics, costs, context |
| `capitalscan/research/harness.py` | DESIGN §5.10's five validation checks |
| `capitalscan/jobs/cli.py` | `cscan backtest` command |
| `capitalscan/tests/unit/test_backtest_*.py` | Unit tests, stubbed IO |
| `capitalscan/tests/property/test_backtest_invariants.py` | Property tests |

Split by pipeline stage rather than technical layer: the reduce steps (3-6) change together, the enrich steps (7-11) change together, and the harness is read-only verification over the result.

---

### Task 1: Wire the hourly pull into the nightly chain

Prerequisite for entry resolution. `run_bars_hourly` has exactly one caller — the `bars` CLI command — so nothing keeps the hourly table current, and a stale table makes `entry_price_for` return null rather than raise, silently dropping two of four entry kinds.

**Files:**
- Modify: `capitalscan/jobs/cli.py` — the `nightly()` function
- Test: `capitalscan/tests/unit/test_nightly_chain.py` (create)

**Interfaces:**
- Consumes: `ingest.run_bars_hourly(tickers, start, end, engine=...)`, already exists
- Produces: nothing downstream depends on this task

- [ ] **Step 1: Write the failing test.** Assert the nightly chain calls `run_bars_hourly` with the same `(tickers, start, end)` it passes to `run_bars_daily`. Monkeypatch every `ingest.run_*` and `compute.run_*` to record calls into a list; assert `"bars_hourly"` appears and that its `start`/`end` match the daily call's.

- [ ] **Step 2: Run it.** `uv run pytest capitalscan/tests/unit/test_nightly_chain.py -v`. Expect FAIL — `run_bars_hourly` is never called.

- [ ] **Step 3: Implement.** In `nightly()`, immediately after the `run_bars_daily` line, add `ingest.run_bars_hourly(tickers, start, end, engine=engine)`. `start` is already `end - timedelta(days=5)`, which yields one 60-day window per ticker rather than the 13 a full backfill walks — about 21 minutes for ~630 tickers at `RATE_LIMIT_PER_SEC = 0.5`.

- [ ] **Step 4: Verify.** Test passes. Run `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.

- [ ] **Step 5: Commit.** `git commit -m "Wire hourly bars into the nightly chain (BUILD 9.0)"`

---

### Task 2: Config hashing and the backtest contract

**Files:**
- Create: `capitalscan/research/backtest.py`
- Test: `capitalscan/tests/unit/test_backtest_config.py`

**Interfaces:**
- Consumes: `core.config` dataclasses (`IndicatorParams`, `SignalParams`, `ExitParams`, `CostParams`, `SplitParams`, `UniverseParams`)
- Produces:
  - `@dataclass(frozen=True) BacktestConfig` with fields `indicators: IndicatorParams`, `signals: SignalParams`, `exits: ExitParams`, `costs: CostParams`, `splits: SplitParams`, `universe: UniverseParams`
  - `config_hash(config: BacktestConfig) -> str` — 16-char hex, deterministic
  - `split_key_for(signal_date: date, sp: SplitParams) -> str` — returns `"train" | "validate" | "holdout"`

- [ ] **Step 1: Write failing tests.** Four behaviours: identical configs hash identically across processes (serialize via `dataclasses.asdict` and hash the sorted JSON, so dict ordering cannot leak in); changing any single field changes the hash; `split_key_for` returns `train` for 2021-12-31, `validate` for 2022-01-01 and 2023-12-31, `holdout` for 2024-01-01; a date before `SplitParams.event_start` raises rather than silently landing in `train`.

- [ ] **Step 2: Run.** Expect FAIL, `ModuleNotFoundError: capitalscan.research.backtest`.

- [ ] **Step 3: Implement.** `config_hash` uses `hashlib.sha256(json.dumps(asdict(config), sort_keys=True, default=str).encode()).hexdigest()[:16]`. `split_key_for` compares against `sp.train_end` / `sp.validate_end` as `date` objects, raising `ValueError` below `sp.event_start`.

- [ ] **Step 4: Verify.** Tests pass, plus full unit + property suite.

- [ ] **Step 5: Commit.**

---

### Task 3: Candidate scan, eligibility, debounce

DESIGN §5.2 steps 3-5. Getting 5 wrong silently changes the event count, which is the Phase 3 gate's headline number.

**Files:**
- Create: `capitalscan/research/candidates.py`
- Test: `capitalscan/tests/unit/test_backtest_candidates.py`

**Interfaces:**
- Consumes: `core.signals.detect(bar, ind_row, sp) -> list[SignalHit]` — **never widen this signature**; it may read only `low`, `high`, `ts`, `ticker` from the bar and takes one indicator row, never a frame
- Produces:
  - `scan_candidates(bars: pd.DataFrame, indicators: pd.DataFrame, sp: SignalParams) -> pd.DataFrame` with columns `ticker, signal_date, signal_type, signal_types_all, signal_strength, side, touch_level`
  - `apply_eligibility(candidates, universe_flags, sp_splits) -> tuple[pd.DataFrame, list[dict]]` — returns kept rows and reject records
  - `debounce(candidates: pd.DataFrame) -> pd.DataFrame` — one row per `(ticker, bound, signal_date)`

- [ ] **Step 1: Write failing tests.** Critical cases: `scan_candidates` passes the indicator row from **t−1** with the bar from **t** (construct a frame where using t's own indicators would produce a different signal, and assert the t−1 answer); a null in any indicator the signal needs drops the row and produces a reject record with a reason, never a filled value (invariant 4); `debounce` collapses two lower-bound touches on one ticker one day apart into one row but keeps a lower and an upper touch on the same day as two.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Iterate bars positionally; for row `i`, pass `bars.iloc[i]` and `indicators.iloc[i-1]`. Skip `i = 0` entirely — there is no t−1. Eligibility drops rows outside `[event_start, today]`, rows whose ticker is not in-trade for that date, and rows with nulls in the fields `detect` requires.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 4: Cluster tagging

DESIGN §5.3. Overlapping events are **tagged, not suppressed** (ADR 056).

**Files:**
- Modify: `capitalscan/research/candidates.py`
- Test: `capitalscan/tests/unit/test_backtest_clusters.py`

**Interfaces:**
- Produces: `tag_clusters(candidates: pd.DataFrame, max_hold_days: int) -> pd.DataFrame`, adding `cluster_id: int`, `seq_in_cluster: int`, `is_cluster_head: bool`, `days_since_head: int`

- [ ] **Step 1: Write failing tests.** Two events on one ticker within `max_hold_days` share a `cluster_id`, with `seq_in_cluster` 1 and 2 and `is_cluster_head` true only for seq 1. Events further apart than `max_hold_days` get distinct clusters, both heads. Two tickers never share a cluster even on identical dates. `days_since_head` counts **trading** bars from the head, not calendar days.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Group by ticker, sort by `signal_date`, walk forward opening a new cluster whenever the gap from the current head exceeds `max_hold_days` bars.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 5: Entry resolution, four kinds

DESIGN §5.4. Entry does not depend on exit parameters, which is what lets the sweep compute entries once and reuse them across 18 exit configs.

**Files:**
- Create: `capitalscan/research/enrich.py`
- Test: `capitalscan/tests/unit/test_backtest_entry.py`

**Interfaces:**
- Consumes: `core.returns.entry_price_for(kind, bar, next_bar, touch_level, side, hourly) -> float`; `core.costs.slippage(price, cp) -> float`
- Produces: `resolve_entries(candidate: pd.Series, bars: pd.DataFrame, hourly: pd.DataFrame | None, cp: CostParams) -> list[dict]` — one dict per `EntryKind`, each with `entry_kind, entry_date, entry_price, entry_gapped`

- [ ] **Step 1: Write failing tests.** The gap rule for `TOUCH` long: a bar opening **below** the band fills at `open`, not the band, with `entry_gapped = True`; a bar opening above fills at the band with `entry_gapped = False`. Mirrored for short. `NEXT_OPEN` on a terminal bar (no next session) yields null, not the current close. `TOUCH_5M` / `TOUCH_30M` yield NaN when `hourly is None`, and the row is still produced rather than dropped. Slippage applies **on top** of the resolved price, in the adverse direction for the side.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Delegate every price decision to `entry_price_for`. This function's only jobs are supplying the right `bar` / `next_bar` / `hourly` slice, applying slippage, and shaping the dicts.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 6: Exit resolution

DESIGN §5.5. Calls `core.exits.resolve_exit`. **No second implementation** (BUILD 9.3).

**Files:**
- Modify: `capitalscan/research/enrich.py`
- Test: `capitalscan/tests/unit/test_backtest_exit.py`

**Interfaces:**
- Consumes: `core.exits.resolve_exit(entry_price, entry_idx, side, fwd_bars, fwd_ind, atr_at_entry, ep, ind_at_entry=None) -> ExitResult`
- Produces: `resolve_exit_for_entry(entry: dict, entry_idx: int, side: Side, bars: pd.DataFrame, indicators: pd.DataFrame, ep: ExitParams) -> dict` with `exit_date, exit_price, exit_reason, holding_days, ambiguous`

- [ ] **Step 1: Write failing tests.** `ind_at_entry` is **always** passed — omitting it makes `resolve_exit` skip band exits on the first forward bar, and the test must prove the first bar can trigger a band exit. `fwd_bars` and `fwd_ind` share an index and cover `entry_idx+1 .. entry_idx+max_hold_days`. `holding_days == exit_idx + 1`. An entry whose forward window is truncated by end-of-data still resolves rather than raising.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Slice the frames, call `resolve_exit`, map `ExitResult` onto the dict. Read every threshold from `ep`; a literal `80.0` anywhere in this file is a review rejection.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 7: Path metrics

DESIGN §5.6. MFE and MAE over `[t+1, exit_idx]`; reachability over the **full** `[t+1, t+5]` regardless of exit timing.

**Files:**
- Modify: `capitalscan/research/enrich.py`
- Test: `capitalscan/tests/unit/test_backtest_path.py`

**Interfaces:**
- Consumes: `core.returns.mfe_mae(entry_price, side, fwd_bars) -> tuple[float, float, int]`; `core.returns.forward_returns(close, horizons) -> pd.DataFrame`
- Produces: `path_metrics(entry_price, side, fwd_bars, exit_idx, targets) -> dict` with `mfe, mae, time_to_mfe, capture_ratio, touched_*pct, day_touched_*pct, fwd_ret_*d`

- [ ] **Step 1: Write failing tests.** The sharp one: **MFE is not clamped at zero** — a position that never traded above entry has negative MFE, and DESIGN §5.6 depends on it (ADR 089). Reachability uses the full 5-bar window even when the exit fired on bar 2: construct a case where the exit is on bar 2 and the +5% touch happens on bar 4, and assert `touched_5pct` is True with `day_touched_5pct == 4`. `capture_ratio` is null when `MFE <= 0`, never a division by a negative.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Two different windows: `mfe_mae` gets `fwd_bars.iloc[:exit_idx+1]`, reachability gets all of `fwd_bars`. Targets come from `StatsParams.reach_targets`, not literals.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 8: Costs, context tagging, split assignment

DESIGN §5.2 steps 10-12.

**Files:**
- Modify: `capitalscan/research/enrich.py`
- Test: `capitalscan/tests/unit/test_backtest_context.py`

**Interfaces:**
- Consumes: `core.costs.apply_costs(gross_ret, side, holding_days, cp, entry_price=None) -> float`; `core.returns.realized_return(entry_price, exit_price, side) -> float`; `split_key_for` from Task 2
- Produces: `enrich_context(event: dict, ind_row: pd.Series, market_row: pd.Series | None, sp: StatsParams, splits: SplitParams) -> dict` adding `gross_ret, net_ret, dd_bucket, bw_regime, era, earnings_in_window, split_key, vix_close, spx_ret_1d`

- [ ] **Step 1: Write failing tests.** Costs always **subtract**, so a losing trade gets worse, not better, on both sides. `dd_bucket` boundaries come from `StatsParams.dd_buckets`, `era` from `StatsParams.era_bounds` — no literals. `split_key` is assigned here at creation and matches Task 2's function exactly. `earnings_in_window` is null, not False, when `days_to_earnings` is null — an unknown is not a negative.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 9: Per-ticker worker, parallel dispatch, cofire post-pass, write

DESIGN §5.2 step 13 and §5.8.

**Files:**
- Modify: `capitalscan/research/backtest.py`
- Test: `capitalscan/tests/unit/test_backtest_determinism.py`, `capitalscan/tests/unit/test_backtest_worker.py`

**Interfaces:**
- Produces:
  - `_backtest_one_ticker(ticker, config, run_id, database_url) -> pd.DataFrame` — module-level, importable with no side effects, opens its own connection
  - `run_backtest(tickers, config, run_id, engine=None, max_workers=1) -> BacktestReport`
  - `add_cofire_count(events: pd.DataFrame) -> pd.DataFrame` — groups across tickers by `(signal_date, signal_type)`

- [ ] **Step 1: Write failing tests.** Determinism is the gate criterion: two runs with identical config produce byte-identical output ignoring `run_id`. Tickers are sorted before dispatch and the collected frame sorted by `(ticker, signal_date, entry_kind)` before writing, because `as_completed` returns nondeterministically. No wall-clock read inside the engine — `run_id` is injected. `add_cofire_count` groups **across** tickers, so it cannot run inside a per-ticker worker; assert two tickers firing the same type on the same date each get `cofire_count == 2`.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Mirror `compute.run_indicators`'s spawn-safe pattern: pass `database_url` as a string, each worker builds its own engine. Write with `db_io.upsert(engine, "events", rows, ["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"])` — complete rows only, since the upsert overwrites every non-key column.

- [ ] **Step 4: Verify.** Include `uv run pytest capitalscan/tests/unit/test_spawn_guard.py` — spawn re-imports every module, and a missing guard causes recursive process creation that looks like a hang.

- [ ] **Step 5: Commit.**

---

### Task 10: Validation harness

DESIGN §5.10's five checks. This is the Phase 3 gate.

**Files:**
- Create: `capitalscan/research/harness.py`
- Test: `capitalscan/tests/unit/test_backtest_harness.py`

**Interfaces:**
- Produces: `run_harness(events: pd.DataFrame, bars_by_ticker: dict[str, pd.DataFrame], config: BacktestConfig) -> HarnessReport` with a bool per check and per-violation detail

- [ ] **Step 1: Write failing tests, one per check.** No look-ahead: shift all indicators forward one bar, rerun, and assert the event set changes **materially** — if it barely changes, either the signal is not reading indicators or it is already using future data. Entry sanity: every `entry_price` within its bar's `[low, high]`, 100%. Exit sanity: same for `exit_price`. Return identity: `gross_ret` recomputed from prices matches to 1e-9. Non-overlap: no two cluster-head events on one ticker have overlapping windows.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Each check returns violations rather than raising, so one failure does not hide the other four.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 11: CLI wiring and the default-config run

ADR 059's ordering rule: default config first, full harness, then ~20 events inspected by hand **before** any sweep. Sweeping over a buggy engine produces 18 confidently wrong answers.

**Files:**
- Modify: `capitalscan/jobs/cli.py`
- Test: `capitalscan/tests/unit/test_backtest_cli.py`

**Interfaces:**
- Produces: `cscan backtest [--tickers] [--workers N] [--sweep] [--config-name NAME]`

- [ ] **Step 1: Write failing test.** `--sweep` without a prior clean default-config run in `runs` refuses to start, citing ADR 059. `--workers` defaults to 1 and is passed through. Ticker resolution reuses `_resolve_tickers`, so `--tickers` bypasses `is_active` the same way every other command does.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Default config is ATR stop k=1.5, target 4%, `NEXT_OPEN` — read from `ExitParams` defaults, not restated as literals.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

### Task 12: Sweep

DESIGN §5.9. 18 exit configs: `stop_mode` (atr, fixed, none) × `stop_atr_k` (1.0, 1.5, 2.0, 2.5) collapsing to 4+1+1 = 6 stop variants, × `target_pct` (0.03, 0.04, 0.05) = 18. Entry prices computed once and reused.

**Files:**
- Modify: `capitalscan/research/backtest.py`
- Test: `capitalscan/tests/unit/test_backtest_sweep.py`

**Interfaces:**
- Produces: `sweep_configs(base: BacktestConfig) -> list[BacktestConfig]` — exactly 18, each with a distinct `config_hash`

- [ ] **Step 1: Write failing tests.** Exactly 18 configs, all hashes distinct. Stochastic thresholds are **not** in the grid — they are held at defaults across the sweep. Entry resolution runs once per event, not 18 times: assert via a call counter that entry work does not scale with config count.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** One candidate pass plus 18 exit passes, roughly 4-5 minutes total.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

## Phase 3 Gate (BUILD.md §9 acceptance)

- Exit invariants hold across 10,000 property-generated cases: `uv run pytest capitalscan/tests/property -m exit_invariant --hypothesis-profile=full`
- Ambiguity rate below 10%, or hourly escalation implemented
- Event count within 20% of the analytical estimate (~4% of ticker-days for confluence)
- Two runs with identical config produce identical output ignoring `run_id`
- All five validation-harness checks pass

**Note on the event-count criterion:** the pre-fix database produced confluence events on ~19% of ticker-days, roughly 4.8x the estimate. That measurement predates the impostor purge, the window trim, and the universe fixes, so it must be re-measured before anyone concludes the engine is wrong. If it remains ~5x after a clean recompute, investigate before sweeping — a miscounted event set makes every downstream statistic wrong in the same direction.

## Self-review notes

- Every DESIGN §5.2 step maps to a task: 1-2 → Task 3, 3-5 → Task 3, 6 → Task 4, 7 → Task 5, 8 → Task 6, 9 → Task 7, 10-12 → Task 8, 13 → Task 9.
- BUILD.md §9 tasks map: 9.0 → Task 1, 9.1 → Tasks 2/9, 9.2 → Task 5, 9.3 → Task 6, 9.4 → Task 7, 9.5 → Task 8, 9.6 → Task 9, 9.7 → Task 9, 9.8 → Task 10, 9.9 → Task 11, 9.10 → Task 12.
- Names are consistent across tasks: `config_hash`, `split_key_for`, `scan_candidates`, `tag_clusters`, `resolve_entries`, `resolve_exit_for_entry`, `path_metrics`, `enrich_context`, `add_cofire_count`, `run_backtest`, `run_harness`, `sweep_configs`.
