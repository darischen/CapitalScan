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
