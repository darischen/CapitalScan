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

