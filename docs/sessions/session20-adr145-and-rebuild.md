# Session 20 — ADR 145, the indicator projection defect, and the ADR 122 gap

**Written 2026-08-21 15:25 PT as a resume point.** A long chain is running
detached; everything needed to pick this up cold is here.

---

## What is running right now

`stage3.sh`, **PID 32842, PPID 1** — detached from the Claude Code session and
unaffected by restarting it.

```
scratchpad/stage3.sh      the script
scratchpad/stage3.log     its output
```

Steps, in order. Each prints `--- <label> :: rc=N :: <duration> ---` and the
script aborts on the first non-zero.

```
3a  indicators --lookback 8000 --workers 8      started 15:12:58, ~45 min
3b  backtest --phase compute --chunk-size 20    ~40 min
3c  backtest --phase finalize                   ~5 min
3d  stale-event sweep (inline SQL)
3e  backtest --phase harness                    ~4.5 h
3f  path backfill --config-hash f66729c7eda212a4
3g  stats rho
3h  stats cells --split-key train
3i  stats cells --split-key validate
3j  stats benchmarks --split-key train
3k  stats benchmarks --split-key validate       <- closes a gap owed from session 19
=== STAGE 3 COMPLETE ===
```

**`cscan nightly` is NOT in the script.** It is deliberately last and manual, at
the user's instruction. Run it after stage 3.

### Checking progress without the monitors

```
grep -E "^--- |^!!! |^=== " scratchpad/stage3.log
ps -ef | grep "[s]tage3.sh"
```

```sql
SELECT job, status, started_at, rows_written FROM runs
 WHERE started_at > now() - interval '6 hours' ORDER BY started_at DESC;
```

**Do not trust `rc=0` on the indicators step.** See the defect below. Verify:

```sql
SELECT count(bull_close_below_lower), count(*) FROM indicators WHERE interval='1d';
```

**Never edit `stage3.sh` while it runs** — bash reads a script incrementally and
editing a running one corrupts execution. An attempt to add a guard mid-run
failed anyway (Windows Python cannot open a `/c/Users/...` Git Bash path), and
it was correct not to retry.

---

## Backups taken before the rebuild

| Table | Rows | Why |
|---|---|---|
| `universe_pre_adr145` | 51,828 (5,736 in_trade) | membership before ADR 145 |
| `events_pre_adr145` | 783,762 | events before the rebuild |
| `run_ids_pre_adr145` | 39 | identifies stale rows afterwards |

**`events` must never be bulk-deleted for this config.** `path_event_id_fkey`
is `ON DELETE CASCADE` over **3,883,894** path rows, and `signal_reports`
references `events.id` — recreating rows reassigns serials and would orphan all
118 poller-session records, today's included. Step 3d instead deletes only rows
still carrying a pre-rebuild `run_id`, which by Ruling C4 are exactly those the
corrected population no longer produces.

**`--chunk-size 20`, not 25, on purpose.** `_chunk_already_done` keys on
`(config_hash, chunk, of)`; session 19's 40 clean chunks all match at 25 and
would be skipped, so the run would report success having done nothing.
Repartitioning invalidates those keys without deleting `runs` provenance.

---

## ADR 145 — market cap was priced on two different split bases

`mcap = shares * float(ind_row["close"])`. `close` is split-adjusted and Yahoo
re-adjusts the whole history on every new split; `shares` is the count as filed.
The two agree only while no split has happened since the filing.

AAPL 2011-06-30 priced at **$11.1B against a real ~$310B** — a factor of 28,
exactly 7:1 (2014) × 4:1 (2020). 446 of ~929 tickers carry splits.

Fixed by `core.universe.split_adjusted_shares`, which restates the filed count
onto the price basis using every split with `ex_date > filed_on` — **including
splits after `as_of`**, which is not look-ahead: market cap is split-invariant,
so the factor cancels and only agreement of the two bases matters.

Verified by recovering raw traded prices: AMZN **$1,893.63** on 2019-06-28,
SIRI **$3.95** mid-2016, JCI $42.60, VTR $64.10. All correct, including
spinoff-encoded ratios and reverse splits.

**Membership delta: 5,736 → 6,325 in_trade quarters (+10.3%).** Gains track
split size and recency (BKNG 25:1 → 29 quarters, ORLY 15:1 → 29, CMG 50:1 → 19).
Proportional gain decays 31% (2010) to 0.3% (2026). **13 quarters were lost**
across DD, SIRI, MAAS, VTR, JCI, MDGL — every one a **reverse split**, where
ratio < 1 correctly shrinks the restated count. 76 tickers carry reverse splits.
A prediction that membership could only increase was wrong for exactly this
reason.

