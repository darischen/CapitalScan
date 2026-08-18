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

