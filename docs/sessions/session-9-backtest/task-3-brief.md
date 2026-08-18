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