`config_hash` is unchanged at `f66729c7eda212a4` — no config field moved.

---

## `INDICATOR_COLUMNS` dropped a column it computed

`cols = ["ticker","ts","interval",*INDICATOR_COLUMNS,...]` then `merged[cols]`.
ADR 144 added `bull_close_below_lower` to the registry, to `core/indicators.py`,
and to the table via migration `c3f91a70b8d4` — **but not to this list**. It was
computed for all 3.93M rows and dropped at the projection. A 23-minute
full-history recompute run specifically to backfill it would have exited 0
having written nothing to that column.

The only tell was `count(bull_close_below_lower)` sitting at exactly 0 while
`INSERT INTO indicators` was actively running.

`test_indicator_columns_persist.py` now pins it as an equality over the frame
that reaches the writer: everything `compute_all` produces is written, and
nothing is written that is not produced. A registry comparison does **not** work
— registry keys are function names (9 entries produce 21 columns), and only the
two close-confirmed flags have a function name equal to their column name, which
is exactly why one could be forgotten.

---

## ADR 122 is only half-applied, and this is a live decision

Two writers put rows in `events` on the same natural key with different
semantics:

| Hash | Writer | Events | in_trade true | in_trade false |
|---|---|---|---|---|
| `86e91448a65aa40b` | `run_events` | 1,731,328 | 557,125 | **1,174,203** |
| `f66729c7eda212a4` | `run_backtest` | 783,762 | 783,762 | **0** |

ADR 122 (2026-08-19) decided membership is a **column on the detection, not a
filter on it**, and applied it to `run_events`.
`research/candidates.py::apply_eligibility` still drops out-of-trade rows
(DESIGN §5.2 step 4), so under the current hash `in_trade` is a constant and
~68% of fired signals are never written.

Symptom the user hit: CHRW shows only July 2026 onward on the ticker page while
the chart shows band touches for years. That is ADR 122's own SMCI example
surviving in the writer it did not touch.

**Decision 2026-08-21: leave it.** Running `cscan events --lookback 6100`
(~4.5 h, single-threaded) would restore full detection history, but the user
declined for Phase 6 reasons — a model trained on periods where a name was not
tradeable would be learning from a population it can never act on. Not writing
the rows keeps that population unavailable by construction.

**Do NOT "fix" this by making `run_backtest` write out-of-trade rows.** Four
allowlist entries in `test_events_in_trade_filter.py` (`path_backfill`,
`path_labels`, `path_queries`, `path_reconcile`) are exempt *because* "the wider
detections have no entry price". The backtest prices everything it writes, so
those four would silently widen ~3x, and the backtest itself would triple in
runtime. The safe route, if ever wanted, is `cscan events` — which writes no
`entry_price`.

**If Phase 6 opens**, the durable fix is that the model's training query filters
`in_trade` explicitly with a test asserting it, so the table's contents stop
being load-bearing. Note also that Phase 6 is gated: the phase list requires any
decision to open it to cite ADR 112's measurement rather than route around it,
and this rebuild is that measurement.

---

## Smaller facts worth not rediscovering

- **Nightly was never scheduled.** `Get-ScheduledTask` has no entry; every run
  in `runs` was hand-typed. Definitions exist unimported at
  `scripts/tasks/{nightly,weekly,poller}.xml`. User: register later.
- **`runs.started_at` renders in UTC**, +7 from PDT. A `poll` row at 13:30:35 is
  the 06:30 PDT poller start.
- **The poller marks the day complete with a file, not a DB row.**
  `wait_and_poll.ps1` checks for `reports/poller/poller_session_<date>_*.csv`.
- **`cscan earnings` needs `--historical` and/or `--forward N`.** Nightly uses
  `historical=False, forward_days=90`.
- **11 bars were rejected 2026-08-21** by `open_outside_range` (UNH, EMN, MOH,
  PSX, TFX, TXT among them) — same-day auction print against the consolidated
  range, $0.005 to $0.69. Correct behaviour (invariant 4). The 19 cache entries
  keyed `_2026-08-16_2026-08-21` were deleted so nightly re-fetches settled
  data. AVB/EQR genuinely have no 2026-08-21 bar upstream; EA stops 2026-08-10.
- **`cscan backtest --phase harness`** was added this session so validation is
  reachable without a 5-hour unresumable block. Session 19 shipped
  `f66729c7eda212a4` with the harness never run against it.
