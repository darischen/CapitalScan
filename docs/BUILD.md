# BUILD.md

Implementation plan for `CapitalScan`.

Read `DECISIONS.md` and `DESIGN.md` first. This document says **what to build in what order**; those say **why** and **what it is**.

Every task points at an ADR or a DESIGN section rather than restating the rule. One source of truth, no drift.

---

## 0. How this is organized

Work is grouped into **sessions**. A session is one coherent goal that fits in a single Claude Code context window and ends with a passing test. Session boundaries follow dependency seams, which is what keeps context switching low.

Sessions 0-7 deliver v1 (Phase 1). Sessions 8-9 deliver Phases 2-3. Session 10 is a data-layer session between the Phase 3 gate and Phase 4. Later phases are sketched at the end and get their own session plan once Phase 4 results exist.

| # | Session | Delivers | Exit test |
|---|---|---|---|
| 0 | Scaffold | Repo, tooling, Docker, CI, CLI skeleton | `cscan --help`; `pytest` passes on zero tests |
| 1 | Schema | All migrations, views, Alembic wired | `cscan db migrate` up and down on empty DB |
| 2 | Core: indicators | `config`, `types`, `indicators` + registry | Golden fixtures + external reference match |
| 3 | Core: signals & exits | `signals`, `exits`, `returns`, `costs`, `universe` | Property, parity, and look-ahead tests |
| 4 | Fetchers | `jobs/fetch/*` with retry, rate limit, cache | Integration test, 3 live tickers |
| 5 | Ingest jobs | bars, actions, market, shares, earnings, validation | `cscan backfill --tickers TSM,NVDA,AAPL --start 2020` |
| 6 | Compute jobs | indicators, universe, events, `scan()` | **Phase 1 gate** |
| 7 | Full backfill | Run it, inspect, fix | 750 tickers ingested, validation clean |
| 8 | Poller & notify | `poll`, Notifier, positions, call overlay | **Phase 2 gate** |
| 9 | Backtest engine | `research/backtest.py`, exit sweep | **Phase 3 gate — complete, passed** |
| 10 | Forward path store and derived label layer | Path table, reconciled label layer, new label families | Reconciliation against Session 9 labels passes with zero unexplained differences; tests and documentation complete |
| 11 | Statistical primitives and self-validation | Wilson CI, standard error on n_eff, Benjamini-Hochberg, baselines, n_eff correction, `rho_era`, self-validation gate | **Session 11 gate — complete, passed.** Null test 2 of 480 cells at `q < 0.05` = 0.42%; recovery test 0.039 pp; broken variant caught at 11.67%. This is the session gate, **not** the Phase 4 gate — that one is in `TESTS.md` §10 and needs four more things Sessions 12 and 13 build. See `RESULTS.md` Phase 4 — Statistics layer |

| 12 | Cell grid and `cell_stats` | Twelve-cell grid (ADR 102), `cell_key` parity, `cell_stats` writer, era/breadth/concentration reporting, `arm` migration, `v_screen` predicates | **Session 12 gate — complete, passed on all 10 items.** Twelve cells enumerated, ten rendered, two suppressed at `n_eff` 14.3 and 5.0 exactly as ADR 102 predicts; measured `n_eff` reproduces the ADR 102 table within ±2. **No cell survives FDR: minimum q-value 0.769 across the 48-test family.** See `RESULTS.md` Session 12 |

| 13 | Benchmark arms | Buy-and-hold, signal, 200-replication random-entry null, trim-and-redeploy, four DCA variants, pre/post-tax and wash-sale flagging, ADR 099's high-breadth subset | **Session 13 gate — complete, passed on all 10 items.** Eight arms on one universe and one date range; the null holds exactly 200 rows with `replication` 1-200 and reproduces identically within a `config_hash`. **The signal arm is below the null's 97.5th percentile on both train and validate, and below buy-and-hold on both.** See `RESULTS.md` Session 13 |

| 14 | Closing Phase 4 | Equity-curve export, three-arm chart, ADR 015's drawdown slice, DESIGN 6.12's volatility-scaled ladder, ADR 092's matcher replacement | **Session 14 gate — complete, passed on all 8 items. Phase 4 gate closed on all five criteria.** Eight artifacts under `reports/phase4/`, all deterministic. **Every drawdown-slice interval crosses zero**, so ADR 015's central claim is not supported at this sample. See `RESULTS.md` Session 14 |

| 15 | Handlers, tools, and the response validator | `handlers/` layer, seven tools, typed results, invariant-8 validator, closed enums, ADR 095's `v_positions` rebuild | **Session 15 gate — complete, passed on all 10 items.** Opens Phase 5. Seven handlers, one contract, no HTTP or display imports. **No probability leaves without `n_eff`, an interval, and a q-value**, enforced by the validator and by a structural test on the annotations. `split='holdout'` raises on every handler that takes one. `predict` returns `NotFound` for every input. `v_positions` reads `serving_config` instead of literals (ADR 115); five defects fixed, one of them not in ADR 095. See `RESULTS.md` Session 15 |

| 16 | MCP server | Seven tools over streamable HTTP, bearer auth, per-token rate limiting, generated schemas, read-only database role | **Session 16 gate — complete, passed on all 10 items.** Verified end to end against the live database on 2026-08-18: unauthenticated `tools/list` refused, `initialize` and all seven tools answered, `get_stats` returned a real suppressed cell. **No `mcp/` module imports `sqlalchemy` or `db_io`**, and no enum value is spelled as a literal anywhere under it. The read-only role is proven by running each write verb through its own connection. See `RESULTS.md` Session 16 and `MCP_SETUP.md` |

| 17 | Screener and ticker page | `/` and `/ticker/[sym]` in Next 15, `lightweight-charts` price/volume/stochastic panes, the five states, ADR 120's `v_chart` rewrite | **Session 17 gate — 10 of 12 items passed; the two that did not are recorded as findings, not as debt.** Both routes render against the live database: the ticker page at **43 ms median**, the screener at 256 ms after one query rewrite took 871 ms to 265 ms. **`v_chart` was returning 963 rows for 275 sessions** — 22 config hashes on an unfiltered join, plus 116 dates that carry both a long and a short head (ADR 120). Four more defects found by running it, including every chart date landing one session early. 145 web tests, up from 15. See `RESULTS.md` Session 17 |

Sessions 0-2 can run back to back in one sitting. Session 7 is mostly waiting.

**Sessions 17 and 18 were stopped on 2026-08-18 and unblocked the same day by ADR 118.**
The session plan said web routes call the Python handlers; ADR 070, 072-as-refined-by-076,
and DESIGN all said TypeScript routes select from the views. CLAUDE.md says stop and ask
rather than work around, so nothing was built until it was decided.

**MCP is for LLM callers, not for deterministic retrieval.** `/`, `/ticker`, and
`/research` select from the views; `/chat`, Claude Code, and Claude Desktop go through the
MCP server to the handlers. ADR 070 and ADR 076 stand as written; the session plan was
what needed correcting.

**Session 17 is complete, 2026-08-19**, and the night after it produced
five more ADRs from *using* the pages. **Four migrations**, not one:
`c4a7e91b53d8` (v_chart, ADR 120), `f2d16b47c093` (events.in_trade,
ADR 122), `a7c519d3e8b4` (live reversal, ADR 123), `c8e2f60a4b17` (cluster
filter out of the view, ADR 124). `db/schema.sql` regenerated. One
dependency, `lightweight-charts` 5.2. `/research` and `/chat` remain
Session 18's.

**A full `cscan events` rebuild ran under ADR 122**: 178 minutes,
**1,312,935 rows**, and every measured number unchanged — `cell_stats`
digest, cell count and `v_screen` all identical to the pre-change baseline.
That comparison is what made the change safe to keep, and it is the only
thing that would have caught a missed consumer.

**ADR 125 came from CI, not from a developer machine.** Migrations imported
DDL constants from `jobs/views.py`; ADR 122 changed a view and four earlier
migrations began emitting SQL for a column that does not exist at their
point in the chain. Every from-scratch replay failed. A developer applies
only the new migrations, so the path is never taken locally.

