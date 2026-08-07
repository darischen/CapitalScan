# SDD ledger — plan: docs/superpowers/plans/2026-08-01-session-9-backtest.md

## Pre-flight conflict scan (controller, resolved with user 2026-08-01)

Four plan-vs-codebase conflicts found before Task 1. User ruling: option (a) on all four.

- **C1 — Task 2 rebuilds `config_hash`/`BacktestConfig`.** `jobs/config.py:223`
  already implements the exact specified algorithm over `core.config.Config`,
  which carries all six plan sections plus `stats` (Tasks 7/8 need
  `reach_targets`, `dd_buckets`, `era_bounds`).
  RULING: reuse. `BacktestConfig = core.config.Config`; re-export
  `jobs.config.config_hash`. No second hash.
- **C2 — Task 2 rebuilds `split_key_for`.** `compute.py:525 _split_key` exists.
  RULING: one shared strict implementation (raises below `sp.event_start`),
  used by both `run_events` and the backtest.
- **C3 — Task 3 Step 3 mandates positional t-1 pairing** (`bars.iloc[i]` /
  `indicators.iloc[i-1]`). Frames are not guaranteed aligned; a one-sided gap
  shifts every later pairing silently. Invariant 3.
  RULING: date-based lookup (latest indicator strictly before the bar date),
  matching `compute.py:733`. CLAUDE.md invariant 3 governs over plan text.
- **C4 — upsert clash.** `run_events` stamps `config_hash(Config(signals=sp))`,
  byte-identical to the backtest's ADR 059 default config hash. `db_io.upsert`
  sets every non-key column from EXCLUDED, so `run_events` nulls the backtest's
  exit columns by omission. Plan Global Constraints say "write complete rows";
  `compute.py:13-20` says Session 9 needs a column-scoped upsert.
  RULING: add optional `update_columns` to `db_io.upsert`; both `run_events`
  and `run_backtest` declare the columns they own. Rejected alternatives:
  read-merge (breaks ADR 060 purity), COALESCE (makes nulls unwritable,
  violates invariant 4).

Branch: session-9-backtest (not main). No worktree: .venv is Windows-locked by
the live toolchain and the DB is bound to this working copy.

## Tasks

Task 1: complete (commits 03242ae..c5b0a0a, review clean)
Task 1: controller-resolved ⚠️ — reviewer flagged `max_workers=1` on cli.py's
  run_indicators call as scope creep. Confirmed pre-existing uncommitted
  working-tree change present before Task 1 dispatch (seen in `git diff` at
  session start); functional no-op matching compute.py:159's default. Controller
  authorized the bundling in the dispatch prompt. Not a gap, no fix round.
Task 1: minor (deferred): test_nightly_chain.py:127 recomputes `date.today()`
  independently of the value nightly() used; midnight-boundary race only.
Task 2: complete (commits c5b0a0a..5e5dd71, review clean)
  C1/C2 rulings honored: BacktestConfig = Config alias, config_hash re-exported,
  split_key_for canonical in jobs/config.py, compute._split_key delegates.
  Note: existing config_hash coverage lives in test_compute_helpers.py::TestConfigHash.
Task 2: minor (deferred): test_backtest_config.py:254 event_start boundary test
  would be sharper with event_start distinct from train_end.
Task 3: complete (commits 5e5dd71..fe8c921, review clean)
  C3 ruling honored: date-based t-1 pairing, mirroring compute.py:731-738.
  Differential t/t-1 fixture verified genuine by reviewer.
Task 3: controller rulings on the three implementer concerns —
  (1) `scan_candidates` returns tuple[DataFrame, list[dict]], NOT the brief's bare
      DataFrame. Null rejects have nowhere else to go (candidates carry no
      indicator columns). ACCEPTED — controller pre-authorized the placement.
      *** Task 9's dispatch must carry this actual signature. ***
  (2) `_in_trade` now has two copies: candidates.py:149-165 and compute.py:624-635.
      RULING: real duplication, consolidate in Task 9 (which owns run_backtest's
      universe-flag loading). Carry into Task 9's dispatch. Not fixed here.
  (3) Brief's debounce example ("two touches one day apart collapse to one row")
      contradicts DESIGN §4.7 "one event per ticker per bound per day" and shipped
      `core.signals.debounce_key`. RULING: DESIGN + shipped code govern over plan
      text. Implementer tested shipped semantics — correct. Plan text is wrong.
Task 3: minor (deferred): apply_eligibility defaults `today` to date.today()
  (candidates.py:185) — a clock read. ADR 060 forbids wall-clock reads inside the
  engine. *** Task 9 must always inject `today` explicitly. ***
Task 3: minor (deferred): per-ticker `ticker ==` filtering is O(tickers x rows);
  mirrors shipped compute.py pattern, not new debt.
Task 3: minor (deferred): debounce relies on caller-supplied ordering.
Task 4: review found 1 Important (silent single-cluster collapse when a ticker is
  absent from trading_dates: bisect on [] returns 0, gap never exceeds
  max_hold_days, whole ticker becomes one endless cluster) + 2 minors.
  C5 ruling honored: trading-bar counting verified genuinely discriminating from
  calendar-day by the reviewer; (ticker, side) keying and _deterministic_id
  algorithm match compute.py byte-for-byte.
  DIVERGENCE ON RECORD: research.candidates.tag_clusters counts trading bars;
  jobs.compute._tag_clusters counts calendar days. The two disagree on cluster
  boundaries for the same events. Backtest owns the cluster columns; Task 9 must
  assign a single writer at the DB layer (see C4).
Task 4: fix round 1/5 dispatched (resumed implementer aaef0152f5a07b8a3)
Task 4: fix round 1/5 (3 addressed, 0 open; commits 2ed2949..c432f5e)
  Raise added at both boundaries: ticker absent/empty in trading_dates, and
  signal_date absent from that ticker's dates. Both hard raises, both tested,
  guard runs before any clustering work.
Task 4: complete (commits fe8c921..c432f5e, review clean)
Task 5: complete (commits c432f5e..23c218b, review clean)
  entry_gapped: None for NEXT_OPEN (justified); attached to TOUCH_5M/TOUCH_30M as
  well as TOUCH — reviewer independently ruled this the more defensible reading,
  since all three pass touch_level to entry_price_for. Slippage adversity verified
  on BOTH sides with exact-magnitude assertions.
Task 5: minor (deferred): enrich.py:145 assumes at most one bar per ticker-date;
  a duplicate-date row would make .loc return a DataFrame and fail obscurely.
Task 5: minor (deferred): _as_date coerces pd.Timestamp but not stdlib datetime.
Task 6: review found 1 Important (no entry_idx consistency guard; a caller passing
  the signal-bar position for NEXT_OPEN, which fills at t+1, shifts the whole
  forward window one bar silently). Implementer had raised the same concern.
  Verified good: ind_at_entry always passed AND the test genuinely requires it
  (reviewer confirmed the fixture yields TIMEOUT instead of UPPER_BAND when the
  arg is omitted). Forward window is entry_idx+1.., proven by a target planted at
  absolute position 3. exit_reason uses .value, matching the text column.
  Surfaced `exit_idx` in the output dict for Task 7.
Task 6: fix round 1/5 dispatched (resumed implementer a500d26be86a8c681)
NOTE: wrote .superpowers/sdd/2026-08-01-session-9-backtest/CONSTRAINTS.md —
  consolidated safety rules, invariants, conventions, and rulings C1-C5. Point
  every later dispatch at it instead of restating.
Task 6: fix round 1/5 (2 addressed, 0 open; commits e42de7d..dc591a3)
  _assert_entry_idx_matches raises on mismatch and on out-of-range (negative and
  >= len). Date normalization symmetric via the existing _as_date, with an
  explicit Timestamp-vs-date non-raise test. Guard sits after the NaN
  short-circuit so never-filled entries cannot reach it.
Task 6: complete (commits 23c218b..dc591a3, review clean)
Task 7: complete (commits dc591a3..4d474f1, review clean after controller ruling)
  Reviewer verdict was "Needs fixes" on ONE process point only: the
  StatsParams.fwd_ret_horizons addition changes config_hash on 1,292,276 live
  event rows. Code itself verified correct on every named risk (two-window
  separation with an exit-before-touch fixture, capture_ratio guard at <=0,
  MFE unclamped with a genuinely negative fixture, column-name derivation
  proven to yield the four schema names, day_touched_* None not NaN).
Task 7: *** CONTROLLER RULING C6 — user decided (option A): ACCEPT the hash bump. ***
  Rationale: fwd_ret_horizons genuinely parameterizes what lands on each event
  row, so per ADR 060 the hash SHOULD change. StatsParams was already inside the
  hash surface (reach_targets, adverse_targets, dd_buckets, era_bounds).
  Confirmed against DESIGN §7.4: fwd_ret_*d are NOT model outputs — the model's
  targets are R_5 timeout return, touched_Xpct, touched_-Ypct. DESIGN §5.7 calls
  fwd_ret_*d "unconditional forward returns, for baseline comparison" — a
  measurement feeding Phase 4 baselines, not a prediction.
  ACTIONS THIS CREATES:
   - Task 11 must REPORT the new default config_hash so the user can update the
     `capitalscan.default_config_hash` GUC that v_events reads.
   - The 1,292,276 old rows under edf5658f5da3807a are NOT auto-pruned; upsert
     never deletes. Pre-2010 creation is now blocked two ways (Task 3
     apply_eligibility window + Task 2 split_key_for raise) but the 65,767
     existing pre-2010 rows persist. Purge decision deferred to Task 11, AFTER
     the new rows land, and requires explicit user approval before any DELETE.
   - Task 11 must measure and report actual backtest runtime.
