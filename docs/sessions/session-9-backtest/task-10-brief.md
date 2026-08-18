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

