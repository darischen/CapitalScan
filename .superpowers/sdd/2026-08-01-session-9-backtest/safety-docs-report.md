# Safety docs report — 2026-08-02

Task: move CapitalScan's operational safety rules into `CLAUDE.md` so a fresh
session reads them before running anything. Docs only, no code touched.

## What I added, and where

**`CLAUDE.md`** — new section "Before running anything", placed right after
the ADR table and before "Non-negotiable invariants" (the first thing a fresh
agent reads after the file's opening warning, and before any invariant that
might tempt a command). Covers, in order:

1. The `pytest`/`testpaths` landmine: mechanism (`testpaths = ["capitalscan/tests"]`
   in `pyproject.toml` collects `integration/` on a bare invocation), what it
   destroys (`TRUNCATE ... CASCADE` on `bars`, 4.5M+ rows, and on `tickers`
   via CASCADE), and the one safe invocation
   (`uv run pytest capitalscan/tests/unit capitalscan/tests/property`).
2. `docker` not on PATH, with the working `psql` invocation and the
   `max_parallel_workers_per_gather=0` prefix for shared-memory errors.
3. No `cscan db migrate` / `uv sync` / `uv add` while a job is running, with
   the one-line reason each (ACCESS EXCLUSIVE lock vs. Windows file locks).
4. Measured costs for the three long jobs named in the task
   (`backtest --workers 8`, `bars --hourly --backfill`, `universe --quarter`).
5. "Verify before you assert" — one line, pointing at direct measurement
   over prior reports.

**`docs/TESTS.md` §2** — one line after the tier list and tooling line:
a warning that a bare `pytest` collects `integration/` against the real
database, cross-referencing `CLAUDE.md` § Before running anything rather
than restating the mechanism.

## Verification against the current repo

- `pyproject.toml:56` — confirmed `testpaths = ["capitalscan/tests"]` still
  present at HEAD `528ca90`.
- `capitalscan/tests/integration/test_ingest.py`, `test_compute.py`,
  `test_poll.py` — confirmed all three still contain `TRUNCATE TABLE ...
  CASCADE` calls, and confirmed `test_poll.py`'s truncate list includes
  `tickers` (CASCADEs to `bars`).
- `docker` on PATH — ran `which docker` in the agent's bash shell: not found.
  Confirms the HANDOFF.md claim still holds in this environment.
- `psql` invocation — ran it live (`select 1;`), got a clean result. The
  exact command in HANDOFF.md works as given; carried it over unchanged.
- Long-job timings — cross-checked against `docs/RESULTS.md:171-174`
  (`2h48m17s` wall clock at 8 workers, write phase ~20 min, harness ~2h28m,
  single-threaded) and `docs/DESIGN.md:805` (hourly backfill ~5.4h, 13
  windows at 0.5 req/s) plus `docs/BUILD.md:653` (~4.6h for the 725-day
  backfill from a different vantage point — the two DESIGN/BUILD numbers
  bracket the task's stated 4.5-5.5h range, so I kept that range rather than
  picking one). `universe --quarter` "~10s" matches `docs/DESIGN.md:756`
  directly, so the "after the memoization fix" caveat in the task prompt is
  already the steady state, not a pending fix — I stated it as a plain
  measured number rather than describing a fix that already landed.

## What I found stale or wrong

- **`docs/SESSION_7_STATUS.md`** and **`docs/SESSION_7_RUNBOOK.md`** still
  quote `universe --quarter` at "10 min", which is roughly 60x the current
  measured cost. I did not touch these files (out of scope, and they're
  dated session artifacts, not living docs), but flagging it here in case
  they mislead someone later — nothing else in the repo besides
  `docs/DESIGN.md:756` has the corrected number.
- Nothing else in `SESSION_9_STANDING_ORDERS.md` or `HANDOFF.md` failed
  verification. Both were accurate against the current repo state for the
  rules in scope for this task.

## What I chose not to carry over, and why

- `SESSION_9_STANDING_ORDERS.md`'s framing ("Hard safety rules while any
  ingest runs" / "Every subagent prompt must carry these rules verbatim")
  is session-scoped process guidance for a specific autonomous run, not a
  durable repo rule. `CLAUDE.md` is read by every future session
  regardless of what's running, so I dropped the "while an ingest runs"
  qualifier and the "put this in every subagent prompt" instruction — the
  latter is now redundant once the rule lives in `CLAUDE.md`, which
  subagents already read.
- I did not carry over the specific table of Step-1 commands
  (`cscan earnings --historical`, `cscan shares --since-last`, etc.) from
  `SESSION_9_STANDING_ORDERS.md`. Those are one-time backfill sequencing for
  a specific session, not an operational safety rule for future sessions.
- I did not restate the ten non-negotiable invariants or the price-series
  table, per the task instruction — they're already correct and present.
- I did not add the "docker is not on PATH" psql line to `docs/TESTS.md`;
  the task asked only for a truncation warning there, cross-referenced
  rather than duplicated.

## Files changed

- `C:\Users\daris\Desktop\School\CapitalScan\CLAUDE.md`
- `C:\Users\daris\Desktop\School\CapitalScan\docs\TESTS.md`

No code files touched. No commands run beyond read-only verification
(`git rev-parse`, `grep`, `which docker`, one `select 1;` against Postgres).
