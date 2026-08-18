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

