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

