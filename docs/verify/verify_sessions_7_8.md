
# Verification: Session 7 (Full backfill) and Session 8 (Poller & notifications)

Audit performed 2026-08-01, read-only, while the hourly bars backfill was still running. No writes, no `cscan` commands, no integration tests were executed. Only `SELECT` queries and `uv run pytest capitalscan/tests/{unit,property}` were run.

---

## SESSION 7 — Full backfill

**Verdict: PARTIAL**

Raw ingestion (bars, tickers, corporate actions, market days, indicators) is in good shape and validation is clean, but the downstream compute chain (`universe`, `events`) has not been run against the full universe, `docs/RESULTS.md` was never updated past the original 51-ticker test entry, and no data backup (task 7.4) exists yet. The Session 7 acceptance bullet that depends on `scan()` returning results currently cannot pass, because its two upstream tables are empty.

### Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 750 tickers with `first_bar`/`last_bar` populated | PASS (with a note) | `tickers`: 707 rows, 633 active, all 633 have `first_bar`/`last_bar` populated (ground truth supplied). 633 is short of the "750" figure in BUILD.md/ADR 055, but ADR 055 itself says "roughly 600 to 750," so 633 is inside the stated range, not a defect. |
| 2 | `cscan validate --report` clean at `reject` severity | PASS | Stated as run and clean earlier this session (ground truth). Not re-run here (forbidden — `validate` invokes `cscan`). |
| 3 | Indicators computed for full range, no post-2010 nulls | PARTIAL | Read-only query: `SELECT count(*) FROM indicators WHERE ts >= '2010-01-01' AND (bb_lower IS NULL OR bb_upper IS NULL OR k_full IS NULL)` → **4,899 nulls** out of 2,400,803 post-2010 rows (0.20%). Breakdown by ticker shows two populations: (a) ~19-row clusters immediately after a listing/spin-off date (`AMTM`, `CARR`, `CEG`, `APP`, `CDW`, …) — this matches indicator warmup and is exactly the case ADR 040 / Session 6's warmup test excludes ("continuous coverage" tickers only); (b) a handful of tickers with much larger null spans that don't look like plain warmup: `CPWR` (1,107 rows, 2010-01 through 2025-12), `AMCR` (531), `SW` (374), `BMC` (363), `RSH` (268). These look like ticker-symbol reuse or merger/delisting artifacts producing a discontinuous `bars` series that indicators never backfilled cleanly. Not necessarily a bug (ADR 040's "continuous coverage" carve-out likely covers them), but BUILD §7's acceptance line as literally written ("no post-2010 nulls") is not met, and nobody has recorded which case each ticker falls into. |
| 4 | `cscan scan --universe trade --date <recent>` returns plausible results | **FAIL as of now** | `universe` table is **empty** (0 rows) and `events` table is **empty** (0 rows). `capitalscan/jobs/compute.py:522-533` (`_in_trade`) and `capitalscan/jobs/compute.py:643` (`run_events`) gate every event on `universe.in_trade`; with `universe` empty, `run_events` would tag nothing as tradeable and `events` stays empty, and `scan()` (`capitalscan/jobs/compute.py:692`) reads from `events`. The `universe` job in turn depends on `shares_outstanding` and `earnings` (both empty, 0 rows), so it cannot produce non-null ADR 014 criteria yet either. **The compute chain past `indicators` has not been run for the full backfill.** |

### Other Session 7 gaps

- **7.3 "Record the result" not done for the real run.** `docs/RESULTS.md` (lines 65-78) still describes the original 51-ticker test as "in progress" and explicitly defers the real record to a future 750-ticker run that "requires Wikipedia scraper fix." The scraper fix already shipped (`capitalscan/jobs/fetch/wikipedia.py` is implemented and `capitalscan/tests/unit/test_fetch_wikipedia.py` passes, 8/8), and the actual run now has 633 active tickers and ~3.9M bar rows, but none of that is reflected in `RESULTS.md`. `docs/SESSION_7_STATUS.md` is similarly stale — it documents the 51-ticker dry run and lists "Next Steps for Production" as still-open items that appear to have since been completed in code.
- **7.4 "First `pg_dump`" appears unimplemented.** `capitalscan/jobs/db.py:110-128` only wraps a **schema-only** `pg_dump` (`cscan db schema`, DDL to `db/schema.sql`). There is no job or script that dumps the full research database nightly to a second local disk or monthly to a GitHub Release asset, as ADR 083 / task 7.4 require. `grep -i "pg_dump|backup"` across `scripts/` and `capitalscan/` turns up nothing beyond the schema-only path.
- `data/universe_union.csv` exists and has 756 lines (ADR 055 compliance for the frozen CSV) — this part of Session 5/7 looks done.

**What would block starting Session 9 from Session 7's side:** `universe` and `events` need to be populated (run the `universe` and `events` jobs, which in turn need `shares_outstanding`/`earnings` populated) before `scan()` produces anything, and `RESULTS.md` needs an honest entry for the actual run before Session 9's backtest results can be compared against anything. The backup task (7.4) is a lower-severity gap but is explicitly called out in BUILD.md.

---

## SESSION 8 — Poller and notifications

**Verdict: PASS (code-complete; gate is empirically untestable until Monday 2026-08-03, as expected)**

All five files exist, are wired into the CLI/Task Scheduler, and match their governing ADRs. Unit tests for every module pass (391/391 overall, including all `test_call_overlay.py`, `test_notify.py` cases). An integration test suite (`capitalscan/tests/integration/test_poll.py`, `test_positions.py`) exists and — by inspection only, not execution — exercises exactly the Phase 2 gate's four claims, but per the safety constraints it was not run.

### Acceptance criteria (empirical gate — cannot fire until a live session; judged here on code readiness)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Poller detects a live breach within one polling interval | READY | `capitalscan/jobs/poll.py:253-368` (`_process_tick`) quotes every ticker once per tick and calls `core_signals.breach_live` (the same function contract used by `detect`, ADR 006) — one comparison per ticker per tick, matching BUILD §8.1. Integration test `test_breach_is_recorded_as_a_holdout_event_within_one_tick` (`capitalscan/tests/integration/test_poll.py:116-137`) targets this exactly (read, not run). |
| 2 | Notification delivered on all three configured channels | READY | `capitalscan/jobs/notify.py` implements `SmtpNotifier`, `DiscordNotifier`, `NtfyNotifier` behind one `Notifier` protocol (lines 21-106), each independently gated on its own env vars with no shared on/off flag (ADR 054). `notify_all` (lines 122-134) sends to every configured channel independently and never lets one failure block another — unit-tested in `test_notify.py::test_notify_all_skips_a_failing_channel_without_raising` and `::test_multiple_channels_active_simultaneously` (passed). |
| 3 | `poller_sessions` records the session with coverage percentage | READY | `capitalscan/jobs/poll.py:448-464` computes `coverage_pct = 100 * ticks_completed / ticks_expected` and upserts one row per `session_date` on every `run_poll` exit (including the early-exit paths). `poller_sessions` is empty right now only because no live session has occurred yet (ground truth) — expected, not a defect. |
| 4 | Restart mid-session does not re-fire an already-sent event | READY | Debounce is keyed entirely in Postgres: `_already_fired` (`capitalscan/jobs/poll.py:146-157`) checks for an existing `events` row on `(config_hash, ticker, signal_date, signal_type, entry_kind='touch')`, the same tuple that is the table's UNIQUE constraint, so a fresh `run_poll()` call (simulating a process restart) sees exactly what already fired — no process-memory state anywhere in the module. Integration test `test_restart_mid_session_does_not_refire_an_already_sent_event` (`capitalscan/tests/integration/test_poll.py:168-202`) targets this directly (read, not run). |

### ADR-by-ADR check

| ADR | Requirement | Status | Evidence |
|---|---|---|---|
| 054 | Notifier protocol, three independently toggleable channels | PASS | `capitalscan/jobs/notify.py:21-119`. Each `from_env` classmethod returns `None` unless its own vars are present; `active_notifiers()` (108-119) includes only the ones that resolve. No shared enable flag. |
| 048 | `order_intents`: structured intent, idempotency key, **no broker client/credentials/order-placement path** | PASS — see CRITICAL check below | `capitalscan/jobs/positions.py:121-166`. |
| 073 | `positions` (user-declared, closed with realized returns); exit signals pushed | PARTIAL | `positions.py:23-118` fully implements open/close/list with correct sign-flipped realized return for shorts (unit-tested in `test_positions.py`, read-only — not run). **However, no code path pushes an exit-signal notification for an open position.** `grep -rn "exit_signal|resolve_exit|check_exits"` across `capitalscan/jobs` returns nothing — the poller only ever emits *entry*-side breach notifications (`poll.py:342-347`); there is no job that checks open `positions` rows against `core.exits.resolve_exit` and notifies on an exit trigger. ADR 073 explicitly requires "Exit signals push through the configured notification channels rather than waiting for the user to check" — this half of the ADR does not appear to be built yet. |
| 050 | Live call overlay priced from live quotes, explicitly not backtested | PASS | `capitalscan/jobs/call_overlay.py:91-98`: every non-`None` result carries `"priced_from": "live_quotes"` and `"backtested": False`. Long-only (ADR 017 compliance noted in the module docstring). Returns `None` rather than a fabricated row when no chain is listed/liquid (`build_overlay`, lines 45-98) — unit-tested, 6/6 passing, including the "zero-ask strikes excluded" and "no expiration far enough out" cases. |
| 084 | `poller_sessions` coverage logging | PASS | `poll.py:448-464`, described under gate criterion 3 above. |
| 080 | Task Scheduler catch-up, `scheduled_runs` records delay | PASS | All four `scripts/tasks/*.xml` (`nightly`, `poller`, `weekly`, `monthly`) have `<StartWhenAvailable>true</StartWhenAvailable>` (confirmed by grep, 4/4 files, 1 match each). `capitalscan/jobs/scheduled_runs.py:47-72` computes `delay_seconds = actual_start - scheduled_for` against the fixed `SCHEDULE` table (lines 20-25) and upserts to `scheduled_runs`, called from `poll.py:398` and referenced from `cli.py`'s `nightly()`. |

### CRITICAL — Invariant 7 (no broker path) check

**Invariant 7 holds. No violation found.**

- `capitalscan/jobs/positions.py:121-166` (`emit_order_intent`) builds a plain dict (`side`, `quantity_basis`, `limit_level`, `stop_level`, `time_in_force`, `idempotency_key`) and writes it to `order_intents` via `db_io.upsert`. Nothing downstream of that write exists — it is never read by another module to act on.
- Repo-wide search for broker-shaped code (`broker`, `alpaca`, `ib_insync`, `place_order`, `submit_order`, `ExecutionClient`, `BrokerClient`, case-insensitive) matched only: `docs/BUILD.md`, `docs/DECISIONS.md`, `CLAUDE.md` (all documentation describing the *absence*), `docs/DESIGN.md`, and `capitalscan/jobs/positions.py` / `capitalscan/tests/integration/test_positions.py` (the module's own docstring and its dedicated negative test, `test_order_intent_never_touches_anything_broker_shaped`, `capitalscan/tests/integration/test_positions.py:94-110`). No credentials variable, HTTP client, or SDK import for any brokerage exists anywhere in the codebase or `.env.example`.
- `.env.example` (read earlier) contains no brokerage credential variables — only `NOTIFY_*`, `SEC_USER_AGENT`, `FINNHUB_API_KEY`, `ANTHROPIC_API_KEY`, DB URLs.

### CLAUDE.md chat/tools rule check

Not yet applicable in Session 8's scope: no code path in `poll.py`/`notify.py`/`call_overlay.py` states a probability or hit rate. Notification bodies (`poll.py:343-346`, e.g. `"{ticker} fired {signal_type} at {price:.2f} (touch level ..., k_full ...)"`) state only what fired and the raw indicator values — no probability claim, so the `n_eff`/CI requirement doesn't trigger yet, and none of the "financial situation / tax / suitability" carve-out is at risk since nothing in this session's code frames a recommendation as personalized advice. This is consistent with the ADR 075 sourcing rule but the rule itself lives in the Phase 5 chat/tools layer, not yet built.

### Test results (allowed paths only)

```
uv run pytest capitalscan/tests/unit -q       → 391 passed
uv run pytest capitalscan/tests/property -q   → 20 passed
```

Both suites are 100% green. Notably `tests/property/test_exit_invariants.py` (5/5) and `tests/property/test_lookahead.py` (6/6) and `tests/property/test_signal_parity.py` (4/4) all pass — the three of CLAUDE.md's "five tests that carry the correctness load" that live outside `tests/golden`/integration.

`capitalscan/tests/integration/test_poll.py` and `test_positions.py` were read but **not executed** (forbidden per the safety constraints — they truncate tables). By inspection, their structure and assertions map directly onto the four Phase 2 gate criteria and the invariant-7 negative-space test.

---

## Summary table

| Session | Verdict |
|---|---|
| 7 — Full backfill | PARTIAL |
| 8 — Poller & notifications | PASS (code-complete; empirical gate pending Monday 2026-08-03) |

## Gaps that would block Session 9

1. `universe` and `events` tables are empty — the `universe` and `events` compute jobs have not been run against the full 633-ticker backfill (blocked in turn by empty `shares_outstanding`/`earnings`). `scan()` returns nothing until this runs.
2. `docs/RESULTS.md` Session 7 entry is stale (describes the abandoned 51-ticker dry run, not the actual 633-ticker/3.9M-row run).
3. No full-database backup job exists (task 7.4 / ADR 083) — only schema-only `pg_dump` via `cscan db schema`.
4. ADR 073's exit-signal push (notifying on an open position's exit trigger, as opposed to the entry-side breach notification the poller already sends) does not appear to be implemented anywhere in `jobs/`.
5. A handful of tickers (`CPWR`, `AMCR`, `SW`, `BMC`, `RSH`) carry large null spans in indicators post-2010 that look larger than warmup — worth a one-time triage to confirm they're the "discontinuous coverage" case ADR 040 exempts, rather than a real ingestion gap, before trusting `scan()` output for those names.
