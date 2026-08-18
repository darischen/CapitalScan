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