**Before touching a migration, replay the chain.** Create a scratch
database, `alembic upgrade head`, and compare an md5 over `pg_get_viewdef`
for every view against the live database. Nothing about reading a migration
establishes that it reproduces the schema.

What unblocked it, kept because the correction is the useful part:

**Session 17 was unblocked.** A second blocker was claimed and was not real: the
`frontend-design` skill is installed and enabled as a *plugin* skill, under
`~/.claude/plugins/` rather than `~/.claude/skills/`. Reading it then showed that
CLAUDE.md and `DESIGN.md` §11.7 both described it wrongly — it carries general design
guidance, not CapitalScan tokens. The constraints were always in `DESIGN.md` §11.6-11.9.

**Session 16 is complete.** No migration. One dependency, `mcp>=1.2.0` (resolved to
2.0.0), which brings `starlette` and `uvicorn`.

The server adds no query logic, and that is asserted rather than intended: a tool making
two handler calls, or any statement-level control flow after the handler call, fails
`test_mcp_contract.py`. Tool schemas are generated from `handlers.enums`, so adding a
`SignalType` member changes the wire contract with no edit under `mcp/` -- pinned by a
test that no signal type, drawdown label, or split name appears as a string literal in
that package's code at all.

One defect found by running it. `build_app` mounted the transport inside another
`Starlette`, which never forwards lifespan events to a sub-app. The result authenticated
correctly, accepted the request, and failed every `initialize` with `RuntimeError: Task
group is not initialized` -- a message that reads like an SDK bug. No unit test in this
repository would have caught it, which is why `test_mcp_server_live.py` drives the real
protocol through a `TestClient` context manager.

Two operational notes. `cscan db grant-readonly` provisions the read-only role and is
idempotent; a deployment behind a domain must pass `allowed_hosts` or every request
returns `421 Misdirected Request`. Both are in `MCP_SETUP.md`.

**Session 15 is complete. Phase 5 is open.**

One migration, `d7f4b91c26ea`: `serving_config` plus a rebuilt `v_positions`. Applied
to research on 2026-08-18 and `db/schema.sql` regenerated. Run `cscan db sync-config`
after any change to `ExitParams`, and after migrating a database whose row predates a
new field.

The session has no measurements, and that is the honest description of it: it builds
the layer three surfaces will call and returns nothing new about the market. What it
does carry is a shape decided by ADR 112. `Suppressed` is the common return of
`get_stats` rather than an edge case, `survives_fdr` is False on every cell that comes
back, and the screener's default view (ADR 114) shows no statistical columns at all.

Two defects found by running the tests rather than by reading:

1. `explain_signal` validated `split` only inside a conditional branch, so
   `split='holdout'` passed through whenever `target_pct` was omitted.
2. `v_positions.days_held` counted calendar days while `max_hold_days` counts bars, so
   `exit_signal_timeout` fired a session early over every weekend. Not a defect ADR 095
   named; it surfaced while writing the parity fixture.

And one hazard worth knowing before Session 17: **`v_ticker_state` takes 26.5 s to
materialize** on the developer database, and every read of `v_positions` pays it.

**Superseded by ADR 116, 2026-08-18, and half of it was wrong.** The 26.5 s was
the whole view; a single-ticker read was already 17 ms, because Postgres pushes a
*constant* predicate down through `DISTINCT ON`. Only a *correlated* one -
`v_positions` joining on `p.ticker` - paid the full cost. ADR 116 rewrote the view
as a loose index scan: the whole view is now 27 ms and `v_positions` 23.5 ms.

**Session 14 is complete, and Phase 4 is closed.** All five Phase 4 gate criteria in
`TESTS.md` §10 now pass, the last two closed by 14.2's chart and 14.3's drawdown slice.
No migration.

What the gate does not say is as important as what it does. It asserts the machinery is
complete and correct, not that the strategy works. It passes alongside a minimum q-value
of 0.790 across 56 tests, a signal arm below its own randomization null on both splits,
and every drawdown-slice interval spanning zero. ADR 033 fixed the kill criteria in
advance precisely so a negative result stays reportable rather than becoming a reason to
move the bar.

Four findings carry into Phase 5:

1. **ADR 015's central claim is unsupported at this sample.** Eleven rendered intervals,
   none excluding zero. Its second half is untestable as stated: "turns negative past 35%"
   needs the 20-35 and 35+ buckets, which ADR 101 suppressed permanently after measuring
   them below the floor.
2. **The long side runs opposite to "stable."** All three long signals show a larger point
   estimate in 10-20 than 0-10, which would invert "trade shallow dips, stand down on deep
   ones" if it were real. It is not real at this sample — every interval spans zero and
   `n_eff` in 10-20 runs 39 to 73 against 147 to 719 in 0-10. Recorded because a reader
   looking only at point estimates reaches the opposite conclusion from the right one.
3. **ADR 095's `v_positions` defect is still live**, now detected rather than invisible.
   ADR 092's replacement matcher catches both spellings; ADR 095 defers the fix to Phase 5,
   which owns the serving layer.
4. **Three defects were found by running things rather than reading them**, each with a
   passing test suite at the moment it was wrong: a `peak_ret_5d` column NULL across all
   627,380 rows, an end-to-end path that raised a `KeyError` while fifteen unit tests
   passed, and two null-guard crashes that surfaced only under whole-repo mypy.

**Session 13 is complete.** Tasks 13.1-13.7 all closed, all ten gate items pass. No
migration: `benchmarks` was already in `db/schema.sql` and had never been written to.
Rollback is `DELETE FROM benchmarks WHERE run_id = '...'`.

Runs of record: `benchmarks_20260813T172347_f421521b` (train) and
`benchmarks_20260813T173059_efceeadc` (validate), both `config_hash = 1835688bf7d760ba`,
409 rows each, ~6m15s per split. ADR 032 was amended mid-session to add a long-term rate,
so these supersede the earlier pair; the pre-tax numbers are identical either way.

Four findings carry into Session 14:

1. **The signal arm loses to both benchmarks on both splits.** Train: +108.37% against
   buy-and-hold's +383.66% and a null 97.5th percentile of +205.77%. Validate: −10.10%
   against −3.69% and +3.02%. It clears the null's *median* on train (+108% against +84%)
   and not on validate, so entry timing is marginally better than random at best. This is
   the second independent measurement pointing the same way as Session 12's minimum
   q-value of 0.769.
2. **Short-term tax removes the entire pre-tax return.** The signal arm's +108.37% becomes
   −7.78% after 7,163 short-term round trips at 37%. The randomization null shows the same
   pattern, so it is a property of the turnover, not of the signal. ADR 032 was amended
   2026-08-13 to add a 20% long-term rate above a 365-day holding period, which moves the
   benchmarks only: buy-and-hold's post-tax return rises from +239.22% to +291.40% and the
   signal arm is unchanged, widening the post-tax gap to 299 points.
3. **The edge concentrates at high breadth**, DESIGN §6.11's market-timing reading. The
   subset arm's capital efficiency is 3.762 against the pooled 1.166 on a third of the
   deployment — and it is still below its own subset null (+126.04% against +248.70%).
4. **Three comparability defects were found and fixed by measurement, not by review.** The
   trim arm initially held a different book than buy-and-hold (+725% against +413%,
   entirely artifact); the tax model treated wash-sale losses as permanently disallowed
   (`post_tax_ret = −354%` against `pre_tax_ret = +109%`); and it then taxed multi-year
   benchmark holds at the short-term rate, understating buy-and-hold by 52 points in the
   signal arm's favor. All three passed their original unit tests. `RESULTS.md` records
   each, and each now has a regression test that would have caught it.

**ADR 108 — the close-confirmed reversal signal (2026-08-13).** Added between
Session 13 and Session 14, at the user's request, not a numbered session.

`open > close AND close >= bb_upper[t-1]`, short side only. `config_hash` moves
`1835688bf7d760ba` -> `697f3ae71428d392`. Migration `a9d3c04f7b15` adds
`indicators.bear_close_above_upper` (boolean, nullable). Grid goes twelve cells to
fourteen; the Benjamini-Hochberg family goes 48 tests to 56.

