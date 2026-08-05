# Session 9 standing orders

Authored 2026-08-01. These are the user's instructions, recorded on disk so they
survive context summarization. **Read this file first on any wake-up.**

Branch: `session-9-backtest`, forked from `9777377` on `main`.

## Trigger

Do **nothing** until the user says approximately "start wake up loop". Until then,
no polling, no ingest commands, no implementers.

## Once triggered: poll for hourly completion

Poll roughly every 5 minutes (user's stated preference; see Open Questions).

Completion test — check **both**, because a killed process leaves `status='running'`
forever:

```sql
SELECT status, finished_at FROM runs WHERE job='bars_hourly' ORDER BY started_at DESC LIMIT 1;
SELECT count(DISTINCT ticker) FROM bars WHERE interval='1h';
```

- `status='ok'` → done, proceed.
- `status='running'` and the ticker count has **not moved** across two consecutive
  polls → the job died. Stop and report; do not proceed.

## Step 1 — populate the remaining tables

These four are empty and every one gates Phase 1:

| Table | Command | Note |
|---|---|---|
| `earnings` | `cscan earnings --historical` | ~80s. `sec.py` rate limit is 8/s, one request per ticker. Cached per CIK, so retries are free. Run it **first**. |
| `shares_outstanding` | `cscan shares --since-last` | SEC XBRL |
| `universe` | `cscan universe --quarter 2026Q3` | reads `shares_outstanding`, so run it after |
| `events` | `cscan events --lookback 6500` | reads `universe.in_trade` and `indicators.days_to_earnings` |

Dependency chain, which is why `earnings` must come first:
`earnings` → `run_indicators` writes `days_to_earnings` (`compute.py:211`) →
`run_events` copies it to each event row (`compute.py:576`). Running events before
earnings means regenerating 2.5M indicator rows and the whole events table afterward.

**Post-run check on earnings — a real defect is suspected.** `sec.fetch_submissions`
reads only `filings.recent`, which SEC caps near the most recent 1,000 filings. Older
filings live in `filings.files[]` shards the code never fetches, so heavy filers
(most large caps) may have no 8-K dates reaching back to 2010, defeating ADR 036's
stated purpose. After the run:

```sql
SELECT count(*) FILTER (WHERE lo > DATE '2011-01-01') AS short_of_2010, count(*)
FROM (SELECT ticker, min(report_date) lo FROM earnings GROUP BY ticker) s;
```

Report the number. Do not attempt to fix the pagination during the autonomous run —
it is an ADR 036 correctness question for the user.

Plus the catch-up and the gate:

```
cscan indicators --workers 8 --lookback 10    # indicators sit one day behind bars
cscan validate --report
cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30    # Phase 1 gate
```

Order matters: `shares` → `universe` → `events`.

**`days_to_earnings` is null on all 2.4M post-2010 indicator rows** because `earnings`
is empty. Phase 1 requires zero post-2010 nulls, so `earnings` must land *before*
the indicators catch-up, and the catch-up needs a wide enough lookback to rewrite
history, not just 10 days. Reassess the lookback once earnings is populated.

**Failure policy:** if any command errors or returns obviously wrong counts, stop and
report. Do not continue down the chain on a broken upstream.

## Step 2 — re-verify sessions 0-8

Re-run the subagent verification, now against populated tables. Prior run's reports:

- `reports/verify_sessions_0_3.md`
- `reports/verify_sessions_4_6.md`
- `reports/verify_sessions_7_8.md`

Known open findings from the first pass, to confirm fixed or still-open:

1. `run_validate` never queries `trading_days` — the missing-bar-per-trading-day check
   from DESIGN §2.3 is documented at `ingest.py:341` but not implemented. "Clean"
   validation does not currently mean "no gaps".
2. `scan()` joins indicators on `e.signal_date = i.ts` (`compute.py:734`), reading
   bands at t while the event fired off t−1. Invariant 3 violated at query time.
   Display only; `events` rows themselves are correct.
3. `run_universe` revenue-growth criterion is a permanent `None` stub
   (`compute.py:299-311`). ADR 014 criterion never evaluates.
4. `db_io.upsert` overwrites every non-key column, so a `run_events` rerun would null
   out any entry/exit columns Session 9 writes. Design constraint for 9.1.
5. ADR 073's exit-signal push is not implemented; only entry-side notifications exist.
6. `docs/RESULTS.md` still describes the abandoned 51-ticker dry run.
7. Reused tickers confirmed live in the DB: `FB`, `PCLN`, `PCS`, `Q` are impostors
   caught only by the 280-bar minimum. The 568 originally-loaded tickers were never
   identity-audited; only the 187-ticker gap was.

## Step 3 — Session 9 via subagent-driven development

Plan file: `docs/superpowers/plans/session-9-backtest.md` (write it if absent, from
BUILD.md §9 and DESIGN §5.2 / §5.4 / §5.10).

User pre-approved the plan **on the condition that it matches the existing docs.**
If the plan needs to depart from BUILD.md or DESIGN.md anywhere, stop and ask.

Follow `superpowers:subagent-driven-development`: fresh implementer per task, task
review after each, ledger at the SDD workspace, broad final review at the end.
Never fix findings in the controller session.

## Hard safety rules while any ingest runs

- **Never** bare `uv run pytest`. `pyproject.toml` sets `testpaths = ["capitalscan/tests"]`,
  which collects the integration suite.
- **Never** `capitalscan/tests/integration/`. `test_ingest.py` and `test_compute.py`
  truncate `bars`; `test_poll.py` truncates `tickers`, which CASCADEs to `bars`.
- Safe: `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.
- No `cscan db migrate` while a write job is live — DDL takes ACCESS EXCLUSIVE.
- No `uv sync` / `uv add` while a job is live — Windows locks `.venv` files.
- Every subagent prompt must carry these rules verbatim.

## Open questions for the user

1. **Poll interval.** 5 minutes over the remaining ~1.5-2 hours is ~20 wake-ups, each
   costing context. 15-20 minutes covers it with a fraction of the overhead. Using 5
   unless told otherwise.
2. **Phase 2 gate** is Monday 2026-08-03, a live market session. Independent of
   Session 9; nothing in the backtest reads poller output.
