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