Task 7: signature widened to add exit_price, adj_close_fwd, horizons — reviewer
  independently confirmed all three genuinely necessary (exit fills can land at a
  gap open/stop/target/band, none recoverable from fwd_bars close; fwd_ret_10d
  needs 10 bars but fwd_bars is bounded at max_hold_days=5). Plan doc line is stale.
Task 7: minor (deferred): resolve_exit already computes mfe/mae/time_to_mfe inside
  core.exits.resolve_exit but discards them; path_metrics recomputes. Pure and
  deterministic so they cannot diverge, but it is duplicate CPU at 1.29M scale.
  Task 9's wiring could surface them from Task 6 instead.
Task 7: minor (deferred): no test for adj_close_fwd is None on a resolved position.
Task 8: review found 2 Important.
  (1) earnings_in_window compared days_to_earnings against the REALIZED
      holding_days, not the fixed window. Concrete failure: stopped out day 2,
      earnings day 4 -> flag says False, but touched_5pct/day_touched_5pct on the
      same row were computed over the full [t+1,t+5] per DESIGN §5.6, so days 3-5
      are earnings-driven and marked clean. Governing text ADR 036
      (DECISIONS.md:1518): "A 5-day window containing an earnings report is
      contaminated regardless of session." FIX DISPATCHED round 1/5: compare
      against ep.max_hold_days, adding ep: ExitParams to the signature.
  (2) era open-era label "2024+" vs ADR 042's literal "2024-2026".
      *** CONTROLLER RULING C7 — user decided: KEEP "2024+". ***
      Honest, never goes stale, derivable from era_bounds with no literal so
      invariant 9 holds. FOLLOW-UP: ADR 042 needs an amendment noting the open
      era is deliberately unbounded. Do at session wrap-up, do not edit silently.
  Verified good: costs subtract on both sides (tested on WINNING trades too,
  stricter than asked, plus an isolated short-vs-long borrow delta); dd_bucket
  labels genuinely derived from sp.dd_buckets and tested against the real
  jobs.compute._dd_bucket rather than restated literals, agreeing at all three
  exact boundaries; split_key delegated to split_key_for; bw_regime returns None
  with sound justification (DESIGN §6.7 treats bandwidth as a continuous feature,
  not a bucketed cell) — this is compliant, not a gap.
Task 8: signature also gained cp: CostParams (necessary for apply_costs).
  *** Task 9's call site must pass BOTH cp: CostParams AND ep: ExitParams. ***
Task 8: fix round 1/5 (1 addressed, 1 deferred-by-ruling C7, 0 open;
  commits 778ac57..6e05887)
  _earnings_in_window now takes ep and compares 0 <= days_to_earnings <=
  ep.max_hold_days. Boundary is <= (report on the last bar is inside). Regression
  test holding_days=2 / days_to_earnings=4 / max_hold_days=5 asserts True and
  would have been False under the old rule — genuinely discriminating.
  The old "holding_days is None -> None" branch is deliberately gone: with a
  fixed window anchored to the signal date, whether the trade resolved is not
  input to this flag. Reviewer scrutinized and confirmed this is reasoned, not
  accidental.
Task 8: complete (commits 4d474f1..6e05887, review clean)
Task 8: minor (deferred): bw_regime permanently None (justified, unimplemented).
Task 8: minor (deferred): em-dash -> "--" cosmetic churn bundled into the fix commit.

## Facts verified against the live DB (controller, before Task 9)

events currently holds TWO config_hash generations:
  edf5658f5da3807a  1,292,276 rows  2010-01-04 .. 2026-07-31  pre_2010 = 0
  39e6a590aa799780        119 rows  2026-07-27 .. 2026-07-31  pre_2010 = 0
CORRECTION to AUTONOMOUS_RUN_FINDINGS.md FINDING 7: there are NO pre-2010 rows.
  The 65,767 it reports were cleaned up before this session. The planned DELETE
  is supersession hygiene, not garbage removal.
No foreign keys reference events (checked db/schema.sql), so the DELETE is
  unconstrained and cascades nothing.
OPEN: what produced 39e6a590aa799780? Unknown. Look before the final review.
DELETE timing agreed with user: AFTER Task 11's run lands and is verified, never
  before. Requires explicit user approval at that point.

Task 9: FIRST DISPATCH TERMINATED (opus, session limit, 2026-08-01 ~03:10 PT).
  Agent was still in the reading phase — 27 tool uses, 117k tokens, zero commits.
  Verified: HEAD still 6e05887, working tree clean apart from the pre-existing
  docker-compose.yml change. Nothing to salvage or revert.
  Re-dispatching fresh. Mitigation: appended verified public signatures to
  CONSTRAINTS.md so the implementer reads less and writes more, and split the
  work — Task 9a (db_io column-scoped upsert + _in_trade consolidation) before
  Task 9b (worker, dispatch, cofire, write). The original single task was the
  largest in the plan and the reading cost alone exhausted a session.
Task 9a: implemented (commit 4656674), 598 tests pass. Review dispatched.
  Scope was the two shipped-code changes only: db_io column-scoped upsert (C4)
  and the _in_trade consolidation. Backtest pipeline is 9b.
  Implementer flagged: Task 9b must define its own events update_columns list
  INCLUDING the four cluster columns (cluster_id, seq_in_cluster,
  is_cluster_head, days_since_head) per ruling C5.

## config_hash state (controller, verified 2026-08-02)

Current default config_hash(Config()) = 22df3117b890793b
  vs edf5658f5da3807a on the 1,292,276 existing rows. The C6 bump is real and
  already in effect (StatsParams.fwd_ret_horizons did it).
PROVISIONAL — any config field added by Tasks 9b-12 moves it again. Task 11 must
  report the value actually stamped on the written rows, and that is what the
  user sets:
  ALTER DATABASE capitalscan SET capitalscan.default_config_hash = '<value>';
Both existing generations came from the `events` job (joined runs.job):
  edf5658f5da3807a  1,292,276 rows  611 tickers  has_exit=0
  39e6a590aa799780        119 rows   36 tickers  has_exit=0
  The 119-row one is a nightly run under a different SignalParams. Not alarming
  — RESOLVES the "what produced 39e6..." open question from the Task 8 entry.
  Neither generation has any exit data; run_events never computed exits.
Task 9a: review found 1 Important (update_columns=[] falls through validation to
  an opaque SQLAlchemy internal error) + 1 Minor (duplicate names untested).
  Reviewer verified all three named risks by direct inspection, not report claims:
   - update_columns=None branch is character-for-character the pre-change column
     expression; no existing caller passes the new kwarg. Backward compatible.
   - _RUN_EVENTS_UPDATE_COLUMNS exactly equals _build_event_row's 30 keys minus
     the 5 conflict columns. No gap. Exit columns are absent from the dict
     entirely, so they never enter the INSERT column list.
   - _in_trade fail-open branch preserved line-for-line.
  _in_trade's new home is core/universe.py:54-79 — takes an already-loaded
  DataFrame, performs no IO, so invariant 1 holds. Both jobs/ and research/
  delegate to it.
Task 9a: fix round 1/5 dispatched (resumed implementer adc6a9b29c402595c)
Task 9a: fix round 1/5 (2 addressed, 0 open; commits 4656674..32d74fb)
  Empty-list guard is correctly NESTED inside `if update_columns is not None:`,
  so None short-circuits before the truthiness check — backward compatibility
  for every existing caller preserved. Duplicate-name test asserts compiled SQL
  (count of "run_id = excluded.run_id" == 1), not merely absence of an exception.
Task 9a: complete (commits 6e05887..32d74fb, review clean)

## FINDING (controller, 2026-08-02) — the trade-universe filter is inert for
## 99.97% of the study period, then switches on abruptly

`universe` holds exactly ONE evaluated quarter:
   as_of 2026-06-30 | 621 rows | 39 in_trade | 621 in_train

`core.universe.in_trade` fails OPEN when no evaluation exists on or before the
signal date (documented v1 fallback, so run_events works before run_universe has
ever run). With one row dated 2026-06-30 that produces a hard discontinuity:

  signal_date < 2026-06-30   -> no row matches -> fail open -> ALL tickers pass
  signal_date >= 2026-06-30  -> row matches    -> only 39 tickers pass

Measured on the existing events table:
  before as_of  1,291,851 events  611 tickers  2010-01-04 .. 2026-06-29
  on/after      *)      425 events   39 tickers  2026-06-30 .. 2026-07-31

CONSEQUENCE: the backtest applies the same filter through
`candidates.apply_eligibility`, so its output inherits the same discontinuity.
The ADR 014 trade-universe filter is effectively UNAPPLIED across the whole
historical record and applied only to the final month. Anything in Phase 4 that
conditions on the trade universe is measuring the filter for one month and
nothing before it. DESIGN §5.2 step 2 says "join universe membership by
quarter", which presumes a row per quarter; 66 quarters are missing.

NOT a Session 9 code defect — the engine correctly applies the data that exists.
It is a data gap: `run_universe` has only ever been run for 2026Q3.

NOT blocking the Phase 3 gate. None of its five criteria (exit invariants,
ambiguity rate, event rate, determinism, harness checks) depend on the universe
filter, and 425 suppressed events out of 1.29M cannot move the event rate.

Options for the user, to raise BEFORE Task 11's run:
 (a) Run the backtest now; backfill `universe` quarterly later and regenerate.
 (b) Backfill `universe` for 2010Q1..2026Q2 first (66 quarters x ~620 tickers,
     several queries each — expensive, and note `_revenue_growth_positive` is a
     permanent None stub so crit_rev_growth stays unevaluated regardless).
 (c) Accept fail-open uniformly for the historical period by design, and record
     it in RESULTS.md as a stated limitation of the v1 backtest.

