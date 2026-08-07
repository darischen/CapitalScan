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

