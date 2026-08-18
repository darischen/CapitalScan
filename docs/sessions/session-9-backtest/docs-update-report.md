# Docs update report — Session 9 completion, session10.md corrections

HEAD `455d64b`, branch `session-9-backtest`. Docs only, no code touched, no
DB writes (SELECT-only, `max_parallel_workers_per_gather=0`).

## docs/RESULTS.md

Replaced the abandoned 51-ticker dry-run "Backfill record" entry with the
current state, and filled in Phase 3's "Default config run" section.

Queries run and figures used:

- `SELECT count(*) FROM tickers;` -> 711 registered tickers.
- `SELECT interval, count(*), count(DISTINCT ticker), min(ts), max(ts) FROM bars GROUP BY interval;`
  -> `1d`: 2,900,865 bars / 615 tickers / 2005-10-11..2026-07-31.
  `1h`: 2,069,250 bars / 605 tickers / 2024-08-06..2026-07-31.
- `SELECT rule, severity, count(*) FROM bar_rejects GROUP BY rule, severity ORDER BY count(*) DESC;`
  -> 59,515 total rows across 8 rules; only `unexplained_split_like_move`
  (11) and `open_outside_range` (1) reach `reject` severity, the rest are
  `flag`.
- `SELECT is_active, delisted_on IS NOT NULL, first_bar IS NOT NULL, count(*) FROM tickers t WHERE NOT EXISTS (SELECT 1 FROM bars b WHERE b.ticker=t.ticker) GROUP BY 1,2,3;`
  plus a `LIMIT 30` listing -> 96 registered tickers have zero bars, all
  `is_active=false`; 19 of the 96 carry a stale `first_bar`/`last_bar` on
  `tickers` despite having no `bars` rows (e.g. `CPWR`). None of the 96 has
  a `delisted_on` date, so the drop reason isn't recorded anywhere.
- `SELECT config_hash, count(*), count(DISTINCT ticker), min(signal_date), max(signal_date) FROM events WHERE config_hash='3e598c59e7d71eae' GROUP BY config_hash;`
  -> confirms 246,116 rows / 575 tickers / 2010-01-05..2026-07-31 for the
  run of record.
- `SELECT split_key, count(*), count(DISTINCT ticker), count(*) FILTER (WHERE entry_price IS NOT NULL), count(*) FILTER (WHERE exit_date IS NOT NULL), count(*) FILTER (WHERE ambiguous), min/max(signal_date) FROM events WHERE config_hash='3e598c59e7d71eae' GROUP BY split_key;`
  -> split table (train 156,848/564, validate 21,672/69, holdout 67,596/124),
  matching the ledger exactly.
- `SELECT run_id, job, git_sha, status, started_at, finished_at FROM runs WHERE run_id='backtest_20260802T183304_6b1c5b52';`
  -> `git_sha='unknown'` on the run row (the job doesn't populate it — noted
  as a caveat in RESULTS.md, not corrected). `finished_at - started_at` is
  only ~19 min because `runs` only spans the write phase; the harness
  (~2h28m) runs after and isn't tracked in that table — the 2h48m17s total
  comes from the ledger's externally measured wall clock, stated as such.

Phase 3 gate table reproduced exactly as specified in the brief. All
caveats from the brief (thin `validate` split, 17-ticker hourly/daily
split-adjustment mismatch, BNY's missing split row, the +2.2-2.7pp
component-rate drift, two BRK-B filings) are recorded, each tied to its
source in the ledger/phase3-gate-measurement.md rather than restated as new
findings.

## docs/BUILD.md

- §0 session table: Session 9 marked "Phase 3 gate — complete, passed";
  added Session 10 row ("Forward path store and derived label layer" /
  exit test "Reconciliation against Session 9 labels passes with zero
  unexplained differences").
- Added a short paragraph after the table stating Session 9 is complete,
  pointing at RESULTS.md and phase3-gate-measurement.md, noting the sweep
  and entry-reuse decision remain open, and stating the Phase 4 boundary now
  falls after Session 10 (referencing session10.md §0 and §4, not
  restating them).
- SESSION 9 section header annotated "Complete — Phase 3 gate passed
  2026-08-02."
- Did not touch §9/§9a criteria text itself — session10.md now references it
  instead of restating it (see below).

## docs/session10.md — four corrections

- **§1**: replaced "Event count lands within tolerance of the analytical
  estimate" with a reference to BUILD §9a's three checks, plus one line
  explaining why the old wording was retired (ambiguous side/price field,
  ~4% estimate matched the close-based reading ADR 005 rejects).
- **§10.4**: added the "different windows for different labels" mismatch
  cause (MFE/MAE over `[t+1, exit_idx]` vs reachability over the full
  `[t+1, t+5]`, DESIGN §5.6) to the known-causes list.
- **§10.5**: rewritten. States the path table is the source of truth and the
  new label families are queries, not materialized columns; gives the
  40+40=80-column grid math and per-new-threshold/horizon cost; gives the
  three derivation formulas (reached-by-day, first-touch, return-at-day);
  states Session 9's existing columns stay as cache/serving and scopes
  `fwd_ret_1d..10d`'s existing presence on `events` explicitly; states a
  probability distribution is a Phase 4 aggregate, not a per-event label;
  requires reuse of `enrich.py`'s `_pct_suffix` for naming; defines giveback
  against `capture_ratio = R_exit / MFE` (null when `MFE <= 0`) and the
  existing `time_to_mfe`, and notes ADR 089's unclamped MFE is load-bearing
  for that computation; flags (without writing) that expanding terminal
  quantiles to all five horizons roughly triples DESIGN §7.4's eleven model
  heads and needs its own ADR before Phase 6.

## Discrepancies found vs. the brief

- The brief's Phase 3 gate table cites "Ambiguity rate < 10%" as 28/110,954 =
  0.025%; this figure was already confirmed pre-session per the ledger, not
  independently re-derived here (the brief itself presents it as given, and
  RESULTS.md states it that way rather than re-running the ambiguity query).
- Everything else in the brief checked out against the ledger and the live
  DB without contradiction: the 246,116/575, config_hash, gate-table
  results, and both `session10.md` facts (existing `fwd_ret_*d` columns and
  the distribution-is-an-aggregate point) all matched what's actually in the
  schema and event data.

Report path: `docs/sessions/session-9-backtest/docs-update-report.md`