Task 9b: implemented (commit 0441b5f), 619 unit+property + 2 spawn-guard pass.
  Review dispatched (opus — largest and highest-risk diff in the plan, 46KB).
  Implementer raised two concerns, both routed to the reviewer for independent
  judgment rather than accepted at face value:
   (1) _RUN_BACKTEST_UPDATE_COLUMNS overlaps _RUN_EVENTS_UPDATE_COLUMNS on five
       columns (run_id, signal_types_all, signal_strength, side, touch_level).
   (2) *** LIKELY REAL GAP *** Indicator-state columns (bb_pctb, atr_14, k_full,
       d_full, k_fast, dd_52w, sma200_slope_60, vol_z_20d, rv_pct_252d,
       bb_width_pct, above_sma200, days_to_earnings) are left NULL on rows only
       run_backtest writes — the touch_5m / touch_30m / next_open entry kinds —
       because scan_candidates does not retain them. Implementer calls this an
       intentional invariant-4 null.
       Controller's read: invariant 4 sanctions a null that is genuinely
       UNKNOWN, not a value that is known upstream and simply not carried
       through. DESIGN §5.7 groups these under "state at signal, all from t-1"
       with no per-entry-kind exemption, and the grain is one row per
       (config_hash, ticker, signal_date, signal_type, entry_kind) — so four
       rows describing ONE signal would carry that signal's state on one row and
       NULL on three. A Phase 4 query conditioning on dd_bucket or k_full would
       silently see only the touch rows. Expect this to come back Important.
Task 9b: review returned 1 CRITICAL + 3 Important + minors. Fix round 1/5
  dispatched (resumed implementer a06167378ab328540).
  VERIFIED CORRECT by reviewer (do not re-litigate): spawn safety incl.
  render_as_string(hide_password=False) and the __main__ guard; `today` injected
  explicitly with no clock read anywhere in the engine; both price series traced
  concretely (fwd_bars/exit = split-adjusted ticker_bars, adj_close_fwd =
  ticker_bars["adj_close"]); exit_idx frame-relativity — backtest's fwd slice is
  byte-identical to the one resolve_exit_for_entry computes internally; full
  un-truncated forward window to path_metrics; _entry_idx_for derives from
  entry_date not entry_kind; every _RUN_BACKTEST_UPDATE_COLUMNS name exists on
  events and none is a conflict column (checked against the migration); no
  Task 10/11/12 scope creep.
  CRITICAL: state-at-signal columns dropped from rows only run_backtest writes.
    Reviewer disproved BOTH premises of the implementer's "intentional null"
    defense: (a) prior_ind is in scope at backtest.py:419 and passed to
    enrich_context at :492, one line from the row dict; (b) run_events hardcodes
    entry_kind=TOUCH (compute.py:721) so touch_5m/touch_30m/next_open have NO
    second writer, ever — "filled in later" cannot happen. dd_bucket IS
    populated from prior_ind["dd_52w"] while dd_52w itself is NULL on the same
    row, which is the tell.
  IMPORTANT 2: the ADR 060 determinism sort test passes with the sort DELETED
    (max_workers=1 iterates sorted_tickers, so concat is pre-sorted). Both
    two-run comparisons share the defect — identical construction path twice.
  IMPORTANT 3: cofire_count computed over the dispatched subset but written as
    universe-wide; a --tickers re-run silently overwrites correct values.
  IMPORTANT 4: one worker exception discards every completed ticker; write
    happens only after all futures return.
  *** CONTROLLER RULING C8: per-ticker `today` (bars max date) is KEPT — it is
  deterministic and the honest per-ticker bound. Add an optional
  today: date|None = None override on run_backtest for Task 11's CLI. ***
  NOT A FINDING: the 5-column overlap between the two update lists is correct;
  both jobs derive them from the same core.signals.detect output.
  DEFERRED to final review: _read_market_days full-table read per worker;
  the 125-line candidate/entry double loop.
Task 9b: fix round 1/5 committed defd732, 632 tests + 2 spawn-guard pass.
  Implementer reports manually verifying the sort tests now FAIL when
  sort_values is deleted, then restoring it. Re-review dispatched (opus) with
  instruction to verify that claim from the tests' construction rather than
  accept it — the prior round's report was factually wrong on two premises it
  asserted confidently.
Task 9b: fix round 1/5 (5 addressed, 0 open; commits 0441b5f..defd732)
  All 14 state columns present in _EVENT_COLUMNS, _RUN_BACKTEST_UPDATE_COLUMNS,
  and the row dict. t-1 provenance verified clean: 13 read prior_ind.get(),
  _prior_indicator selects index < signal_date (strictly before, no <=), and
  nothing reads aligned_ind or the signal bar's own indicators. Invariant 3 holds.
  above_sma200 DERIVED (not nulled) as bool(signal-bar close > prior_ind
  sma_200), matching compute._build_event_row exactly. Cross-writer agreement
  checked column by column — compute's bb_pctb/k_full come via hit.pctb/hit.k_full
  which core/signals.py:221-222 reads off the same prior_ind. No divergence.
  Determinism: reviewer independently traced that the amended tests FAIL with
  sort_values deleted (fake worker now emits AAA's rows out of order, so dispatch
  order cannot repair within-ticker order). Three guards now, at least two of
  which break on sort deletion.
  Finding 3 solved via full_universe: bool = True — warns and strips cofire_count
  from update_columns on a partial run, so a subset can never UPDATE over a
  correct universe-wide value.
  Finding 4: per-future try/except, BacktestReport.failed_tickers dict.
  Ruling C8 implemented: today: date|None on both run_backtest and the worker.
Task 9b: fix round 2/5 dispatched — NEW Important defect created by the
  INTERACTION of round 1's own fixes 4 and 5: the per-ticker `except Exception`
  swallows the config-level ValueError from the path-key guard, converting a
  broken-config run into a clean rows_written=0 report. Task 12 sweeps 18
  configs; a malformed one would report "ran fine, found nothing".
  Plus 2 minors: atr_14 and days_to_earnings are None in the test fixture so
  NO test would fail if either were dropped again (2 of the 14 Critical columns
  unguarded); and backtest.py imports private _isnan while compute.py uses
  pd.isna for the identical above_sma200 guard.
Task 9b: fix round 2/5 (3 addressed, 0 open; commits defd732..8993aab)
  BacktestRunFailed(RuntimeError) raised only when sorted_tickers is non-empty
  AND every dispatched ticker failed. Verified correct: a ticker returning zero
  events never enters failed_tickers (only future.result() raising does), so a
  legitimately-empty run does not raise; empty tickers list short-circuits.
  Partial path untouched. Both tests discriminating in opposite directions.
  atr_14=0.5 and days_to_earnings=45 now non-null in the fixture and asserted.
  Private _isnan import dropped for pd.isna, matching compute._build_event_row.
Task 9b: complete (commits 32d74fb..8993aab, review clean)

Task 10: implemented (commit dcc612b), 651 tests pass (18 new). Review dispatched
  (opus — this is the Phase 3 gate itself).
  Implementer raised two concerns, both routed to the reviewer for independent
  judgment:
   (1) bars_by_ticker shape is unpinned by the brief; it chose bars+indicators
       merged per ticker so the look-ahead check can rerun scan_candidates.
       *** Task 11's CLI must build that shape. ***
   (2) *** REAL TENSION *** The non-overlap check groups cluster heads by TICKER
       ALONE, while tag_clusters keys on (ticker, side) per ruling C5.
       Consequence: a long head and a short head on one ticker with overlapping
       windows are two independent positions by the tagger's definition, but the
       check reports them as a violation — so the Phase 3 GATE COULD FAIL ON
       DATA THE ENGINE PRODUCED CORRECTLY.
       Counter-argument worth weighing: DESIGN §5.3 says is_cluster_head exists
       for "non-overlapping, clean standard errors". A long and a short on the
       same ticker over the same window are perfectly negatively correlated, not
       independent observations, so ticker-alone may be the statistically
       correct grouping even though it disagrees with the tagger.
       Asked the reviewer to state plainly which is right, not just note it.
Task 10: first review dispatch TERMINATED (opus, session limit, before reading
  anything). Verified HEAD still dcc612b, tree clean, review package intact on
  disk. Re-dispatched on sonnet — the 50KB diff does not need opus, and opus has
  now burned two dispatches on session limits this session.