Measured: 43,701 flagged bars across 612 tickers, 20,796 event rows under the new hash.
The new type draws from both `confluence_high` (-16,015) and `bb_upper_touch` (-4,589),
reconciling to within 192 rows — bars where the reversal fired without a stochastic
extreme were previously plain band touches. `1835688bf7d760ba` is untouched, so every
Session 12 and 13 number stays reproducible.

Four defects surfaced, and **three were found by running things rather than reading
them**, each with a passing test suite at the moment it was wrong:

1. **`config_hash` never moved.** ADR 108 asserted it would; it does not, because
   `config_hash` hashes `Config` fields and a `SignalType` enum member is not one. Caught
   by printing the hash before launching the backtest. The run would have rewritten all
   626,977 events in place and silently broken Sessions 12/13's reproducibility. Fixed by
   `SignalParams.enabled_signal_types`, which also doubles as DESIGN §3.10's ablation
   switch.
2. **`--confluence-only` would have hidden its own target rows.** It filtered on
   `signal_type`, which carries only the most specific type, and the new type outranks
   `confluence_high`. Both scan filters now read `signal_types_all`.
3. **A grid guard rejected a valid cell for its spelling.**
   `test_signal_type_pairs_with_side_rather_than_crossing_it` asserted the short side via
   `endswith("_high")`, which `bear_close_above_upper` fails despite being correctly
   short-side. Rewritten to assert membership plus disjointness.
4. **`run_cell_stats` had no CLI entry point** and no caller outside its own tests, so
   Session 12's published table could not be reproduced from the CLI. Added
   `cscan stats cells`.

Two of my own errors are recorded rather than quietly corrected: a verification query that
compared row *t*'s band instead of *t-1*'s and appeared to show violations that were the
query's fault, and a completion monitor that read "`tasklist`: command not found" as "no
process running" and declared a still-running backtest finished.

**ADR 109 — the close-confirmed band is the same day's (2026-08-14).** Amends ADR 108
one day later, at the user's request, after checking fired tickers against a chart.