Task 10: review found 2 Important, both in the non-overlap check. Fix round 1/5
  dispatched.
  *** The (ticker, side) vs ticker-alone tension is RESOLVED, no user escalation
  needed. *** Reviewer's argument, which the controller verified: DESIGN §5.3's
  purpose for is_cluster_head (its own table, line 993, "Non-overlapping, clean
  standard errors") is avoiding double-counting overlapping SAME-SIDE
  observations, not merging opposite-side trades into one statistical unit. And
  primary statistics separate by signal_type when forming cells — signal_type is
  itself side-bearing (confluence_low vs confluence_high, bb_lower_touch vs
  bb_upper_touch) — so a long and a short land in DIFFERENT cells and cannot
  double-count. The correlation concern never arises. tag_clusters' docstring
  already states the ticker-only merge is wrong. Fix to (ticker, side).
  Finding 2: non-overlap silently `continue`s on a ticker missing from
  bars_by_ticker while entry/exit sanity emit a violation for the identical
  condition — so non_overlap.passed can read True on unchecked input.
  VERIFIED CORRECT by reviewer against real source (do not re-litigate):
   - _pre_slippage_price is the exact inverse of enrich.py:161-168
   - exit prices carry NO slippage (core/exits.py:220; slippage charged in
     return space at core/costs.py:54), so exact comparison is right
   - gross_ret recomputation matches enrich.py:675 exactly
   - look-ahead routes only through scan_candidates, never reimplements
   - all four Jaccard bounds are the exact TESTS.md §3.1 inequalities as named
     constants with citations, not a naive threshold
   - NaN: both-NaN matches, exactly-one-NaN violates, with a dedicated test
   - all five checks have violation-constructing tests, and
     test_all_passed_is_false_if_any_single_check_fails proves check independence
  OPEN for Task 11: bars_by_ticker is bars+indicators merged per ticker. The
  reviewer confirmed the shape is internally coherent (no OHLCV/indicator column
  collisions) but task-11-brief.md never mentions run_harness or building it.
  *** Task 11's dispatch must specify this shape explicitly. ***
Task 10: fix round 1/5 (2 addressed + minors, 0 open; commits dcc612b..48eb903)
  groupby(["ticker","side"]) at harness.py:233. Both tests discriminate: the
  long/short-overlap test fails if the key is reverted to ticker-only; the
  same-side test fails if the check is loosened. Trading-bar counting untouched
  (bars are not per-side, so _trading_bars_between is called exactly as before)
  and still agrees with ruling C5.
  Missing-bar tickers now emit a `no_bars_for_ticker` violation that flips
  passed to False — stronger than the controller's minimum bar. n_checked split
  into n_priced/n_validated. Empty-events and ticker-absent tests added.
Task 10: complete (commits 8993aab..48eb903, review clean)

## Task 11 SPLIT by the controller

Task 11 bundles CLI wiring (pure code, no side effects) with the ADR 059
default-config RUN (writes ~5M rows to the live research DB). Splitting:
  11a = the `cscan backtest` command + tests. Dispatch now.
  11b = the actual run. HELD. Requires explicit user approval — it is the first
        non-test action in this session that touches production data, and the
        universe-filter question (a/b/c) has been open across three asks.
Task 12 (sweep_configs) is also pure code and can proceed without the run.
Task 11a: implemented (commits ec11294, 74546bd), 676 tests pass. Review dispatched.
  Implementer concerns routed to reviewer: (1) _load_bars_by_ticker reads one
  ticker at a time; (2) the harness now runs automatically on every non-sweep
  invocation, doubling reads.

## USER DECISION — universe filter: option (a)

Run the backtest now; backfill `universe` later. User will do the backfill
MANUALLY themselves.
Controller advice given:
 - NOT blocked by any Session 9 code; run_universe is untouched this session and
   the backtest only reads the universe table.
 - Safe to run concurrently with 11a/12 (pure code, no DB).
 - MUST NOT overlap with 11b (the real run) or acceptance: not a locking issue
   (MVCC) but a DETERMINISM one — parallel workers would resolve eligibility
   against different universe states mid-run, violating ADR 060.
 - *** COST WARNING given: run_universe is O(n^2). Per quarter it loops ~620
   tickers, and because tickers.sector is NULL for every row,
   _sector_median_return (compute.py:333) takes the fallback branch and calls
   _rel_return_756d for ~620 peers, each a 757-row query. ~385,000 queries per
   quarter, ~25M across 66 quarters — and the median is IDENTICAL for every
   ticker in a quarter, recomputed 620 times. Read from code, not measured. ***
 - Offered two mitigations: sample one quarter per year (16 runs, not 66 —
   in_trade resolves to the most recent evaluation on or before the signal date,
   so annual granularity still gives a real filter), OR let the controller hoist
   the universe-wide median to one computation per quarter first (~620x cut).
   User has not yet chosen between these.
 - cscan universe takes ONE quarter per invocation (cli.py:205); no batch flag.
   FutureQuarterError blocks any quarter that has not ended.
Task 11a: complete (commits 48eb903..74546bd, review clean — Approved)
  Reviewer verified against real code, not report claims: --tickers sets
  full_universe=False (cli.py:268-269 -> backtest.py:796-805,848-850 warning +
  cofire_count strip); the --sweep gate is STRICTER than the brief asked
  (status='ok' AND notes IS NULL AND full_universe='true' AND matching
  config_hash), and a partial-failure run cannot satisfy it because cli.py:290-296
  sets report.notes whenever failed_tickers is non-empty; default config from
  Config() with no restated literals; all tests stub IO; today omitted so ruling
  C8's per-ticker default applies; harness signature and _BAR_COLUMNS match;
  config_hash printed first; BacktestRunFailed -> clean error exit 1; partial
  failure -> exit 1 with a stated rationale (Task Scheduler catch-up needs a
  machine-checkable signal); --config-name hard-errors rather than being ignored.
Task 11a: *** CONTROLLER RULING C9 on the reviewer's ⚠️ (Config() bypasses
  jobs.config.resolve_config): NOT a defect, 11a stands. ***
  Verified by grep: resolve_config is called by NO command anywhere in
  capitalscan/ outside tests — the only two references are docstrings Task 11a
  itself wrote. It is dead code from Session 0: written, tested
  (test_jobs_config.py), never wired. So `Config()` is consistent with every
  other command, not an inconsistency 11a introduced.
  PROJECT-LEVEL FINDING for the final review / a later session: the sanctioned
  CLI>env>toml>default resolution chain (ADR 091, BUILD §0.5) exists and is
  tested but has never been connected to the CLI. Any CAPSCAN_* env var or
  config.toml override is currently inert for EVERY command.
Task 11a: minor (deferred): _load_bars_by_ticker's docstring claims parity with
  _backtest_one_ticker's lower bound, but the engine reads indicators from
  splits.event_start while the CLI uses ingest_start. Harmless extra history.
Task 11a: minor (deferred): cli.py:517-519 prints tickers={n_with_events}/{n_resolved},
  but BacktestReport.tickers means "tickers with >=1 event written". A clean run
  over tickers with no signals prints tickers=0/5, which reads as 5 failures.

Task 12: implemented (commit 5df570e), 690 tests. Review dispatched.
  *** ENTRY-REUSE OPTIMIZATION IS NOT IMPLEMENTED — reported honestly, no
  misleading call-counter test written. *** run_backtest resolves entries
  per-config inside _backtest_one_ticker, so running all 18 sweep configs costs
  18 FULL passes, not DESIGN §5.9's "1 candidate pass + 18 exit passes / ~4-5
  minutes". The plan and DESIGN both overstate what exists. Controller must
  decide whether to scope the restructuring; it is NOT a Phase 3 gate criterion.
  SweepParams added as a STANDALONE dataclass in core/config.py, deliberately
  NOT a field of Config. Controller independently verified
  config_hash(Config()) == 22df3117b890793b, UNCHANGED. The user is tracking
  that value for the Postgres GUC.
  Controller ran the property suite after this commit: 20 passed, clean. The
  "pre-existing Hypothesis flake" the report mentions did NOT reproduce —
  recorded as non-reproducing, not fixed.

## USER DECISIONS (2026-08-02, mid-turn)

 - O(n^2) fix: do BOTH memos (dict keyed on (sector, as_of) AND memoize
   _rel_return_756d). Already dispatched exactly that way; agent running.
 - resolve_config wiring: DO IT, sequenced AFTER the O(n^2) fix and BEFORE the
   final sweep. Controller verified it is safe/behaviorally inert right now:
   no config.toml at repo root and no CAPSCAN_* env vars set, so resolving
   would yield identical defaults and config_hash stays 22df3117b890793b.
   Only takes effect if the user later sets a toml/env override — which is the
   intended behavior per ADR 091 / BUILD §0.5.
Task 12: complete (commits 74546bd..5df570e, review clean — Approved)
  Reviewer verified the stop-mode collapse directly against core/exits.py:42-58:
  stop_atr_k is read ONLY in the atr branch (line 53); fixed uses stop_fixed_pct
  (55); none returns NaN unconditionally (48-49) and never reads stop_atr_k. So
  holding stop_atr_k at the base value under fixed/none is behaviorally inert and
  NO pair of the 18 configs can be behaviorally identical yet differently hashed.
  That was the subtle risk and it is handled correctly.
  Confirmed: no misleading call-counter test exists. The one entry-related test
  only proves sweep_configs itself never calls resolve_entries (trivially true
  for a pure generator), and BOTH the module and function docstrings state
  plainly that entry-reuse is not implemented and a real sweep costs 18 full
  passes.
  Confirmed invariant 10 intact: core/config.py's sole import is still
  `from dataclasses import dataclass`; SweepParams adds none.

### ALL 12 PLAN TASKS COMPLETE (1, 2, 3, 4, 5, 6, 7, 8, 9a, 9b, 10, 11a, 12)
Task 11b (the ADR 059 default-config RUN) is deliberately HELD pending explicit
user approval. It is the only plan item not built, and it is an execution step,
not code.

Remaining controller-added work before the final whole-branch review:
 1. O(n^2) run_universe fix — IN FLIGHT (agent ab95742e72bf72287)
 2. resolve_config wiring — QUEUED, dispatch after (1) lands

O(n^2) run_universe fix: complete (commit 5bf5aec, review clean — Approved)
  Both cache keys include as_of — (ticker, as_of, lookback_days) and
  ("sector_median", sector, as_of, lookback_days). The highest-value risk
  (omitting as_of, which would silently serve a prior quarter's value across
  calls) is proven absent by a dedicated test, not merely asserted.
  Cache is a local dict created inside run_universe and threaded by parameter —
  no module-level dict, no lru_cache, no mutable default arg. Goes out of scope
  on return, so a long-lived scheduler process cannot accumulate.
  `if key in cache`, not truthiness, so a cached None (and a legitimate 0.0)
  short-circuits correctly. Tested.
  Query-count test discriminates by hand-traceable arithmetic: unfixed code
  yields N*N + N rel-return queries (110 at N=10, 1640 at N=40); fixed yields
  exactly N. At N=620 that is ~385,000 -> ~620.
  cache=None default preserves every existing call site unchanged.
  *** USER'S UNIVERSE BACKFILL IS NOW UNBLOCKED AND ~620x CHEAPER. ***
  Deferred minors: _rel_return_756d's cache type hint is narrower than the
  shared dict it actually receives (cosmetic, strict-mypy only); _peer_return's
  `if cache is not None` branch is redundant in the non-test path.
  NOTED, NOT IMPLEMENTED: run_universe still discards _sector_median_return's
  used_fallback flag (always True today since tickers.sector is null everywhere).
  DESIGN §4.6 describes it as something to flag. Controller decision, deferred.

resolve_config wiring: dispatched (agent af7eeadfcce98996e), in flight.

## REVISED SEQUENCE (user, 2026-08-02)

User measured one quarter of `cscan universe` at under 1-2 minutes post-fix, so
all 66 quarters is ~1.5 hours. Confirms the memoization worked in practice.

User's restructuring: the branch contains ONLY session 9 (forked from main at
9777377), and sessions 0-8 were verified in a prior pass, so "whole-branch
review" and "the scope the sweep exercises" are the same thing. Steps 3 and 7 of
the controller's earlier 7-step plan are FOLDED into one final step.

Sequence now:
 1. O(n^2) fix .................................... DONE (5bf5aec, approved)
 2. resolve_config wiring + review ................ IN FLIGHT (af7eeadfcce98996e)
 3. User's universe backfill, 66 quarters ......... user-run, ~1.5 hr
 4. 11b — ADR 059 default-config run .............. HELD, needs user go-ahead
       report real config_hash off written rows, runtime, harness results
 5. Entry-reuse refactor decision ................. informed by 4's measurement
 6. Whole-branch review + full sweep .............. TOGETHER, final step

TRADEOFF ACCEPTED BY USER: 11b writes ~5M rows before the whole-branch review
runs. Bounded risk — rows are additive under a NEW config_hash, the old
generation is untouched, and a bad run is reversible with
DELETE WHERE config_hash = '<new>'. Every task has already passed an individual
task-scoped review; the whole-branch pass is for cross-task issues, and it will
be better informed for having seen a real run.

resolve_config wiring: fix round 1/5 (2 addressed + minors; commits 99612a4..324a6d3)
  nightly now resolves config first and threads params=config.indicators /
  sp=config.signals (names verified against compute.py:156-163 and 804-810).
  weekly/monthly confirmed to call nothing config-taking. Test asserts captured
  downstream args (bb_window==25, stoch_oversold==25.0), fails if reverted.
  poll's fail-closed contract documented + pinned by a test using CAPSCAN_COSTS,
  a section poll never reads. NOT special-cased out of the contract.
  The false "nightly has nothing to wire" claim was corrected visibly in place.
resolve_config wiring: fix round 2/5 dispatched — CONTROLLER ADJUDICATION of a
  finding the reviewer called "not blocking".
  Reviewer noted: config now resolves BEFORE scheduled_runs.record, so a
  malformed config produces a nightly attempt that is never recorded. ADR 080's
  catch-up tracking then cannot distinguish "nightly never fired" (Task Scheduler
  problem) from "nightly fired and died on config" (config problem) — both are
  simply absent from scheduled_runs.
  Reviewer framed this as IO-safety vs observability. IT IS NOT A BINARY:
  scheduled_runs.record takes no config and writes one row, so ordering it
  engine -> record -> resolve_config -> ingest keeps BOTH properties (attempt
  logged AND config fails before any ingest IO). Dispatched the reorder.

## USER ACTIONS IN FLIGHT (2026-08-02)

User is running the 66-quarter universe backfill NOW (2010Q1..2026Q2,
sequential, ~1.5 hr) via a PowerShell loop the controller supplied. Progress
prints per quarter, job output tees to universe_backfill.log, a failed quarter
is recorded and the loop continues. Reviews are read-only and do not interfere.
User confirmed: whole-branch review moves BACK to before 11b (reversing their
earlier fold of steps 3 and 7). Reviews run on sonnet for cost.

Sequence now:
 1. config wiring round 2 re-review ....... in flight
 2. whole-branch review (sonnet, scope 03242ae..HEAD = 24 commits, 27 files,
    8,640 insertions — session 9 only; the other 17 commits on the branch are
    session 8 handoff work the user already verified)
 3. user's backfill completes ............. in flight now
 4. 11b — needs explicit user go-ahead
 5. entry-reuse refactor decision, using 11b's measured single-pass time
 6. full sweep
resolve_config wiring: fix round 2/5 (1 addressed, 0 open; commits 324a6d3..5613eef)
  Order is now engine -> scheduled_runs.record -> resolve_config -> everything
  else, so the attempted run is logged AND config still gates every ingest and
  compute call. Test asserts record_calls == [("fake-engine","nightly")] and
  traps all six ingest.run_* — it would fail if the order were reverted, since
  reverting makes record_calls empty.
resolve_config wiring: complete (commits 5bf5aec..5613eef, review clean)

WHOLE-BRANCH REVIEW dispatched (sonnet, agent afd1f3b800a82aaba).
  Scope 03242ae..5613eef, 26 commits. The generated package was 415KB, too
  large for one read, so the controller built a SOURCE-ONLY diff at
  .superpowers/sdd/2026-08-01-session-9-backtest/whole-branch-source.diff
  (187KB, 10 source files, -U8) plus the full stat summary including tests.
  Test files excluded deliberately: all 17 were reviewed per-task, and this pass
  is for cross-task defects. Reviewer was pointed at the ledger's deferred and
  parked lines and required to triage them as a named output section.

## WHOLE-BRANCH REVIEW RESULT (sonnet, 03242ae..5613eef)

Verdict: needs fixes — ONE Important cross-task defect, no Criticals.
  Reviewer traced DESIGN §5.2's pipeline order by hand and confirmed each stage
  receives what the previous produced; verified the price-series split end to
  end (split-adjusted OHLC to entry/exit/MFE, adj_close only to fwd_ret_*d);
  cross-referenced BOTH column-ownership lists column-by-column against
  _EVENT_COLUMNS — no orphaned column, no genuine contest; found no
  undocumented second implementation (invariant 2 held). All 15 deferred minors
  triaged as genuinely deferrable.
IMPORTANT — run_events config drift (compute.py ~800, ~814):
  "split_key": _split_key(hit.ts, SplitParams())   <- hardcoded default
  chash = config_hash(Config(signals=sp))          <- all but signals defaulted
  run_events has no splits/Config param. PREDATES session 9, but session 9's own
  resolve_config wiring ACTIVATES it: the CLI now resolves a real Config and
  threads sp=config.signals, while run_backtest uses the FULL config for both
  config_hash(config) and split_key_for(..., config.splits).
  Set any non-signals override and (a) split_key drifts between the two writers,
  (b) config_hash DIVERGES — and since it is part of the natural key, the
  backtest's upsert cannot find run_events' row at all and silently INSERTs a
  disconnected duplicate. Dormant today (no config.toml, no CAPSCAN_*).
  Same shape as the Task 9b defect: two individually-correct pieces combining
  where no per-task review had both halves in view. SECOND instance this session.
MINOR: backtest.py's final sort key omits signal_type; pandas sort_values
  defaults to non-stable quicksort, so two rows differing only in signal_type
  have unreproducible relative order. ADR 060 risk, no DB-state impact.
Fix dispatched (agent ac677b2b10b944eb0, in flight).

## MARKET-CAP OUTLIER DEFECT (controller-added, found while checking universe data)

universe.mcap_usd max was $17.3 QUADRILLION (2010), $23 quadrillion (2016).
p99 was sane throughout (~$308B for 2010, correct), so: a few extreme outliers.
DIAGNOSED (read-only agent ace0752715900cdf6, report at
.superpowers/sdd/2026-08-01-session-9-backtest/mcap-outlier-diagnosis.md):
  43 rows / 24 tickers, all source='sec_xbrl'. Individual filings carry the real
  share count scaled by an extra x1,000 or x1,000,000 IN THE RAW SEC XBRL DATA,
  ingested verbatim with no plausibility check, then multiplied by a correct
  split-adjusted close. Ruled out: close, adr_adjusted_shares, events.mcap_usd.
  Confirmed corrupted: crit_mcap -> in_trade -> event eligibility.
CONTROLLER VERIFIED independently — ratios cluster at ~1.0-1.2 x 10^6:
  ORCL 1,177,581  WHR 1,127,562  MAR 1,114,240  EIX 1,112,822  STX 1,107,279
COUNTEREXAMPLE THE CONTROLLER FOUND, which the diagnosis missed: PSKY's median
  share count is 1,000 (itself absurd) and its "flagged" row of 1,071,666,977
  looks CORRECT. A median-based test rejects the good row and keeps the bad
  ones. So the 43-row set may be incomplete or partly wrong, and the guard needs
  an ABSOLUTE plausibility bound, not only a within-ticker relative one.
Fix dispatched (agent a7ce6051618040a38, in flight) — ingestion-side guard in
  run_shares: REJECT and log to bar_rejects, never divide by an inferred scale
  factor. Must accommodate a legitimate 10:1 split (NVDA 2024, AAPL 4:1 2020).
  Existing bad rows NOT cleaned up — that needs user approval + a universe
  re-backfill afterward.

## CPI DEFLATOR (user asked; fetched 2026-08-02)