`open > close AND close >= bb_upper[t]`, dropping the `[t-1]` shift. `config_hash` moves
`697f3ae71428d392` -> `541f84a384b07ba2` via the new `IndicatorParams.bear_close_band_lag`
(default 0; set it to 1 to reconstruct ADR 108's population as `3f9b74da68e4573e`). No
migration: the column already exists and only its computed value changes. `max_warmup()`
returns to 272. Grid and FDR family are unchanged at fourteen cells and 56 tests.

Measured over 2026-08-03 to 2026-08-14: the bear flag falls 151 -> 74 bars, and 109 -> 53
once `confluence_high` is also required, dropping 56 and gaining 0. Nine of nine
user-checked tickers now agree with the charted band; NTRS, STT, DELL, NTAP, CAH, and
PANW all leave the scan. Roughly 55% of the old rule's extra fires never cleared the band
as drawn, because a gap-up day that fades raises its own band above its close.

The defect ADR 108's own review missed, in the same class as defect 1 above: changing the
formula did not move `config_hash`, because a formula is not a `Config` field. Third
occurrence, after `stoch_source` and `enabled_signal_types`. `bear_close_band_lag` exists
for that reason and for nothing else.

Two display sites still read `k_full` or the `t-1` band directly and do not follow the
signal: `core/exits.py:161` (no `exit_stoch_source` field exists) and the scan CSV's band
columns. Neither affects detection. **Both closed 2026-08-15/16** — see below.

**ADR 110 — the raw %K becomes the trigger (2026-08-16).** `stoch_source = "k_fast"`,
`require_fast_agreement = True`. Supersedes the 2026-08-05 note calling `stoch_source`
"an A/B test, not a permanent swap".

No code changed: both are `SignalParams` fields `core/signals.py` already honoured, and
`_fast_agrees` compares `abs(k_fast - k_full)` symmetrically, so "smoothed within 5 of
raw" needed no new comparison. `config_hash` moves `1b97abf7e458d537` ->
`86e91448a65aa40b`. Unlike ADR 108 and 109, no field had to be invented — both are
already hashed.

Measured on the one available sample (bear-reversal rows, universe members, 2026-08-03
to 2026-08-14): 13 rows old, 15 under `k_fast` alone, **7** with agreement. The column
swap *widens*; the agreement check narrows. The dropped rows carry the widest fast/full
gaps (APH 9.5, AVGO 10.5, DAL 10.3), which is where %K accelerates hardest — a
deliberate trade, revisitable once this hash has a backtest.

Four defects and one operational failure came out of the two days around it:

1. **`core/exits.py` hardcoded `"k_full"`** with no field naming it. Invisible while
   `stoch_source` was also `k_full`; a silent entry/exit disagreement the moment it was
   not. Closed by `ExitParams.exit_stoch_source`, defaulting to `k_full` so no measured
   exit moved.
2. **`run_job` caught `Exception`,** which does not include `KeyboardInterrupt`. Every
   Ctrl-C left a `runs` row at `'running'` forever, so nothing could tell a live job from
   a cancelled one. Now records `'interrupted'`.
3. **21 tests depended on a default rather than on their subject.** They set the extreme
   on `k_full` and read `SignalParams()` for the source. Now routed through
   `_ind(k=...)`, which follows `stoch_source`.
4. **Two fixtures named a %K column where they meant a %K value** — surfaced only by
   running the suite under the flipped default, and invisible to the conditional-skip
   approach that was considered first.

The operational failure: the 2026-08-15 chain ran `path backfill` **before** the
backtest. `path` keys on `event_id`, `run_backtest` mints its own event rows, so 2h46m
and 61M rows were orphaned three minutes after they were written, and `path peak-labels`
reported `rows_updated=0`. `cscan events` is redundant before a backtest for the same
reason — the backtest's population is a strict superset. Ordering is now pinned in
`scripts/run_chain_86e91448.ps1`.

**Session 12 is complete.** Tasks 12.1-12.6 all closed, all ten gate items pass.
Migrations `e3c7f5a91d24` (`cell_stats.arm`, `v_screen` config/arm predicates) and
`f1a8d3b62c07` (ADR 107, strength pooling).

One task-level item could not be delivered as specified: 12.4's breadth split is
described as "three rows per cell", but `cell_stats` has no breadth column and
`cell_key()` has no breadth parameter, so those rows have nowhere to go. The terciles
are measured and recorded in `RESULTS.md` instead, which is what gate item 7 asks for.
Storing them would need a schema change and a tenth `cell_key` parameter, and the tenth
parameter would change every existing `cell_id`.

Three findings carry into Session 13:

1. **Nothing is significant after correction.** The minimum q-value over 12 cells x 4
   targets is 0.769, and the single sub-0.05 raw p-value (short `bb_upper_touch` 0-10 at
   the 2% target, edge +2.5 points, p = 0.039) is one test out of 48. Session 13's
   benchmark arms are what turn this into a kill-criterion reading.
2. **`v_screen`'s `signal_strength` join — found and fixed.** The view joined on a
   dimension ADR 102 removed, so no Session 12 statistic could reach the screener.
   Decided as ADR 107 and fixed by migration `f1a8d3b62c07`: the view now pins
   `c.signal_strength IS NULL`, matching how it already pins `c.era IS NULL`. The
   remaining zero is `c.split_key = 'validate'` against a train-split measurement,
   which is invariant 5b working as designed.
3. **Long `stoch_oversold` 10-20 nearly doubles its hit rate in the high-breadth
   tercile** (0.2135 / 0.2085 / 0.4035), which is DESIGN §6.11's market-timing reading.
   Session 13's three-arm comparison on the high-breadth subset answers it.

**Session 9 is complete.** Phase 3 gate passed on all five criteria — see
`docs/RESULTS.md` (Phase 3 — Engine validation) for the run of record
(`run_id=backtest_20260802T183304_6b1c5b52`, `config_hash=3e598c59e7d71eae`)
and `docs/sessions/session-9-backtest/phase3-gate-measurement.md`
for the full measurement detail. The 18-config sweep and the entry-reuse
refactor decision remain open, tracked outside this document.

**Session 10 is complete.** Session 10 built the forward-path store and
derived label layer (tasks 10.1–10.6) and passes its gate with reconciliation
clean against Session 9 labels. ADR 094 records the design (path table as
source of truth, labels derived on demand). Tests and documentation are
complete (task 10.7). See `docs/sessions/session10.md` §4 for gate criteria and
`docs/RESULTS.md` (Session 10 tasks) for run details.

**Pre-Phase-4 cleanup, 2026-08-09.** Work done between Session 10's gate and
Phase 4 planning, none of it changing signal detection, event creation, or
entry/exit logic:

| Item | State |
|---|---|
| Database cleanup | 28.1 GB → 16 GB. 39 config generations → 20: dropped one superseded sweep generation (17 configs run under `min_mcap_usd = 100e9`), the residue of a failed 2026-08-04 run sequence, and one superseded default. The Phase 3 run of record (`3e598c59e7d71eae`) and the live hash are kept |
| ADR 097 | Added 2026-08-08, reverted 2026-08-09, never in effect. A rolling two-year window floors past every split boundary and yields holdout-only event sets. See the ADR before proposing a lookback window again |
| `cscan system-status` | **Built.** Was a `NotImplementedError` stub since Session 0 while DESIGN §9.6 and ADRs 080/083 specified it. See below |
| ADR 093 peak-within-horizon | Target family documented (amendment, 2026-08-09) and materialized as `events.peak_ret_1d/2d/3d/5d/10d` (migration `c7e1a4f9d302`). See below |

**`cscan system-status` (DESIGN §9.6, ADR 080, ADR 083).** Prints last run and
staleness per job, the latest schedule slot with ADR 080's catch-up flag, and
any `runs` row still marked `running` past a threshold. Query layer is
`jobs/status.py` (read-only, returns frames); rendering is in the CLI.
Thresholds live in `core/config.MonitoringThresholds`, a standalone frozen
dataclass deliberately **not** a field of `Config` — same standing as
`SweepParams` and `SharesPlausibility`, so `config_hash` does not move for a
value that cannot affect an event row.

Two write-side gaps closed alongside it. `scheduled_runs.status` could only
ever hold `'started'`, because `record` wrote that literal and nothing else
touched the column; `scheduled_runs.complete` now closes the slot, and
`nightly`, `weekly`, and `monthly` call it. `run_id` was NULL on all three of
those jobs' slots, breaking the join back to `runs` that ADR 080 specified it
for; `complete` writes it, since `record` fires before `run_job` mints one.

Deliberately not done: `runs` rows stuck at `running` are reported by age, not
relabelled. `runs_status_check` allows only `running`/`ok`/`failed`, and an
interrupted process is none of the three — marking it `failed` would assert
something untrue in the table ADR 034 makes the provenance record.

**Peak-within-horizon materialization (ADR 093 amendment).** `M_h = max` of the
entry-anchored return over `[1, h]`, for the five horizons `StatsParams.
fwd_ret_horizons` already carries. Two families now sit side by side on
`events`: `fwd_ret_{h}d` answers "sell on day h regardless of path,"
`peak_ret_{h}d` answers "best available inside h days."

| Piece | Where |
|---|---|
| Migration | `c7e1a4f9d302_events_peak_ret_horizons.py` — five nullable `numeric(12,6)`, catalog-only |
| Writer (product) | `research/peak_labels.py` — one set-based `UPDATE ... FROM`, idempotent, scoped to one `config_hash` |
| Reference (oracle) | `path_labels.derive_labels_from_path` — per-event Python |
| Agreement test | `test_peak_labels.py::TestSqlMatchesPythonReference` |
| Refresh | `nightly`, after `path_capture`, plus `cscan path peak-labels` |

Two implementations exist on purpose, which invariant 2 would normally forbid.
The SQL is what runs; the Python is the oracle, and a divergence fails a test
rather than producing two quiet answers. Same shape as `path_reconcile`'s
Session-9-versus-path-derived comparison.

Three properties are pinned by test: `M_h >= R_h`, `M_h` non-decreasing in `h`,
and `M_h` unchanged when `holding_days` moves. The third is the one that
matters most — **`events.mfe` is not a substitute for `peak_ret_5d`.** MFE stops
at the exit bar and therefore moves with every stop or target sweep, which is
the config coupling ADR 064 rejects for model targets. It is also the column
that already existed, which makes it the easy wrong choice.

`peak_ret_{h}d` is NULL until an event's forward window closes; a partial
maximum is a smaller number wearing a complete label. Phase 4 and Phase 6 must
filter on window completeness the same way they already do for the other label
families.

Populated for the live `config_hash` only (user's decision, 2026-08-09). The
superseded generations in `events` are kept as database history, not as
anything a query reads, so retrofitting them buys no consumer anything.

**Open, carried into Phase 4 planning (2026-08-09).** Not blocking Phase 4's
statistics work, but decided-against-doing-now rather than unnoticed:

| Item | Status |
|---|---|
| `v_positions` exit literals (ADR 095) | Deferred to Phase 5 by that ADR's own reasoning — Phase 4 does not read the view |
| **ADR 092's source assertion is narrower than it reads** | **Not deferred anywhere. Candidate for Phase 4** |
| `cscan logs --job X --tail N` | Still a `NotImplementedError` stub; DESIGN §9.6 specifies it alongside `system-status`, which is now built |

The assertion gap is the one worth acting on. ADR 092's Enforcement line says
"a source assertion that no bare numeric literal appears in the exit threshold
comparison," which reads as solved. Measured: it is
`test_exits.py::test_exits_module_contains_no_bare_stochastic_literal`, it
greps `inspect.getsource(core.exits)` for the two strings `"80.0"` and
`"20.0"`, and that is its entire universe. One module, two spellings, no SQL.

That is how ADR 095's `v_positions` defect survived. And ADR 095's proposed
remedy — "extend the assertion to scan checked-in SQL" — does not work as
stated: `db/schema.sql:1005` spells the threshold `(s.k_full >= (80)::numeric)`
and line 1008 spells the timeout `>= 5`. Neither matches `"80.0"` or `"20.0"`,
so re-aiming the existing matcher at SQL finds nothing. **The matcher needs
replacing**, with a numeric literal adjacent to a comparison on a
threshold-bearing column, not a substring search.

Doing this in Phase 4 costs a test and guards every future instance. Deferring
it to Phase 5 means the next SQL exit literal lands with the same silence.

**Phase 4 boundary is after Session 10.** No change to signal detection, event
creation, or entry/exit logic. Phase 4 does not start until Session 10 passes,
which it now has.

**Label source contract for Phase 4** (pinned 2026-08-05, correcting an
earlier blanket statement here that the statistics layer "reads from the path
table" for everything — for forward returns, doing so would swap the price
series):

| Label family | Phase 4 reads | Why |
|---|---|---|
| `fwd_ret_1d/2d/3d/5d/10d` | `events.fwd_ret_*d` | Return measurement takes **total-return `adj_close`** (`core/returns.forward_returns`). `path.terminal` is split-adjusted close anchored to `entry_price` — a different quantity by design, which is why reconciliation marks the whole `fwd_ret_*d` family explained rather than matching it |
| MFE, MAE, time-to-MFE, capture_ratio | path-derived layer (`research/path_labels.py`) | Both sides use split-adjusted OHLC; reconciliation matches to zero |
| Reachability (`touched_*pct`, `day_touched_*pct`) at the configured targets | path-derived layer | Same series; residual is 9 verified boundary events |
| Any threshold or horizon beyond the materialized set | `research/path_queries.py` against `path` | Config-parametrized, no migration |

The rule underneath: **`path` is the source of truth for anything measured off
the traded price series, and never for return measurement across a horizon.**
DESIGN §2.2 and `core/returns.py`'s module docstring own that split; the path
table does not override it.

**Two consumer rules Phase 4 must follow** (added 2026-08-06):

**Filter on window completeness, always.** Every statistic reads only events
satisfying `fwd_window_days >= entry_offset + max_hold_days`. An event whose
forward window was still open when the backtest last ran carries labels frozen
at that moment, and `cscan weekly`'s refresh (`jobs/cli.py::weekly`) narrows
that staleness window without closing it — a signal that fired last night is
legitimately unlabelable until its window closes. The filter makes the
statistics correct regardless of when the refresh last ran, rather than
depending on scheduling. See ADR 094, "Materialized labels need a refresh job,
not just an exclusion filter."

**Never read `first_touch_day` alone.** `research/path_labels.py` exposes two
functions with deliberately different null semantics:

| Function | Returns `None` when |
|---|---|
| `touched_by` | the window is too short to answer — three-valued, so `True`/`False`/unknown are distinguishable |
| `first_touch_day` | **either** the target was never touched in a complete window **or** the window was too short |

Both are correct and both match §10.5's "null means untouched, never zero."
But a caller reading `first_touch_day` on its own cannot tell "did not happen"
from "cannot know yet," and averaging the two together silently biases the
time-to-touch distribution toward whatever the recent, still-accumulating
events look like. Pair the calls: use `touched_by` to establish
answerable-ness, then read `first_touch_day` for the day.

---

## 1. Ground rules for every session

1. **Read `DECISIONS.md` before writing code.** 87 ADRs. They are decisions, not suggestions. If a task appears to require contradicting one, stop and ask.
2. **Tests before implementation for anything in `core/`.** Coverage gate is 90% on `core/` only.
3. **No magic numbers outside `core/config.py`.**
4. **Every generated row carries `run_id` and `git_sha`.**
5. **Alembic tasks get step-by-step walkthroughs** — see §2 below. The user has not used Alembic before and this is a learning objective, not just a build step.
6. **Before writing any frontend component, read the frontend-design skill.**

---

## 2. Alembic protocol

The user is new to Alembic. Every task touching migrations must:

- Explain what the command does before running it
- Show the generated file and walk through each line
- Explain what `upgrade()` and `downgrade()` do in that specific case
- Show how to verify the result (`alembic current`, `\d tablename` in psql)
- Never run `--autogenerate` without reading the output aloud first

Expected usage over the project: ~15 migrations total, front-loaded into sessions 1-3. Day-to-day commands are `cscan db migrate` and `cscan db status`, wrapping `alembic upgrade head` and `alembic current`.

**Rule:** `cscan db migrate` applies to **both** databases by default and reports each. Single-target requires an explicit flag. Forgetting the second database is the main way this goes wrong.

---

## SESSION 0 — Scaffold

**Goal:** a repo that runs, tests, and lints, with a CLI that does nothing yet.

### Tasks

**0.1 Repo structure**

```
capitalscan/
  core/          config.py types.py indicators.py signals.py
                 exits.py returns.py costs.py universe.py
  jobs/          fetch/ ingest.py compute.py poll.py sync.py cli.py
  research/      backtest.py stats.py baseline.py benchmarks.py train.py
  handlers/      screen.py stats.py predict.py explain.py   # empty until Phase 5
  mcp/           server.py tools.py auth.py
  web/           (Next.js, session 10+)
  db/            migrations/ schema.sql
  tests/         unit/ property/ golden/ integration/ acceptance/
  data/          cache/ (gitignored)  universe_union.csv
  models/        (LFS, populated in Phase 6)
  logs/          (gitignored)
  docs/          DECISIONS.md DESIGN.md BUILD.md TESTS.md RESULTS.md
  scripts/       wip_snapshot.ps1 backfill.bat tasks/*.xml
  CLAUDE.md
  pyproject.toml
  docker-compose.yml
  .env.example
```

`handlers/`, `mcp/`, and `web/` are created empty in session 0 and stay empty until Phase 5. They exist in the scaffold so imports and tooling config are settled once.

`db/seeds/` was in the original scaffold and is not listed above because it was never used. Reference data — the trading calendar, ticker identity, corporate actions, shares outstanding, earnings dates — landed in migration `f8a373722e8d` instead, which is the right home for it: a seed file would have to be applied separately and could drift from the schema it populates. The empty directory was removed 2026-08-18. Git never tracked it, since git does not track empty directories.

**0.2 `pyproject.toml`**

Dependencies: `pandas`, `numpy`, `scipy`, `sqlalchemy`, `psycopg[binary]`, `alembic`, `pydantic`, `typer`, `rich`, `yfinance`, `pandas-market-calendars`, `requests`, `pyarrow`, `python-dotenv`, `tomli`.

Dev: `pytest`, `pytest-xdist`, `pytest-cov`, `hypothesis`, `testcontainers[postgres]`, `ruff`, `mypy`.

Deferred to later sessions: `lightgbm`, `scikit-learn` (Phase 6), `mcp` (Phase 5).

Use `uv`. Commit the lockfile.

**0.3 `docker-compose.yml`**

Docker Desktop on Windows. This is the only Linux in the local stack (ADR 081) and it is transparent.

Postgres 16, named volume, port 5432. Tuning per DESIGN §9.3:

```
shared_buffers=1GB
work_mem=32MB
maintenance_work_mem=512MB
effective_cache_size=4GB
max_parallel_workers=8
```

Default Postgres settings make the backfill noticeably slower than necessary.

**0.4 `.env.example`**

```
DATABASE_URL_RESEARCH=postgresql://capscan:capscan@localhost:5432/capitalscan
DATABASE_URL_SERVING=
SEC_USER_AGENT="Your Name your@email.com"
FINNHUB_API_KEY=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6
NOTIFY_DISCORD_WEBHOOK=
NOTIFY_NTFY_TOPIC=
NOTIFY_SMTP_HOST=
NOTIFY_SMTP_USER=
NOTIFY_SMTP_PASS=
NOTIFY_SMTP_TO=
MCP_BEARER_TOKEN=
```

`SEC_USER_AGENT` must contain a real email. SEC blocks requests without it at the IP level, persistently.

**0.4b Windows multiprocessing guard**

`ProcessPoolExecutor` uses **spawn** on Windows. Set this up correctly once in session 0 rather than debugging it in session 6:

- Every job module importable with no side effects. No top-level execution.
- `if __name__ == "__main__":` on every entry point. Without it, spawn recursively creates processes.
- Workers open their own database connections. Connections are not picklable.

Add a test in session 0 that spawns a trivial pool and asserts it returns, so the guard is verified before any real parallel work exists.

**0.5 Config resolution — in `jobs/`, not `core/`**

`core/config.py` holds frozen dataclasses only. Its sole import is `dataclasses`. No `open()`, no `os.getenv()`, no `Path.exists()`, no `dotenv`. Invariant 1 applies to `core/config.py` like every other module in `core/`.

`jobs/config.py` owns resolution: CLI flag > env var > `config.toml` > dataclass default. It returns populated frozen dataclasses. `core/` receives them as arguments.

The resolver raises `ConfigError` on parse failure, unknown keys, and type-coercion failure. Never fall back to defaults on malformed input: `config_hash` is stamped on every generated row, so a silently-defaulted config produces provenance that describes parameters which never ran.

Tests for the resolver must pass an explicit `config_file` and clear `CAPSCAN_*` with `monkeypatch.delenv`. A resolver reading ambient CWD and environment makes the suite depend on the machine, and a green CI then proves only that no stray variable happened to be set.

**0.6 CLI skeleton**

Typer app named `cscan`. Stub commands with `--help` text for every job in DESIGN §4.1. Each stub raises `NotImplementedError`.

**0.7 Logging**

Rotating JSON lines to `logs/`, plus stdout. `cscan logs --job <name> --tail N`.

**0.8 CI**

`.github/workflows/ci.yml`:
- fast job: `ruff check`, `ruff format --check`, `mypy`, `pytest tests/unit tests/property`
- slow job: `pytest tests/golden tests/integration` with a Postgres service container

**0.9 `.gitignore` and WIP script**

Gitignore: `data/cache/`, `logs/`, `.env.local`, `__pycache__`, `.venv`.

`scripts/wip_snapshot.ps1` per ADR 082 — **explicit path allowlist, never `git add -A`**, pushes to a throwaway `wip` branch.

**0.10 Git LFS**

Set up **now**, before any binary is committed. Adding LFS after the fact requires rewriting history.

```bash
git lfs install
git lfs track "models/**/*.txt"       # LightGBM native format
git lfs track "models/**/*.pkl"       # isotonic calibrators
git lfs track "models/**/*.parquet"   # training feature snapshots
git add .gitattributes && git commit -m "configure git lfs"
```

Per ADR 082, LFS holds **promoted model artifacts only** — roughly 22 MB per version, monthly. Free tier is 1 GB storage and 1 GB monthly bandwidth, which covers several years at that rate.

Explicitly **not** in LFS: the research database (1.5 GB, goes to GitHub Release assets monthly per ADR 083), the raw fetch cache (refetchable, gitignored), and logs.

Verify with `git lfs track` and confirm `.gitattributes` is committed.

### Acceptance

```powershell
docker compose up -d ; uv sync
cscan --help                # lists all commands
pytest                      # spawn-guard test passes, exits 0
ruff check ; mypy core/     # clean
```

---

## SESSION 1 — Schema

**Goal:** every table and view from DESIGN §2.5, §5.7, §6.8, §7.8, §8.5 exists, with Alembic managing it.

### Tasks

**1.1 Alembic init** *(walkthrough required, see §2)*

```bash
alembic init db/migrations
```

Edit `alembic.ini` and `env.py` to read `DATABASE_URL_RESEARCH` from env. Explain what each file does.

**1.2 Migration 001: reference tables**

`trading_days`, `tickers`, `corporate_actions`, `shares_outstanding`, `earnings`. DDL is in DESIGN §2.5 — copy it exactly.

**1.3 Migration 002: market data**

`bars`, `bar_rejects`, `market_days`, `indicators`. Note the CHECK constraints on `bars`; they are the first line of validation.

**1.4 Migration 003: universe and audit**

`universe`, `runs`, `scheduled_runs`, `poller_sessions`, `quotes_live`, `signal_reports`.

Note: there is no `regime` table. `universe` already stores `as_of` plus the five `crit_*` columns separately, which is what ADR 014 requires for ablation. Do not create a second one.

**1.5 Migration 004: events**

**`events` has no `cell_id` column and must not gain one.** `cell_id` is derived, not stored — see DESIGN §8.2. If a view appears to need it, join on component columns instead.



`events` per DESIGN §5.7, with all three indexes. This is the widest table; take care with the cluster columns and the UNIQUE constraint on `(config_hash, ticker, signal_date, signal_type, entry_kind)`.

**1.6 Migration 005: statistics, predictions, positions**

`cell_stats` (DESIGN §6.9), `benchmarks` (§6.10), `predictions` and `outcomes` (§7.8), `positions` and `order_intents` (§8.5).

`signal_reports` has a foreign key to `predictions`, so if migration 003 created it, add that constraint here rather than earlier.

**1.7 Migration 006: views**

Create `cell_key()` first — it is an immutable SQL function the stats job and the views both use.

All eight views, with full DDL in DESIGN §8.2: `v_screen`, `v_ticker_state`, `v_chart`, `v_stats`, `v_events`, `v_forward`, `v_universe`, `v_positions`. Copy them exactly.

`v_positions` depends on `v_ticker_state`, so create it after. `v_screen` hardcodes `split_key = 'validate'` in its join. This is deliberate and must not be changed to inherit `e.split_key`, which would leak holdout statistics into the UI.

`v_events` uses `current_setting('capitalscan.default_config_hash', true)` — the `true` second argument makes it return NULL rather than erroring when unset, which is what you want before the first backtest exists.

**1.8 CLI wiring**

```
cscan db migrate     # alembic upgrade head, BOTH databases by default
cscan db status      # alembic current for each
cscan db rollback    # alembic downgrade -1, with confirmation prompt
cscan db schema      # pg_dump --schema-only > db/schema.sql
```

**1.9 Commit `db/schema.sql`**

Regenerated after every migration. 30-50 KB of readable DDL, far better as a reference than reading migration files.

### Acceptance

```bash
cscan db migrate                    # clean apply on empty database
cscan db status                     # reports head revision
cscan db rollback && cscan db migrate  # down and back up, no errors
psql -c '\dt' | wc -l            # all tables present
psql -c '\dv'                    # all views present
```

---

## SESSION 2 — Core: indicators

**Goal:** correct, verified indicator computation with zero IO.

### Tasks

**2.1 `core/config.py`**

Every dataclass from DESIGN §3.3, frozen, with the defaults exactly as written: `IndicatorParams`, `SignalParams`, `ExitParams`, `CostParams`, `UniverseParams`, `StatsParams`, `SplitParams`.

`StatsParams` holds the suppression threshold (`min_n_eff = 30`), FDR alpha, replication counts, and bucket boundaries. These are used in Phase 4 but belong here now so they are never written as literals elsewhere.

**2.2 `core/types.py`**

Every enum and dataclass from DESIGN §3.4.

**2.3 Indicator registry** *(ADR 051)*

```python
@register("name", deps=[...], warmup=N, dtype="numeric(12,6)")
```

Registry exposes `max_warmup()` for the read-window expansion in the indicators job.

**2.4 Indicator functions**

`bollinger`, `stochastic` (fast and full), `atr`, `realized_vol`, `rolling_percentile`, `drawdown_from_high`, `sma_slope`, `volume_zscore`, plus `k_cross_up` / `k_cross_down`.

Formulas are pinned in DESIGN §3.5. Do not derive them from another source.

Three specifics that decide correctness — all three are in DESIGN §3.5 and each needs a test:
- Which price series each function takes (and the `realized_vol` exception)
- Stochastic returns NaN when `H_14 == L_14`, never 50 or 0
- `rolling_percentile` convention: fraction of the trailing window at or below, strict trailing

**2.5 `compute_all`**

Iterates the registry. One ticker in, one row per bar out, columns matching the `indicators` table exactly. No translation layer.

**2.6 Golden fixtures**

Build the fixtures listed in `TESTS.md` §4. At minimum for this session: `tsm_2026_07.csv`, `nvda_split_2024.csv`, `flat_series.csv`.

**2.7 External verification tooling** *(ADR 086)*

```bash
cscan verify-indicators --ticker TSM --ticker NVDA --dates 2026-07-29,...
```

Prints computed `bb_upper`, `bb_mid`, `bb_lower`, `k_full`, `d_full` and writes `tests/golden/external_reference.csv` with empty `external_*` columns. The user fills these once by hand from StockCharts or TradingView (~30 min). A test then asserts agreement within 0.1%.

This catches the ddof and SMA-vs-EMA class of silent divergence that every downstream number would inherit.

### Acceptance

```bash
pytest tests/unit/test_indicators.py tests/golden/ -x
# external_reference test passes once the user fills the CSV
```

---

## SESSION 3 — Core: signals and exits

**Goal:** the two modules where a bug produces a plausible-looking wrong number.

### Tasks

**3.1 `core/signals.py`**

`_breach`, `detect`, `breach_live` per DESIGN §3.6. Implement `_breach` first; everything routes through it.

Confluence definitions are pinned in DESIGN §3.6. **Indicators are read at t−1, never t.** This is the highest-risk silent failure in the system and it gets a dedicated test.

Multi-signal handling per ADR 057: one hit, most-specific `signal_type`, full `signal_types_all`, `signal_strength` count. Specificity ranking is fixed in DESIGN §3.6.

Crossover flags computed and stored, never gating (ADR 045).

**3.2 `core/exits.py`**

`resolve_exit` with the **exact** priority order in DESIGN §5.5. Gap rules are in the table there. Do not invent an ordering.

The four details in §5.5 each need a test: gap checks first, band levels from i−1, `ambiguous` on stop-and-target collision, stochastic evaluated last.

**3.3 `core/returns.py`**

`forward_returns`, `mfe_mae`, `realized_return`, `entry_price_for`.

Note the coverage constraint in DESIGN §3.8: `TOUCH_5M` and `TOUCH_30M` return null before the hourly archive begins, never a fabricated value.

**3.4 `core/costs.py`**

`apply_costs`, `borrow_cost`, `slippage` per DESIGN §3.9.

**3.5 `core/universe.py`**

`evaluate_criteria` returning the five ADR 014 criteria **separately**; `is_tradeable` accepting a subset, so ablation is a config change.

**3.6 Property tests**

Exit invariants across 10,000 hypothesis-generated cases. The four invariants are in `TESTS.md` §3.4. The sharp one: `mfe >= realized_return`. A violation means path metrics and the exit disagree about what happened.

**3.7 Parity test** *(ADR 006 enforcement)*

`detect()` on a bar must produce the same signal set as `breach_live()` walked across a simulated intraday path (open → low → high → close).

**3.8 Look-ahead test**

Shift all indicators forward one bar, rerun detection, assert Jaccard overlap below 0.5. High overlap means the signal is not reading the indicators or is already using future data.

### Acceptance

```bash
pytest tests/unit/test_signals.py tests/unit/test_exits.py \
       tests/property/ -x
# parity, look-ahead, and all four exit invariants pass
```

---

## SESSION 4 — Fetchers

**Goal:** all external IO behind one contract with retry, rate limiting, and disk caching.

### Tasks

**4.1 `jobs/fetch/base.py`**

`Fetcher` protocol, `with_retry`, `rate_limited`, `cached` per DESIGN §4.2.

Retry on connection errors, timeouts, 429, 5xx. **Never retry 404** — that means the ticker does not exist and belongs in `bar_rejects`.

**4.2 Disk cache**

`data/cache/{source}/{key}.parquet`, written before parsing. This is what turns a 40-minute backfill into a 90-second replay while iterating on the parser, which will happen several times.

**4.3 Rate limits**

| Source | Setting |
|---|---|
| yfinance | 0.5 req/s, batches of 50 |
| SEC EDGAR | 8 req/s, `User-Agent` mandatory |
| Finnhub | 0.8 req/s |

Removed 2026-08-01: a Stooq row (2 req/s) was here for the cross-check fetcher. Stooq began serving a JavaScript proof-of-work challenge to automated requests on every endpoint tried, so the fetcher and the cross-check it backed were both removed; the pipeline is single-source on Yahoo.

**4.4 Fetchers**

`yahoo.py` (daily bars, hourly bars, quotes, actions, chains), `sec.py` (XBRL company facts, 8-K submissions), `finnhub.py` (forward calendar), `wikipedia.py` (membership history). (`stooq.py` was removed 2026-08-01; see §4.3.)

**4.5 Progress reporting** *(ADR 052)*

`rich.progress` for anything over 30 seconds: current item, completed/total, elapsed, ETA, running error count. `--quiet` emits JSON lines for cron.

### Acceptance

```bash
pytest tests/integration/test_fetchers.py
# fetches 3 real tickers, cache hit on second call, rate limit respected
```

---

## SESSION 5 — Ingest jobs

**Goal:** raw data landing correctly, with validation that fails loudly.

### Tasks

**5.1 `calendar`** — NYSE calendar into `trading_days`.

**5.2 `tickers` and `membership`**

Wikipedia scrape, manual review, freeze to `data/universe_union.csv` and commit it (ADR 055). Roughly 600-750 tickers with add and remove dates. CIK mapping from SEC.

**5.3 `bars_daily`**

Per DESIGN §4.3. `auto_adjust=False` is **mandatory** — the default destroys the split-adjusted series the band levels need. `threads=False`.

Handle both failure modes explicitly: partial batch failure (NaN columns, no exception) and silent truncation (short history returned without warning).

**5.4 `bars_hourly`**

Per DESIGN §4.4. 13 sequential 60-day windows per ticker. Regular session only. Checkpoint per ticker.

**5.5 `actions`, `market`**

Splits and dividends into `corporate_actions`; SPX and VIX into `market_days`.

**5.6 `shares`**

SEC XBRL company facts. Point-in-time semantics per DESIGN §2.4: use the latest filing with `filed_on < as_of`.

**5.7 `earnings`**

Historical from EDGAR 8-K filings, frozen after backfill. Forward from Finnhub, 90-day window, weekly (ADR 036). Store `source` and `confidence` per row.

**5.8 Validation**

Every rule in DESIGN §2.3, writing to `bar_rejects` rather than dropping silently.

`cscan validate --report` prints reject counts by rule and per-ticker coverage. This is a deliberate human gate in the backfill runbook. (Until 2026-08-01 it also ran a Stooq cross-check on 20 tickers; removed when the vendor began blocking automated requests — see §4.3.)

**5.9 Idempotency**

All writes are upserts. Verify by running any ingest job twice and asserting identical row counts.

### Acceptance

```bash
cscan backfill --tickers TSM,NVDA,AAPL --start 2020-01-01 --through-validate
cscan validate --report      # zero rejects at 'reject' severity
# rerun the same command: row counts unchanged
```

---

## SESSION 6 — Compute jobs and `scan()`

**Goal:** the v1 deliverable.

### Tasks

**6.1 `indicators` job**

Per DESIGN §4.5. **The read window and write window differ by design.** `read_start = target_start − ceil(max_warmup() × 1.6) days`. Getting this wrong produces silent nulls at the range boundary.

`ProcessPoolExecutor(max_workers=14)`. Merge `days_to_earnings`, `spx_ret_1d`, and VIX in a single vectorized pass after per-ticker computation.

**6.2 `universe` job**

Quarterly evaluation per DESIGN §4.6. Handle all three data hazards: shares lag, sector median fallback for thin sectors, revenue growth null-vs-false distinction.

**6.3 `events` job**

Per DESIGN §4.7. The t−1 indicator read is guarded **again** here at the job level — two layers, deliberately.

Debounce: one event per ticker per bound per day. Cluster tagging per DESIGN §5.3.

**6.4 `scan()`** *(ADR 049 — the v1 acceptance criterion)*

```python
scan(tickers, signal_types, start, end, universe="trade") -> pd.DataFrame
```

One row per event: ticker, timestamp, signal type, touch level, %B, %K full, %K fast, crossover flags, drawdown bucket, trend state. No statistics, no predictions, no exits.

**6.5 CLI**

```bash
cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30
cscan scan --universe trade --date today
```

**6.6 Warmup assertion** *(ADR 040)*

Test asserting zero nulls in every required indicator column on or after 2010-01-01 for any ticker with continuous coverage.

### Acceptance — **PHASE 1 GATE**

```bash
cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30
# returns the 2026-07-29 event with correct %B and %K
```

Plus:
- All golden fixtures pass
- Zero nulls in indicators after 2010-01-01
- Random-walk null test passes (`TESTS.md` §6)

(This gate used to also require Stooq agreement within tolerance. Removed 2026-08-01 — Stooq began blocking automated requests; the pipeline is single-source on Yahoo.)

---

## SESSION 7 — Full backfill

**Goal:** 750 tickers, 16 years, clean.

### Tasks

**7.1 Run the backfill**

```bash
cscan backfill --all --through-validate   # ~50 min, stops at the gate
# read the report
cscan backfill --all --resume             # ~2.5 hr
cscan bars --hourly --backfill            # ~5.4 hr, overnight, checkpointed
```

**7.2 Inspect and fix**

Expect failures on delisted tickers, ADRs with unusual histories, and pre-2012 data quality. Every fix goes into a validation rule, not a special case.

**7.3 Record the result**

Append to `RESULTS.md`: ticker count, bar count, reject counts by rule, coverage gaps, and any tickers dropped with reasons.

**7.4 First `pg_dump`**

Nightly dump to the second local disk. Monthly to a GitHub Release asset (~300 MB compressed).

### Acceptance

- 750 tickers with `first_bar` and `last_bar` populated
- `cscan validate --report` clean at `reject` severity
- Indicators computed for the full range with no post-2010 nulls
- `cscan scan --universe trade --date <any recent date>` returns plausible results

---

## SESSION 8 — Poller and notifications

**Goal:** Phase 2.

### Tasks

**8.1 `poll` job** — DESIGN §4.8. Bands loaded once at 09:15; the loop is two float comparisons per ticker per tick.

**8.2 Debounce state in Postgres**, not process memory, so a restart mid-session does not re-fire.

**8.3 `Notifier` protocol** *(ADR 054)* — SMTP, Discord webhook, ntfy. Each independently toggleable, any combination active.

**8.4 `positions`** — user-declared entry, closed positions with realized returns, exit signals pushed (ADR 073).

**8.5 `order_intents`** *(ADR 048)* — structured intent with an idempotency key. Renders as a notification. **No broker client. No credentials. No code path capable of placing an order.**

**8.6 Live call overlay** *(ADR 050)* — on a long signal, pull the current chain from yfinance and show cost at the real ask, breakeven, payoff at each reachability rung, and max loss. Labeled as priced from live quotes, explicitly not backtested.

**8.7 `poller_sessions`** — coverage logging so gaps are visible rather than inferred (ADR 084).

**8.8 Task Scheduler** *(ADR 080)* — enable "Run task as soon as possible after a scheduled start is missed" so a missed job fires at next boot. Export task definitions to `scripts/tasks/*.xml` and commit them. `scheduled_runs` records delay.

### Acceptance — **PHASE 2 GATE**

- Poller detects a live breach within one polling interval
- Notification delivered on all three configured channels
- `poller_sessions` records the session with coverage percentage
- Restart mid-session does not re-fire an already-sent event

---

## SESSION 9 — Backtest engine

**Goal:** Phase 3. **Complete — Phase 3 gate passed 2026-08-02.** See
`docs/RESULTS.md` for the run of record and the gate table.

### Tasks

**9.0 Wire the hourly pull into the nightly chain** *(prerequisite for 9.2)* — `run_bars_hourly` has exactly one caller, the `bars` CLI command. `nightly()` in `jobs/cli.py` fetches daily bars and skips hourly, so nothing keeps the hourly table current and it freezes at whatever the last manual `cscan bars --hourly --backfill` produced. Add one line after `run_bars_daily`:

```python
ingest.run_bars_hourly(tickers, start, end, engine=engine)
```

`start` is already `end - 5` in that function, so `_hourly_windows` yields a single 60-day window per ticker rather than the 13 a full backfill walks. Cost is roughly **21 minutes** for ~630 tickers at `RATE_LIMIT_PER_SEC = 0.5`, against ~4.6 hours for the 725-day backfill.

Safe to defer until this session: hourly is inert until 9.2 exists, since DESIGN §3.8 reads it only to resolve `TOUCH_5M` and `TOUCH_30M`. Bars already written persist, and only the *fetchable* window slides, so a later run recovers any gap shorter than 725 days.

Failure mode if skipped: `entry_price_for` returns null rather than raising when hourly is absent (`tests/unit/test_returns.py:199`), so a stale hourly table silently drops two of the four entry kinds instead of erroring.

**9.1 `research/backtest.py`** — the pipeline in DESIGN §5.2, exactly in that order.

**9.2 Entry resolution** — four kinds, DESIGN §5.4, with the gap rule.

**9.3 Exit resolution** — calls `core.exits.resolve_exit`. No second implementation.

**9.4 Path metrics** — MFE, MAE, time-to-MFE over `[t+1, exit_idx]`; reachability over the **full** `[t+1, t+5]` regardless of exit timing.

**9.5 Context tagging and split assignment** — `split_key` assigned at creation by date, never at query time.

**9.6 `cofire_count` post-pass** — single-threaded, groups across tickers.

**9.7 Determinism** *(ADR 060)* — sorted dispatch, sorted write, no wall-clock reads, `config_hash` on every row.

**9.8 Validation harness** — the five checks in DESIGN §5.10.

**9.9 Default config run first** *(ADR 059)* — ATR stop k=1.5, target 4%, `NEXT_OPEN`. Full harness. **Then ~20 events inspected by hand against charts before any sweep.**

**9.10 Sweep** — 18 exit configs, entry prices computed once and reused. ~4-5 minutes.

### Acceptance — **PHASE 3 GATE**

- Exit invariants hold across 10,000 property-generated cases
- Ambiguity rate below 10%, or hourly escalation implemented
- Event rate passes the three checks in §9a below
- Two runs with identical config produce identical output ignoring `run_id`
- All five validation harness checks pass

### 9a. Event-rate criterion

The old wording — "within 20% of the analytical estimate (~4% of ticker-days
for confluence)" — was **ambiguous and its estimate was wrong**. Measured
2026-08-01 over 2,371,529 ticker-days from 2010-01-01, joining bars to
indicators at t−1 (the pairing `detect` actually uses):

| Reading of "confluence" | Rate |
|---|---|
| `confluence_low`, on closes | 4.43% |
| `confluence_low`, on intraday extremes | 7.16% |
| either side, on closes | 11.38% |
| either side, on intraday extremes | **18.34%** |

Three readings, four-fold spread. The one matching the old ~4% estimate is
`confluence_low` on **closes** — which is exactly what ADR 005 says not to
do, since "a daily bar's extremes are the intraday touch". The engine's
18.34% is the deliberate design: intraday extremes, both sides.

**Do not replace the estimate with the measured value.** A criterion set to
what the engine currently emits is circular — it passes on the day it is
written and every day after, including the days something breaks. An
analytical estimate earns its place by being derived independently.

Three checks instead, each verifiable against something other than itself:

**1. Structural invariants.** Provable, and a violation means detection is
genuinely broken:

```
P(low  <= bb_lower)  >=  P(close <= bb_lower)     (low <= close, always)
P(high >= bb_upper)  >=  P(close >= bb_upper)
P(confluence_low)    <=  min(P(lower touch), P(oversold))
```

**2. Component rates against theory.** Each has an independent prediction,
so a drift is interpretable rather than just different:

| Component | Independent prediction | Measured 2026-08-01 |
|---|---|---|
| `P(k_full <= 20)` | ~20%: the oscillator is bounded [0,100] and roughly uniform | 15.92% |
| `P(close <= bb_lower)` | ~2.5% under normality; higher with fat tails | 5.29% |
| `P(low <= bb_lower)` | strictly above the close rate | 11.18% |

**3. Headline band, deliberately wide.** Confluence on either side, intraday
extremes, fires on **10-25%** of ticker-days. Wide enough not to be
circular; tight enough to catch an order-of-magnitude error such as a
dropped t−1 shift.

Whether 18% is a *useful* event rate is a Phase 4 statistical-power
question, not a Phase 3 correctness one.

---

## Later phases

Session plans get written once the preceding gate produces data.

**Phase 4 — Statistics.** Baselines (dual, per ticker-year), effective sample size, three benchmark arms with the 200-replication randomization null, **trim-and-redeploy variant (DESIGN §6.5, ADR 017)**, DCA comparison, headline grid, BH correction, era breakdown, cluster-sequence comparison, long-vs-short asymmetry, **pre/post-tax reporting (DESIGN §8.8, ADR 032)**, `cell_stats`. Plus the statistical self-validation in DESIGN §6.12 — **the random-walk null test runs before any real result is trusted.**

**Phase 5 — Frontend, tools, MCP.** Screener, ticker page, research page, `handlers/` layer, seven tools, response validator, MCP server (ADR 027, built after Phase 4 results exist).

**Phase 6 — Model.** Eleven heads, purged walk-forward CV, isotonic calibration, four-check promotion gate, forward log, live inference in the poller, breach-depth features added after the base model exists.

**Phase 7 — Options.** Synthetic Black-Scholes layer labeled synthetic, then one month of Polygon chains to measure and publish the pricing error.

---

## Kill criteria

Fixed in advance (ADR 033), before sunk cost exists.

- **No cell beats its per-ticker-year baseline** by the power-adjusted threshold at sufficient `n_eff` after FDR correction → the two-indicator hypothesis is dead as a standalone claim.
- **Validation edge under half the training edge** → overfit. Cut features, widen cells.
- **Holdout edge negative** → report it.

What survives a null result: the event-study engine with correct look-ahead handling and MFE/MAE tracking, the tool-restricted chat layer, the calibration methodology, and the ingest and scheduling pipeline. The pivot keeps the engine and swaps the input signal — volatility term structure, earnings drift, cross-sectional momentum residuals, and volume-price divergence are all testable in the same framework with no rewrite.

**Holdout is evaluated exactly once, at the end, and published whatever it says.**