Saved to .superpowers/sdd/2026-08-01-session-9-backtest/cpi-u-annual.md.
BLS and Minneapolis Fed both 403 automated requests; used a secondary source
reproducing the BLS CPI-U series. 2025 is a PARTIAL-year average; 2026 has no
annual average at all, so any "2026 dollars" anchor is an extrapolation.
*** KEY FINDING: CPI deflation does NOT explain the empty historical universe. ***
  Factors span only 0.677 (2010) to 1.000 (2025) — a 47% move across 16 years.
  In 2010, 23 tickers already cleared $100B nominal and 6 cleared $200B, yet
  in_trade was 0. The SMA-200 / slope / relative-return criteria zeroed it, not
  crit_mcap (is_tradeable requires all four). Deflating widens 2010's pool from
  6 to ~30, which helps at the margin but is not the cause.
  Equity market caps grew far faster than CPI, so an index-relative or
  rank-based threshold would move much more than 1.5x. Separate lever, likely
  the more effective one. Revisit AFTER the mcap guard lands and the numbers
  mean something.
Whole-branch fix: complete (commit a4ee776, re-review clean). 725 tests.
  run_events now takes config: Config | None and derives BOTH config_hash and
  split_key from it. Full Config chosen over SplitParams-only, correctly — a
  SplitParams-only fix repairs the split-key drift but leaves the hash
  divergence, which is the half that breaks the join.
  *** The agreement test uses a NON-DEFAULT config *** (stoch_oversold=25.0,
  train_end="2026-12-31") and drives BOTH jobs through their real public entry
  points, asserting they agree with each other AND with config_hash(override)
  directly. A default-config test would have passed with the bug fully intact,
  since defaults are exactly what the buggy code used. Reviewer confirmed, and a
  companion test asserts the new hash DIFFERS from the old buggy
  Config(signals=...)-only hash — direct proof the fix reads more than signals.
  Backward compat verified: sp-only and no-arg callers unchanged; the
  ValueError on conflicting sp+config is unreachable from any existing call
  site; split_key_for's raise-below-event_start preserved; _build_event_row's
  dict keys, _RUN_EVENTS_UPDATE_COLUMNS, and the upsert conflict columns all
  untouched.
  Sort key now ["ticker","signal_date","signal_type","entry_kind"], with a test
  feeding reverse-alphabetical signal_type that would fail under a
  kind="stable"-only fix.
  Controller verified config_hash(Config()) AND config_hash(resolve_config())
  both == 22df3117b890793b post-commit.

### BRANCH STATE: code-complete and reviewed except the shares guard (in flight,
### agent a7ce6051618040a38). 725 tests. HEAD a4ee776.

DECISIONS PENDING FROM USER (nothing further to dispatch until these):
 1. Clean the bad shares_outstanding rows — writes to production, needs
    approval. The guard prevents new ones; it does not touch stored rows.
 2. Universe re-backfill after (1) — ~11 min at the user's measured 10s/quarter.
 3. What in_trade should mean. CPI deflation is NOT the binding constraint
    (see the CPI section above): in 2010, 23 tickers cleared $100B nominal and
    6 cleared $200B, yet in_trade was 0 because the SMA/slope/rel-return
    criteria zeroed it. Options: inflation-adjusted threshold, index-relative
    share, rank-based top-N, or relax the four-criteria conjunction.
 4. 11b — the ADR 059 default-config run.
 5. The 18-config sweep, and whether to do the entry-reuse refactor first
    (decide using 11b's measured single-pass time).

## SHARES GUARD + CONFIG THRESHOLDS (controller-added, complete)

cc7e9fe  shares plausibility guard at ingestion (run_shares)
a1dacec  max_shares 32B->320B; min_mcap_usd 200e9->100e9; ADR 014 dated note
c5e588f  docstring accuracy fix (scope of the widened-band gap)

Guard: absolute bounds, REJECT not correct, logged to bar_rejects. Reviewer
  verified all four requirements, that run_shares is the sole writer of
  shares_outstanding, and that both real call sites (cli.py:191, cli.py:922)
  invoke it with the guard armed by default.
  min_shares = 1,000,000 (unchanged, floor is sound)
  max_shares = 320,000,000,000
Ceiling raised because 32B sat at 91% of Citigroup's LIVE 29.2B count, with TSM
  25.9B and NVDA 24.5B. A 2:1 split on any of those three real constituents
  would produce a GENUINE filing above the ceiling, rejected permanently and
  silently, with no fallback — once genuine data is outside the band every
  future filing is rejected and the ticker freezes at a stale count.
  Governing asymmetry: rejecting good data is worse than admitting bad data.
  Bad counts surface downstream as absurd market caps (that is how this whole
  defect was found); rejected good data is invisible.

*** MEASURED SCOPE of the widened band, verified by the controller against
    shares_outstanding_rejected_20260802: ***
  IN-BAND now accepted:  26 rows / 12 tickers
    AAP AIZ ALK CNX EOG FTNT GRMN MAA PKG PNR REG SWKS
  STILL REJECTED >320B:  33 rows / 23 tickers (ORCL AMD EXC TFC AEP ...)
  So the ceiling is NOT vestigial — it still catches x10^5/x10^6 corruption —
  but it no longer catches x1,000 errors on tens-of-millions-share companies.
  Documented in the core/config.py docstring, not only in the report.
  NOTE: the reviewer said 13 in-band tickers; the implementer independently
  measured 12 and used its own number; controller confirmed 12. The implementer
  was right to verify rather than copy.

CLEANUP DONE (user-approved): 135 rows deleted from shares_outstanding,
  backed up first to shares_outstanding_rejected_20260802 (with backed_up_at).
  0 remaining bad. 73,097 rows / 627 tickers remain. NO ticker lost all its
  data — every affected ticker has surviving good filings for _latest_shares
  to fall back to. The backup table is what let the reviewer measure the gap
  scope against real observed corruption instead of reasoning hypothetically.

*** NEW config_hash = 3e598c59e7d71eae  (was 22df3117b890793b) ***
  Moved by min_mcap_usd 200e9 -> 100e9. Correct per ADR 060 — a different
  universe threshold is genuinely a different config. Controller verified both
  config_hash(Config()) and config_hash(resolve_config()) return it.
  GUC: ALTER DATABASE capitalscan SET capitalscan.default_config_hash =
       '3e598c59e7d71eae';
ADR 014 conflicted ($200B stated as fixed) — resolved with a dated note in
  docs/DECISIONS.md following the file's existing convention, pinned decision
  text left untouched.

### SESSION STATE: 735 tests. HEAD c5e588f. Branch code-complete and fully
### reviewed. User is re-running the 66-quarter universe backfill now.

STILL PENDING USER:
 - 11b, the ADR 059 default-config run (needs explicit go-ahead)
 - entry-reuse refactor decision, using 11b's measured single-pass time
 - the 18-config sweep
KNOWN OPEN, not blocking: BRK-B 2009-2010 filings (1,103,764 / 1,056,884) sit
  just above min_shares but are implausible against BRK-B's contemporaneous
  price — a class-mismatch defect (Class A count filed under B) this guard does
  not close.

## 11b SMOKE TEST — HARNESS GATE FAILED (2026-08-02)

Ran `cscan backtest --tickers AAPL,MSFT --workers 1` as a smoke test BEFORE the
full run. It failed, which is exactly why the smoke test existed.
  config_hash 3e598c59e7d71eae, run_id backtest_20260802T151645_02d59fd4
  9,112 rows (2,278 signals x 4 entry kinds), 106.7s serial, exit 1
  PASS no_lookahead | FAIL entry_sanity (37) | PASS exit_sanity
  FAIL return_identity (3838) | FAIL non_overlap (1596)

*** TIMING DATAPOINT: ~53s per ticker serial. 615 tickers => ~9 hr serial,
    ~1.1 hr at 8 workers. The 18-config sweep at 18 full passes is therefore
    ~20 hours. This makes the entry-reuse refactor decision MATERIAL, not
    academic. Revisit once the gate passes. ***

Controller preliminary measurements (handed to the diagnosis agent to VERIFY,
not assume):
 - return_identity: max_abs_diff 9.88e-6, avg 4.6e-7, ZERO rows > 1e-4.
   entry_price/exit_price are numeric(12,4), gross_ret numeric(12,6), and the
   harness re-reads from the DB via cli.py _load_events_for_run rather than the
   in-memory frame. DESIGN §5.10 specifies 1e-9. Hypothesis: structurally
   unsatisfiable across a numeric(12,4) round-trip — the check can never pass on
   real data.
 - non_overlap: cluster tagging itself verified correct (AAPL long head
   2010-01-22, seq 2-5, new head 2010-02-01 = 6 trading bars later, and
   tag_clusters breaks on gap > max_hold_days=5). But a NEXT_OPEN position
   entered t+1 holds up to 5 forward bars, so its window reaches 02-01 — the bar
   the next head fires on. Hypothesis: off-by-one between the tagger's break
   rule and the harness's overlap window. Same CLASS as the (ticker,side) defect
   already fixed in this harness: a checker and the thing it checks disagreeing
   about a contract.
 - entry_sanity: NOT explained. Raw SQL (no slippage reversal) finds 128 rows
   outside [low, high]. next_open/touch max_rel is 3.05e-4 == slippage_bps 3.0
   exactly, and the harness reverses slippage, which is why only 37 survive.
   *** touch_5m max_rel is 0.0233 — 76x slippage, cannot be rounding. Possible
   REAL ENGINE BUG. *** An intraday price must sit inside the daily bar's range
   by definition, so something about the TOUCH_5M path is wrong.

Diagnosis agent dispatched (a50763393729ed534) with explicit instruction to
decide PER FAILURE whether the engine or the harness is wrong, and that a
tolerance must be DERIVED from storage precision or a stated contract, never
picked to make a failure go away.

11b is NOT complete. The full-universe run is blocked until the gate passes.

## HARNESS GATE DIAGNOSIS — RESOLVED (all three verified against live data)

 1. return_identity ... HARNESS wrong. 1e-9 vs numeric(12,4) round-trip is
    structurally unsatisfiable. Derived bound: 1e-4/entry_price + 5e-7
    (price storage +-5e-5 on each leg propagated through (exit-entry)/entry,
    plus gross_ret's own numeric(12,6) rounding). Covers the observed 9.88e-6
    max at the min entry price of $6.87.
 2. non_overlap .... HARNESS wrong. The check never deduped the 4 entry-kind
    rows per cluster head before the gap walk.
    *** CONTROLLER VERIFIED THE ARITHMETIC EXACTLY: ***
        head_rows_all_kinds 2128 | distinct_heads 532 | surplus 1596
    1596 == the violation count. 100% artifact, ZERO genuine overlaps.
    The controller's own entry-lag hypothesis (NEXT_OPEN entering t+1 extending
    into the next head's bar) was CHECKED AND RULED OUT by the diagnosis — the
    offset cancels between rows of the same entry kind. Not a defect.
 3. entry_sanity ... *** ENGINE BUG, REAL. *** TOUCH_5M only.
    On a gap day the touch level sits outside the whole session's range — a
    price that never traded — and the interpolation anchored on it, landing
    ~92% of the way toward it, outside the daily bar. Worst case 2.3% better
    than anything that traded, vs 0.03% for the slippage-explained kinds.
    All 37 survivors shared one signature (entry_gapped=true, touch_level
    outside the daily range): ONE defect, not several.
    This is the same trap DESIGN §5.4's gap rule already solves for TOUCH,
    never applied at hourly granularity.
 no_lookahead and exit_sanity PASS results remain trustworthy — neither path
 touches any of the three defects.

FIXED: TOUCH_5M engine bug — commit f6dd524 (core/returns.py + test_returns.py).
  Root cause confirmed as the ANCHOR, not _first_hourly_touch's bar selection:
  it now anchors on the hourly bar's `open` when that bar gapped through the
  level. TOUCH_30M unaffected (returns close before the changed branch).
  29/29 test_returns.py pass, 25 pre-existing unmodified.
IN FLIGHT: the two harness fixes (agent ac583b81303b6c623).

*** LESSON WORTH KEEPING: two of the three gate failures were the harness's own
bugs, but the third caught a real engine defect that silently invented entry
prices up to 2.3% better than anything that traded. Loosening all three
tolerances to make the gate green — the obvious move — would have shipped that
into every historical TOUCH_5M entry from 2024 onward. ***

## 11b FULL RUN — 2026-08-02

run_id backtest_20260802T153716_d86d21cf, config_hash 3e598c59e7d71eae
246,116 rows | 575/615 tickers | *** 2h41m45s at 8 workers *** | exit 1
  PASS no_lookahead | FAIL entry_sanity (7) | PASS exit_sanity
  PASS return_identity | PASS non_overlap

*** TIMING IS DECISIVE FOR THE SWEEP: 2h41m per pass x 18 passes = ~48 HOURS.
    The entry-reuse refactor (not implemented; run_backtest resolves entries
    per-config inside _backtest_one_ticker) is now clearly worth doing before
    the sweep. Revisit with the user. ***

Row count sanity: 246K over 575 tickers = ~428/ticker, vs 4,556/ticker for
AAPL+MSFT in the smoke test. That gap is the trade-universe filter finally
doing real work — most of the other tickers do not clear $100B. Compare
1.29M rows in the old generation where the filter was inert.

### entry_sanity's 7 violations — ENGINE IS CORRECT, HARNESS USES THE WRONG
### REFERENCE FRAME (controller traced end to end)

All 7 are TOUCH_5M/TOUCH_30M, which are priced from HOURLY bars by
construction, but entry_sanity compares every entry_price against the DAILY
bar's [low, high]. On most days the daily bar contains the hourly bars so it
happens to work; it breaks when the two feeds disagree.

PGR 2025-02-25 touch_5m short, entry_gapped=true:
  hourly 14:30  open 273.98  high 285.00  low 273.16  close 276.08
  daily         open 277.64  high 279.93  low 274.39  close 278.52
  stored entry_price = 274.0728
  273.98 + (276.08-273.98) x 5/60 = 274.155   (gap-anchored on the hourly open)
  274.155 x (1 - 0.0003)          = 274.0728  (short slippage) -- EXACT MATCH
The fill is INSIDE its hourly bar [273.16, 285.00]. It is outside the daily
bar only because Yahoo's hourly feed escapes the daily on both sides — a
285.00 hourly high against a 279.93 daily high is a bad tick.

This is the THIRD reference-frame/contract mismatch found in this harness
(after grouping by ticker where tag_clusters keys on (ticker,side), and
comparing DB-rounded values against a 1e-9 in-memory tolerance). Pattern
worth noting: a checker and the thing it checks disagreeing about a contract.

Fix dispatched (agent a7c1717b6ab8c0466): validate TOUCH_5M/TOUCH_30M against
the hourly bar they were priced from, reusing core.returns._first_hourly_touch
rather than duplicating the selection. Also asked to QUANTIFY the underlying
hourly-vs-daily inconsistency (not fix it) so the controller can decide whether
it warrants a DESIGN §2.3 validation rule.

11b is NOT yet accepted. Re-run required after the harness fix.

entry_sanity reference-frame fix: commit b1d78b5, 746 tests. TOUCH_5M/TOUCH_30M
  now validate against the HOURLY bar they were priced from, reusing
  core.returns._first_hourly_touch rather than duplicating the selection.
  Smoke-tested against the ACTUAL failing tickers (PGR, LIN) rather than
  AAPL/MSFT — 5/5 PASS, exit 0, 81s. 11b re-run started.

## *** NEW DATA DEFECT FOUND: hourly and daily bars disagree on SPLIT
## *** ADJUSTMENT for 17 tickers. Not blocking 11b. Needs an ingest fix.

The harness fix's quantification found 50,239 of 297,790 (ticker, day) pairs
(16.9%) where the hourly range escapes the daily range. 45,815 of those are
tick-level noise (median ~$0.005). But 4,424 pairs across 17 tickers are
SCALE mismatches. Controller verified the ratios are EXACT SPLIT FACTORS:

  KLAC   9.999 ~ 10:1      BKNG  24.997 ~ 25:1
  CRWD   4.000 =  4:1      CVNA   5.000 =  5:1
  TPL    3.000 =  3:1      NOW    5.000 =  5:1
  AMCR   0.200 =  1/5      BNY    0.115 ~  1/8.7

So for these tickers the two intervals are on different split-adjustment
bases — one back-adjusted, one not. Any TOUCH_5M/TOUCH_30M fill for them
would be off by the split factor (BKNG: 25x).

BLAST RADIUS TODAY: **ZERO events affected.** Controller measured it —
0 events with a priced touch_5m/touch_30m entry on any mismatched
(ticker, day). Those tickers DO have events (BKNG 152, KLAC 120, TPL 108,
CVNA 92, BNY 132, AMCR 40; CRWD and NOW have none) but none carry a priced
hourly-derived entry on an affected day.

*** THIS IS ZERO BY COINCIDENCE, NOT BY DESIGN. *** Nothing prevents the
conditions from coinciding — a universe-threshold change, one of these
tickers growing past $100B, or a signal firing on a different date would
make the bad data live. Treat as a latent correctness defect, not a
non-issue.

Recommended fix (NOT dispatched, needs user decision): refetch hourly bars
for the 17 tickers, and add a DESIGN §2.3 validation rule rejecting an
hourly bar whose range escapes its containing daily bar by more than a
tick-noise tolerance. The rule is the durable part — a refetch alone would
silently regress on the next split.

## *** 11b RUN 2 — PHASE 3 HARNESS GATE PASSED (2026-08-02) ***

run_id backtest_20260802T183304_6b1c5b52, config_hash 3e598c59e7d71eae
246,116 rows | 575/615 tickers | 2h48m17s at 8 workers | exit 0
  PASS no_lookahead | PASS entry_sanity | PASS exit_sanity
  PASS return_identity | PASS non_overlap

Timing breakdown (measured): write phase ~20 min, harness ~2h28m. THE HARNESS
IS THE BOTTLENECK, and it is SINGLE-THREADED — more workers do not help it.
Cause: no_lookahead re-runs scan_candidates 5x over all 575 tickers (shifts
1/2/5/20 + a shuffled control), so validation does ~5 detection passes where
the backtest does 1. Run 2's harness also loads hourly bars now (the
entry_sanity reference-frame fix), which is why it ran past run 1's 2h21m.
If this needs to be faster, the lever is sampling tickers for the shift ladder
or parallelizing the harness — NOT raising --workers.

Split distribution:
  split_key | rows   | tickers | priced | exited | ambiguous | range
  train     | 156848 |     564 |  61535 |  61535 | 15 | 2010-01-05..2021-12-31
  validate  |  21672 |      69 |   8418 |   8418 |  0 | 2022-01-03..2023-12-29
  holdout   |  67596 |     124 |  41001 |  40941 | 13 | 2024-01-02..2026-07-31

*** AMBIGUITY RATE: 28 of 110,954 priced rows = 0.025%, FAR under the Phase 3
gate's 10% threshold. No hourly escalation needed. ***

NOTE for the RESULTS.md write-up: validate has only 69 tickers vs train's 564
and holdout's 124. Worth explaining before Phase 4 — likely the trade-universe
filter interacting with 2022-2023 (a drawdown period, so crit_above_sma200 and
crit_sma200_slope would fail broadly). Not a defect on its face, but a thin
validation set weakens the validate-vs-train edge comparison ADR 033's kill
criteria depend on.

## *** PHASE 3 GATE: ALL FIVE CRITERIA PASS (2026-08-02) ***

 1. Exit invariants, 10,000 property cases ... PASS (full profile confirmed
    registered in conftest.py — 10,000 examples each across 5 exit_invariant
    tests, 420s. NOT the 100/200 default.)
 2. Ambiguity rate < 10% ..................... PASS (28/110,954 = 0.025%)
 3. Event rate, BUILD §9a three checks ....... PASS (structural invariants hold;
    component rates hold direction; confluence 18.34% all-ticker / 19.07%
    in-trade-only, both inside the 10-25% band and essentially unchanged from
    the 2026-08-01 baseline despite every data repair since)
 4. Two runs identical ignoring run_id ....... PASS (confirmed TWICE
    independently: controller's own script over AAPL/MSFT/PGR = 10,056 rows,
    62 columns, zero differing cells; agent's over AAPL/HD/JPM/MSFT/UNH =
    22,168 rows, same result. Explicit today=2030-01-01 matched the default
    per-ticker derivation exactly, confirming no wall-clock leak. No DB writes
    — db_io.upsert monkeypatched to capture.)
 5. All five validation-harness checks ....... PASS
Run of record: backtest_20260802T183304_6b1c5b52, config_hash 3e598c59e7d71eae,
246,116 rows, 575/615 tickers, 2h48m17s at 8 workers.

FLAGGED, not disqualifying: +2.2-2.7pp drift on raw band-touch marginals vs the
2026-08-01 baseline, unexplained. Worth a look before Phase 4 conditions on
those rates.

## Stale event generations DELETED (user-approved)
1,292,395 rows removed (edf5658f5da3807a 1,292,276 + 39e6a590aa799780 119).
Backed up first to events_stale_gen_20260802 with backed_up_at. events now
holds exactly one generation: 3e598c59e7d71eae, 246,116 rows.
scan() verified: 27 rows for 27 signals on 2026-07-31, one row per event
per ADR 049. Was 148.

## USER'S REMAINING COMMANDS (given 2026-08-02)
 1. uv run cscan actions --tickers BNY
 2. uv run cscan bars --hourly --backfill --tickers AMCR,ANET,BKNG,BNY,CRWD,
    CVNA,DD,ETR,FAST,IBKR,KLAC,NFLX,NOW,ORLY,PANW,TPL,TSCO      (~7 min)
 3. uv run cscan backtest --workers 8                            (~2h50m)
NO universe re-run needed — verified run_universe reads only daily bars
(interval='1d' in _latest_indicator_row, _rel_return_756d, _adv_20d) and
shares_outstanding. Hourly does not touch it.
NOTE: cscan actions accepts --lookback but never passes it to run_actions
(cli.py) — minor CLI bug, not blocking.

## Doc work dispatched (agent ade4fde88a70968c4)
RESULTS.md (BUILD §7.3 write-up + gate result + honest caveats), BUILD.md
(session 9 complete, session 10 added), session10.md (5 corrections incl. the
label-grid explosion that makes materialized columns untenable once horizons
multiply thresholds).

## Post-gate work (2026-08-02/03)

1136459  operational safety rules moved into CLAUDE.md ("Before running
         anything", placed BEFORE the invariants) + a TESTS.md §2 warning.
         The testpaths/TRUNCATE landmine had been in NO durable doc — only in
         reports/ and a gitignored scratch file. Fresh agents would not have
         seen it. Also records measured job costs so nobody starts a 5-hour
         backfill blind.
528ca90  git_sha() reads .git/HEAD directly instead of shelling out.
         *** INVARIANT 6 VIOLATION, now fixed. *** `git` is not on PowerShell's
         PATH, so subprocess raised FileNotFoundError, the handler returned
         'unknown', and EVERY job run from PowerShell lost provenance —
         backtest was 5/5 unpopulated. Docstring claimed 'unknown' meant
         "outside a git checkout"; the real trigger was a missing binary.
         Agent added commondir handling for worktrees beyond the brief.
aacee77  18-config sweep WIRED. sweep_configs existed and was exported but
         nothing called it; --sweep only checked the ADR 059 gate and exited.
         Per-config checkpointing + resume. Command: cscan backtest --sweep
         --workers 8, ~6 hours (18 x ~20 min write phase at 8 workers).
acd1ea8  session10 path window PINNED at 10 trading days, derived from
         max(StatsParams.fwd_ret_horizons) — an existing field, so config_hash
         is unaffected. The doc had said "full evaluation window" without a
         number; an implementer would naturally have used max_hold_days=5,
         built a 5-day table that passed every acceptance criterion, and made
         day-6-to-10 labels permanently underivable.
87ef410  ADR 093 "Terminal quantiles expand to five horizons", status
         Provisional (not Pinned — ADR 033's kill criteria could retire the
         model surface first). 11 heads -> ~31. Agent CAUGHT TWO ERRORS in the
         controller's brief: ADR 072 is "Shared handler layer", not
         calibration (correct cite is ADR 067 + DESIGN §7.6); and "roughly
         triple" is 2.8x, not 3x.

## Targeted 17-ticker backtest — PASSED (user-run)
run_id backtest_20260803T004905_4de2686b, 2,920 rows, 15/17 tickers, 5/5 harness
PASS. Verified in data: 332 hourly-priced touch_5m/touch_30m entries where there
were ZERO before (the split fix landing), and cofire_count NOT nulled
(full_universe=False guard preserving universe-wide values as designed).

## *** OPEN: hourly refetch rejected 4,425 bars — TWO DISTINCT CAUSES ***

`cscan bars --hourly --backfill` over the 17: 53,786 written, 4,435 rejected.
4,425 are the new hourly_daily_range_escape guard, tolerance 0.5 (a 50% band,
so NOT firing on tick noise — every one is a genuine escape).

Back-adjustment DID work: KLAC/BKNG/NFLX avg hourly/daily ratio went from
10-25x to ~1.02.

CAUSE 1 — BNY, 2,545 of 4,425 rejects (58%) across 377 days. NOT a split
  problem: its only splits are 1983-2007, none in the 2024-2026 hourly window,
  so back-adjustment correctly does nothing. But hourly sits at ~$11 while
  daily is ~$95. That is the SYMBOL-REUSE signature the handoff documented for
  FB/PCLN/PCS/Q. BNY moved from ticker BK recently; Yahoo's hourly endpoint is
  likely returning a different security that held the ticker before.
  `cscan actions --tickers BNY` upserted 163 rows but no in-window split,
  because there is none to find.
CAUSE 2 — ~1,880 rejects across 15 tickers, 4 to 41 days each (ANET 41, DD 40,
  NFLX 33). Too many for a pure ex-date off-by-one. UNDIAGNOSED.

CONSEQUENCE: rejected bars are not written, so those ticker-days have NO hourly
data and TOUCH_5M/TOUCH_30M return NaN there. Honest per invariant 4, but it is
coverage loss. The user's sweep is running against this state — not invalidated,
those entries are null rather than wrong.

## SESSION 9 CLOSE-OUT (2026-08-03)

d33aaa5  double-adjustment fix. The original hourly back-adjustment assumed
         Yahoo never adjusts hourly; it actually PRE-ADJUSTS a 1-41 session
         window before each ex-date, and the code divided those again.
         Controller verified independently: ANET split ex_date 2024-12-04,
         rejected days 2024-10-07..2024-12-03 — a contiguous block ending
         exactly one day before the split, discrepancies at exact split
         multiples (ANET 4.0004x, NFLX 10.0012x).
         *** Controller sent the first attempt BACK: its detection asked "is
         hourly/daily within 0.5 of 1.0?", which is a single-hypothesis test
         for a two-hypothesis question. 11 of 33 splits in the hourly window
         have ratios in [0.667, 1.5], where "already adjusted" (~1.0) and
         "not adjusted" (~factor) are indistinguishable under that band.
         Final version compares against BOTH hypotheses and takes the nearer
         if decisive by > 0.10; otherwise DROPS the day and logs
         `hourly_split_adjustment_unresolved` rather than guessing — because a
         wrong guess in that band errs by ~|F-1| (20% at ratio 1.2), too small
         for the 0.5 range-escape guard to catch, so it would silently
         overwrite good data. A visible reject beats an invisible overwrite. ***

REFETCH RESULT: hourly_daily_range_escape 4,425 -> 154. FAST 30->1 bad days,
ETR 6->1. Residual: DD 22 days (a 2.39-ratio action 2025-11-03 the detection
could not resolve) and exactly 1 day each on 14 tickers (the ex-date boundary).

*** BLAST RADIUS MEASURED — the bad hourly data reaches ZERO event rows where
it mattered: ***
  BNY  2,376 events, 0 hourly-priced   (378 bad days, no propagation)
  DD   4,824 events, 0 hourly-priced   (22 bad days, no propagation)
  ANET 7,416 events, 2,196 hourly-priced  (1 bad day of 498)
  IBKR 8,640 events, 2,124 hourly-priced  (1 bad day of 498)
Only ANET and IBKR clear in_trade at all (4 and 3 quarters). So pruning BNY was
HYGIENE, not a correctness fix.

BNY HOURLY PRUNED (user-approved): 2,888 rows deleted, backed up to
bars_bny_hourly_bad_20260803. Its 5,233 DAILY bars are intact — the daily
series was verified sound (normal 10.6x range ratio, no discontinuity), and
symbol reuse was investigated and REFUTED. The defect is hourly-only.

*** SWEEP COMPLETE: 18 config hashes, 4,430,088 rows in events. ***
That was the last outstanding BUILD §9 build item.

*** ADR 059 HAND-INSPECTION: COMPLETED BY THE USER, 2026-08-03. *** ~20 events
inspected against charts; user reported they looked correct. The gate condition
is met. Being recorded in RESULTS.md.

REMAINING: RESULTS.md sweep write-up (agent a334ee8437f3532fd in flight).
After that, Session 9 is closed.
