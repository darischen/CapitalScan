# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

**Audited 2026-08-24.** Three entries were deleted because they were
finished: the serving store's growth ceiling (closed by moving serving to a
Raspberry Pi and widening `history_years` to 30), `mcap_usd`'s two bad
inputs (ADR 146 for the x1,000 scale class; source-switching for the
ADR-ratio class), and the single-threaded harness (parallelised, 3h58m35s ->
48m21s). The reasoning behind each lives in the ADR that closed it, so
deleting the entry loses nothing.

# HIGHEST PRIORITY

## The Pi must be pulled LAST across a `config_hash` change

Written 2026-09-10 while sequencing the bull-reversal rebuild, before it
could bite.

`cscan poll` resolves config from the Pi's own checkout, so a Pi holding
new code writes events under the **new** hash. `serving_config` still pins
the old one until `cscan db sync-config` and `cscan sync` have run, and
`v_screen_live` filters on `current_setting('capitalscan.default_config_hash')`.
A Pi pulled early therefore writes rows the site cannot see, and **the page
goes blank with every job reporting success** -- the same failure CLAUDE.md
records for an ablation-arm config left on the Pi.

The order is not negotiable:

```
1. config change, then the full rebuild        (research machine)
2. ALTER DATABASE ... SET default_config_hash  (research)
3. cscan sync                                  (ships it, THEN pins it last)
4. cscan db sync-config                        (exit policy; pin again)
5. restart capitalscan-web, verify RENDERED rows
6. only now: git pull on the Pi
```

**Steps 3 and 4 were the other way round here until 2026-09-10**, when
that ordering blanked the live site -- `db sync-config` writes serving's
`serving_config` too, and so does `cscan sync`, which had it as table 5
of 15. Writing the pin before the rows arrive is the outage; it is now
the last table a sync writes. -> `OPERATIONS.md`

**Only steps 4 and 6 are time-constrained, and the distinction is easy to
get wrong.** The rebuild in step 1 writes *research*, which has no live
writer during market hours -- that is the whole point of the poller writing
serving instead. `cscan sync` is the step that writes serving and must not
overlap a live poller session. Steps 1-3 can run any time; step 4 waits for
the 13:00 close, and step 6 waits for step 5.

Recorded because the first pass at this sequencing held the *rebuild* back
for the poller, which costs half a day for no reason.

**The poller runs the day on old code against the generation serving
actually holds**, and picks up the new one after the close. Rushing the Pi
pull to "keep it in sync" is the mistake.

## Open

### Where Session 30 left off (2026-09-22/23) — read this first

Four things shipped, three of them measured rather than reasoned. All three
machines are at `d46ec80`.

**1. `cscan events` is 8.6x faster serial, 16.6x with `--workers`**
(2026-09-22, PR #82). Per-bar pandas overhead: membership re-resolved per
bar, close-confirmed flags assigned through pandas' missing-key insert path
(17.0 s of a 48.6 s profile), and the prior indicator row found by scanning
every date. Byte-identical output on both paths. Nightly's `events` step
went 3.5 -> 2.0 min in the wild. Full entry in *Closed*; timings in
`TIMINGS.md`.

**2. Regime-aware calibration is refuted** (ADR 199, PR #83). Item 3c's
proposal -- fit the reliability table separately above and below the
200-day line -- is **worse in both time directions** (mean |bias| 0.0150 ->
0.0185 and 0.0157 -> 0.0237), winning 10 of 36 field x cell x direction.
The cause is sample, not regime: the thin side carries `n_eff` 1,090
against the pooled 18,353. **Nothing on the 3c list is now untested**, and
the transition remains unexplained with no proposal on the table.

**3. The forward log was stalled and is not any more** (ADR 200, PR #86).
`cscan outcomes` had resolved **0 predictions on four consecutive nights**
with every nightly step reporting `ok`. `peak_labels` wrote labels for
`in_trade` rows; `path_backfill`, which produces the data it reads, prices
`(in_trade OR in_watch)`. One predicate of difference, and 2,767
predictions sat on events with a complete path, an entry price and no
label. Widening it resolved **1,350 on the first run** -- the log went
8,293 -> 9,643, +16% in one command.

**4. The bull close is badged like the bear one** (PR #85). Display only;
both types stay enabled and unchanged in the backend. `DESIGN.md` §11.2.

**What this session is a lesson about, and it happened twice.** Both real
defects were **two places that had to agree, with nothing connecting
them**: the reversal type list (excluded in two components, both naming the
bear side alone) and the label population (`in_trade` against
`(in_trade OR in_watch)`). Neither raised an error, because a job asked for
a narrower population and delivering it exactly is not failing. Both are
now tied together by a shared constant plus a test that names which side
moved -- `web/lib/format.ts::REVERSAL_TYPES` and
`test_label_scope_matches_path.py`.

**Continued 2026-09-24.** ADR 198's code removal landed (three days after
the ADR); the stale `cscan indicators` warning in CLAUDE.md was corrected
after surviving twenty days as a recorded-but-unfixed defect; **item 3b was
retired** as obsolete; and scoping it surfaced that **ADR 176's ranking
gate was never wired to a surface** -- see that item below.

**Open, small, and deliberate:**

- **22,967 unresolved predictions are `cosmetic = true` and will never
  resolve. That is correct.** They sit on events in neither universe
  (ADR 178), which are excluded from `path` on purpose -- 3,609,960 of them
  took `path_capture` from 97 s to over an hour on 2026-09-08. Verified
  2026-09-23: every row in that residual carries the flag. **Do not
  re-raise this as a forward-log gap.**
- **Nightly grows by ~5 minutes.** `peak_labels` goes 3.0 -> ~8 min at the
  widened scope (8m22s measured on the backfill run, which also swept
  history in one pass). Measured total is **49.9-51.0 min** across the last
  six 16-step nights, so expect ~55. Confirm against the 13:15 run before
  quoting it; one measurement is one regime. The dominant costs are
  elsewhere: `actions` 13.7 min and `shares` 10.1 min are half the run.
- **Item 7's completion date needs re-deriving from measured nightlies**,
  not from the prediction count. See that item for why the old estimate was
  wrong.

### Deferred from the slot-keyed adoption review (2026-09-20)

Six minors the branch's reviews found and deliberately did not fix. None
blocks adoption; each says what would make it matter.

1. **Nightly's unlinked counts cover the whole floor-scoped frame**, not the
   rows written that night, so the 100 legacy adopted rows report
   `collision` or `no_slot` on every nightly forever. Scope the counts to
   rows actually inserted, or date-bound the selection, before anyone learns
   to ignore the line.
2. **`slot_side` raises on an unknown `signal_type`.** With no date bound on
   selection, one bad serving row would fail adoption every night, hidden by
   nightly's broad `except`. Low risk while `core/cells.py` covers every
   `SignalType`; it bites the day a member is added without a side.
3. **The test fakes restate the adoption SELECT's columns** in
   `test_pull_predictions.py` and `test_slot_remap.py`, so a column added to
   the real query drifts silently. Derive both from one constant.
4. **`_apply_slot_remap`'s input guard omits `event_id`**, so a frame missing
   it raises `KeyError` rather than the guard's `ValueError`. Unreachable
   through `SELECT *`.
5. **The backfill takes no table lock.** A concurrent nightly can race it
   into a unique-index violation; the loser rolls back cleanly and a rerun
   converges. Documented as a run window instead: after a finished nightly,
   before the Pi's 06:45 session.
6. **The backfill replays the slot lookup once per unresolved row** to split
   `no_slot` from `ambiguous`. Fine for ~100 rows once; not for a hot path.

### Where Session 26 left off — read this first

**2026-09-05. The target was the problem, not the features.** The median
5-day return is unpredictable (SNR 0.100, ceiling +0.281%). `p_touch` --
the same already-fitted peak head, read through
`distributions.exceedance` -- is **calibrated, monotone and skilful**:
AUC 0.607/0.638/0.689/0.771 at the 2/3/5/10% thresholds, Brier skill up to
+9.33%, and `p_touch_3` deciles run 0.284 realised at the bottom to 0.755
at the top against a 0.516 base rate. **No retraining was involved.**

**Session 27 shipped items 1 and 2** (ADR 174 and ADR 175, 2026-09-05).
`cscan predict` fits, calibrates and writes `predictions`;
`handlers.predict` returns a real `Prediction` instead of `NotFound` for
the first time since Phase 5; the screener shows `P(+3%)` with its
interval; and the adverse half now exists as two more heads.

**ADR 175 did not use `events.mae`, and that was the decision.** `mae` is
adverse excursion *until the trade exits*, so `ExitParams` is baked into
it and every sweep of `stop_atr_k` would silently redefine the training
target. `trough_ret_{1,2,3,5,10}d` was added instead as the exact mirror
of `peak_ret_*`, computed from `path.adverse` (already side-adjusted).
Backfill: 489,914 rows in 67s, and peak/trough NULL patterns agree
exactly, so the training population did not change.

**Session 28 (2026-09-06) built the forward log and diagnosed the coverage
failures.** `cscan outcomes` resolves predictions against what actually
happened; 3,482 resolved on the first run. The coverage diagnosis refuted
the market-regime hypothesis and located the real cause. See `RESULTS.md`.

**Next, in cost order:**

Items 1 and 2 closed 2026-09-17: the arm comparisons each ran on a single label state, and `outcomes` now runs nightly (ADR 195, ADR 197). What follows is still open or recorded as refuted.

3. **~~Add market-level trend features~~ -- RUN 2026-09-07, AND IT DOES NOT
   FIX THE TRANSITION.** Index state plus breadth moves the target cell
   0.0778 -> 0.0550 and **triples the error everywhere else** (2023_above
   0.0199 -> 0.0608, heads passing 25/30 -> 16/30). The five index-state
   features do nothing alone (0.0778 -> 0.0771); breadth carried the whole
   gain, and breadth alone leaves the target cell at 0.0715. There is no
   combination here that fixes the transition without losing more than it
   gains. **Do not retry index-state features.** Full numbers in
   `RESULTS.md`.

3b. **~~Ship the two breadth features~~ -- RETIRED 2026-09-24, owner's
   call. Its headline benefit was obsolete one day after it was written.**

   The claim was that `breadth_ma_above` and `breadth_mean_dd` take 30
   heads from 25 passing to **26** and halve the 2023 error (0.0199 ->
   0.0085), with ALL-cell mean abs error 0.0262 -> 0.0225.

   **On 2026-09-08 -- the day after -- the gate was split by task family
   for the first time, and that inverted the priority.** `peak` (every
   `p_touch_*`) is **10/10** and `trough` (every `p_adverse_*`) is
   **10/10**; all four failures are `terminal` heads backing `q05..q95`,
   which ADR 172 has negative out of sample and which nothing displays. The
   base model already sits at 26/30. So 3b buys one more head in the one
   family that reaches no surface, on a metric already maxed out in the two
   families that ship -- against CLAUDE.md's own rule, "fix a displayed
   head before an undisplayed one".

   **It would not touch the thing that is actually wrong.** The live ~5
   point calibration bias is measured on `p_touch_3`, a `peak` head,
   against the forward log. RESULTS 2026-09-08 is explicit that this is "a
   different question from the coverage gate, and still open". Breadth was
   never measured against it.

   **What the scoping found, kept because it is the real cost.** The entry
   said "two columns on `events`, a backfill, and `RAW_FEATURE_COLS`". That
   is incomplete: `cscan breadth` is not in `nightly` (see the item below),
   `breadth_mean_dd` **does not exist** -- only `breadth_ma_above` and
   `breadth_chg_60d` do -- and the live path needs breadth resolved at t-1,
   because the poller writes events during a session whose breadth cannot
   be computed until its indicators are complete.

   **What would revive it:** breadth measured against Brier skill or the
   forward-log bias rather than against coverage. That is a different
   experiment, it is cheap now the forward log is unstalled, and it has
   never been run.

3c. **The transition is still unexplained, and the calibration fix is now
   refuted too.** The model can be told what the market is doing and does
   not convert that into a wider distribution at the top without
   over-widening everywhere. Open questions, none tested: is it capacity,
   too few transition-with-bad-outcome examples, or a real limit?

   **The per-regime calibration layer -- ADR 174's reliability table fitted
   separately above and below the 200-day line -- was MEASURED 2026-09-22
   AND IS WORSE.** One fit, two calibration schemes, both time directions
   on `capitalscan_hist`: mean |bias| 0.0150 -> 0.0185 calibrating on
   2022-23, and 0.0157 -> 0.0237 calibrating on 2024-26. ECE up both times,
   Brier flat, winning 10 of 36 field x cell x direction. The cause is
   sample, not regime: the below-the-line table carries `n_eff` 1,090
   against the pooled 18,353 and triples that cell's bias on its own.
   **Do not retry it**, and note the general form -- any partition of the
   calibration sample must show its gain net of the `n_eff` it costs, in
   both directions. ADR 199, RESULTS 2026-09-22,
   `scripts/hist/regime-calibration-2026-09-22/`.

   **It also does not touch the failing heads**, which is worth stating
   because the numbers read wider than they are. Reliability tables cover
   the six binary published fields only, and those already pass coverage
   10/10 by family. The four failures are `terminal` heads backing
   `q05..q95`. A regime-aware *quantile* adjustment is a different and
   untested thing, and ADR 172 has that fan negative out of sample.

   Measured as a 2x2 with the year held fixed, the regime separates by a
   factor of three within 2022:

   | | SPX above 200-SMA | SPX below 200-SMA |
   |---|---|---|
   | mean abs error, 2022 | **0.0778** | 0.0236 |
   | `terminal_h5_q0.25` | +0.2364 | +0.0713 |
   | `trough_h5_q0.25` | +0.1272 | +0.0055 |

   **The model fails during the transition, not during the bear market.**
   Once the index is clearly below its 200-day average the per-ticker
   features have caught up and coverage is inside tolerance. The failure is
   concentrated where the index is still above its average while the
   decline is underway and every per-ticker feature looks ordinary -- which
   matches 2026-08-25's independent finding that error was +0.118 for
   tickers down 0-5% and +0.039 for tickers down 25%+.

   Of 22 features only three are market-level: `vix_close` (a level),
   `spx_ret_1d` (**one day**), `cofire_count` (same-day breadth). Every
   trend feature is per-ticker.

   **The test:** add index-against-its-own-200-SMA, index drawdown from the
   252-day high, and days spent below -10%. One fit. `market_days` already
   holds `spx_close` back to 2004 and `vix_pct_252d` unused.

4. **Give the model decline-regime exposure -- lower priority than item 3
   now.** Still worth testing, but the 2x2 above says the model's problem
   is not that it lacks bear-market rows; it handles established bears
   fine. It lacks the ability to *recognise* one starting.

   **Verified 2026-09-06: `capitalscan_hist` has what is needed.** Events
   back to 2002-01-02 (6.7M rows), bars to 1998, and two declines worse
   than 2022:

   | year | frac above 200-SMA | in-trade events |
   |---|---|---|
   | 2002 | 0.071 | 23,867 |
   | 2008 | **0.000** | 19,808 |
   | 2009 | 0.575 | 14,465 |
   | *(2022, for scale)* | *0.151* | — |

   **But extending the window helps less than it sounds.** 2003-2007 is
   another ~200k mostly-uptrend events that dilute the declines:

   | window | events | frac uptrend |
   |---|---|---|
   | train 2010-2021 | 564,748 | 0.899 |
   | extended 2002-2021 | 809,905 | **0.844** |
   | validate 2022-23 (target) | 75,753 | 0.623 |

   The mix barely moves. **The number that does move is the absolute count
   of decline-regime examples: 57,085 -> 126,252, a 2.2x increase.** If the
   problem is that 57k is too few to fit regime-dependent behaviour, that
   matters more than the fraction. If the problem is the ratio, extending
   will disappoint.

   **2a. ~~Reweight first~~ -- MEASURED 2026-09-06, AND IT MAKES THINGS
   WORSE.** Three arms: base 4/30 heads failing (mean |err| 0.0230), x3
   7/30 (0.0267), balanced 6/30 (0.0311). The four terminal failures *grow*
   monotonically with the multiplier, +0.0737 -> +0.1018 at
   `terminal_h5_q0.25`. The training frame holds only **14,535 decline
   events against 143,403 uptrend**, so balancing needs a 9.82x multiplier
   that collapses the effective sample without adding information. **This
   is evidence for count over ratio. Do not retry it.**

   **Post-mortem, 2026-09-07: the experiment was aimed at the wrong
   rows.** It upweighted events with SPX below its 200-day SMA --
   exactly the rows the model already handles well (mean abs error
   0.0236). It multiplied the easy cases 9.82x and left the hard ones
   (0.0778, above the line) alone. So it was never a test of the regime
   hypothesis, and its failure says nothing about count versus ratio.

   **2a (superseded, kept so it is not retried).** Upweight decline-regime
   events inside the existing window and refit. It costs ~11 minutes
   against ~2 h for a rebuild, and it separates the two explanations: if
   ratio is what matters, reweighting moves coverage; if absolute count is
   what matters, it will not and 2b is required.

   **2b. ~~Rebuild on 2002-2021~~ -- RUN 2026-09-22, AND THE FALSIFIER
   FAILS. Count is refuted too.** Roughly doubling the decline examples
   (57,085 -> 126,252) moved the 2022 start-of-decline cell by 7-10% per
   family and left `trough` at 0.083 against a +/-0.05 tolerance. A second
   run then matched BOTH arms to production's window (train to 2026-03,
   validate 2026-03..2026-09) and the remaining gap collapsed to ~0.002 per
   family, inside seed noise, with skill slightly LOWER for the deeper arm.
   So yesterday's gain was training staleness, not depth. -> RESULTS
   2026-09-22, `scripts/hist/rerun-2026-09-22/`.

   **By this item's own falsifier, that ends the label-shift line**: 2a
   refuted ratio, 2b refutes count, and the text below says if neither moves
   coverage the cause is still unfound. Regime-aware calibration was the
   last idea standing and **it fell on 2026-09-22** (item 3c, ADR 199), so
   nothing on this list is now untested. The cause is unfound and no
   proposal on the table addresses it.

   **Falsifier for both:** coverage errors should shrink toward zero with
   no architecture change. If neither moves them, label shift is wrong too
   and the cause is still unfound. Measured 2026-09-06:
   every coverage failure follows from the label distribution moving
   between train and validate, with each sign forced rather than fitted.

   | quantity | train (2010-21) | validate (2022-23) |
   |---|---|---|
   | `peak_ret_10d` q75 | 0.0539 | 0.0710 (+32%) |
   | `fwd_ret_5d` q50 | 0.00366 | 0.00074 (-80%) |
   | `trough_ret_5d` q25 | -0.0362 | -0.0456 (26% deeper) |

   Train contains no period like it: worst year 2011 at 0.603 of sessions
   above the 200-day SMA against 2022's 0.151, and **2008 (0.000) is
   excluded because `ingest_start` is 2010**. `capitalscan_hist` (11 GB) is
   still on disk, built for exactly this and shelved after being judged
   against a different question -- that negative result does not transfer.

   **Checked 2026-09-10: it holds 2008, and does NOT hold 2000-2001.**
   `events` begin **2002-01-02** (6,749,793 rows to 2026-09-03); `bars`
   reach 1998-09-30 but events do not. So the 2008 decline -- the one this
   item needs, and the year `ingest_start` excludes -- is available, and
   the dot-com decline is only partly so. **A 2002-2021 rebuild is
   therefore possible as specified; a 1999-2021 one is not** without
   re-running events over the existing bars.

   Falsifier unchanged: refit on a window containing those declines and the
   coverage errors should shrink toward zero with no architecture change.
   If they do not, the label-shift story is wrong too.

   **Do NOT spend time on these -- all four are measured and refuted:**
   a market-regime feature (error is the same size above and below the
   200-day line), CRPS grid truncation (0.50% exceeds the top edge, `q0.75`
   sits at bin 9 of 32), volatility scale (`peak_h10_q0.75` misses in both
   2022 and 2023, opposite regimes), and multi-task interference (5/30
   heads fail multi-task, 5/30 fail single-task; shrinkage 4-15%, and
   `trough` is *better* shared). Bin resolution does not separate the
   families either: the q25-q75 body spans 3.7-5.0 bins for all six.

5. **Measure `P(stop)`, which is not `p_adverse_*`.** A trade can reach its
   target before its stop, so the two are not independent and
   `P(stop) != P(trough <= stop)`. The ordering is already in `path`, so
   this is a measurement over existing rows.
   `research.predict.expected_net_return` is written and takes both
   probabilities from its caller so it cannot pretend otherwise; nothing in
   the serving path calls it.

6. **Adopt arm D's config, with a caveat.** Sector-relative features plus
   `net_ret`/`mae` tasks beat base on Brier skill at all three thresholds
   (t3 +5.54% -> +6.22%) and do not interfere, **but AUC is flat**
   (0.6379 -> 0.6411), so it is better calibration rather than new
   predictive power, on one seed-triple each. Re-derive against the
   six-head model; the four-head numbers no longer describe the code.

7. **Refit the reliability tables on clean data.** Blocked until the
   forward log has accumulated enough resolved rows. The current intervals
   are fitted on validate and are a lower bound on the true uncertainty.

   **The old estimate here -- "roughly 2026-12 at ~15k events a month" --
   was wrong, and the way it was wrong is worth keeping.** It counted the
   prediction stream, but only the *labelled* population ever resolved:
   `peak_labels` wrote labels for `in_trade` rows while `predict` scored
   `in_trade` and `in_watch` both. Measured 2026-09-23: **8,293 rows had
   ever resolved and the current rate was zero**, with 29,354 predictions
   waiting and 0 resolved on each of four consecutive nights. ADR 200
   widened the label predicate to match path capture, which unblocks 2,767
   immediately and puts `in_watch` on the same footing going forward.
   **Re-estimate from the measured rate after a week of nightlies rather
   than from the prediction count** -- that substitution is what produced a
   date nothing supported.

### Command audit, 2026-09-24 — every CLI command checked for a caller

Prompted by `cscan breadth` turning out to be scheduled nowhere. All 49
commands were checked against the three chains, `scripts/`, and the docs.

**One dead command, now removed.** `cscan logs logs-tail` was the last
survivor of the 23 `NotImplementedError` stubs in `Session 0: Scaffold`
(2026-07-31); the other 22 were implemented over the following two months.
Its only live effect was advertising a capability in `--help` that raised
on any invocation.

**It did have an origin, and the origin is why it is safe to delete**
(owner, 2026-09-24): the Session 0 planning asked for *logging everywhere*,
and this command was that requirement's CLI shape. The requirement was then
met properly and somewhere better -- **`runs` holds every job's history in
the database**, queried directly and read by `cscan system-status`, and
`journalctl -u capitalscan-*` holds the process output with following and
time filtering a wrapper would not have had. So the stub is superseded
rather than abandoned: the thing it stood for exists, and nothing was lost
by removing the placeholder. **Deleting an unimplemented stub needs this
check** -- an empty command can be a forgotten requirement rather than dead
code, and the two look identical in the source.

**`cscan positions open/close/list` works and has never been used --
DECIDED 2026-09-25: keep it, and it now has a doc line.** Zero rows in
`positions`; the sibling `order_intents` seam is alive at 1,926 rows.

**What it is for, from ADR 073, which is the argument for keeping it:** the
trade log is *"a second forward record measuring **user decisions** rather
than model predictions, and comparing the two is directly informative about
whether the system helps."* `outcomes` measures the model against what
happened; this would measure **you** against what happened, and the
comparison answers a question neither record answers alone -- when you
overrode the model, were you right? ADR 074 already reserves
`compare_positions` as the tool that joins them.

**Why retiring was rejected on cost, not merit.** `v_positions` reads the
table (`db/schema.sql:1228`), and `test_v_positions_config.py` uses that
view to enforce an **unrelated** invariant -- that `serving_config` matches
live `ExitParams` (ADR 115). Retiring therefore means a migration dropping
a table and a view, amending ADR 048 and ADR 073, and **rehoming a live
test guard that has nothing to do with trade logging**. That is a lot of
moving parts to remove something that costs nothing dormant. Zero rows is a
usage cost -- it needs a trade logged by hand -- not a design fault.

**Five commands are outside every chain and script by design** and stay
that way: `scan`, `preflight`, `backfill`, `validate`,
`verify-indicators`. All are documented research or ops tools.

**No other command writes something nothing reads** -- the `breadth` class
of defect. Every remaining command feeds a chain, backs a surface, or
prints for a human.

**A false positive worth recording, because the method produced it.** The
first pass reported a command registered as `syn` rather than `sync`. That
was the audit script's own bug -- `"sync".rstrip("_cmd")` strips the
trailing `c`, because `rstrip` takes a character *set*. `cscan sync` is
correct and all 52 doc references are right. **Verify a tooling finding
against the tool itself before believing it**, which is the same lesson as
`verify-with-a-different-instrument`.

### ADR 176's ranking gate was built and never wired to anything

**Found 2026-09-24 while scoping item 3b, and the staleness is the symptom
rather than the defect.**

`cscan breadth` fills `market_days.breadth_ma_above` and
`breadth_chg_60d`. Measured on `wivie`:

| | |
|---|---|
| newest `breadth_ma_above` | **2026-09-01** |
| newest `market_days` row | 2026-09-23 |
| NULL sessions since January | **17** |

**Why it stopped: nothing ran it.** `cscan breadth` had exactly one
caller -- its own CLI command. It was in no chain: not `nightly`, not
`weekly`, not `monthly`. It updated when someone typed it, and nobody had
since 2026-09-01.

**FIXED 2026-09-24: breadth now runs in `nightly`, after `indicators`.**
Six seconds for the full 5,333-session history against a ~55 minute
nightly, so the cost does not depend on the gate ever being displayed. The
backfill run that day took the column current (latest 0.571, gate open).
**The scheduling half is done; the wiring half below is not.**

**Why nobody noticed, which is the part worth keeping.** `ranking_gate_open`
is called from exactly one place: `jobs/breadth.py`, to print a line in that
command's own report. Checked across `handlers/`, `mcp/`, `web/lib`,
`web/components`, `web/app` and the serving views:

- no handler reads it
- no MCP tool reads it
- no serving view has a breadth column
- `InferenceModal.tsx` names the 0.68 threshold **in a code comment**
  explaining why the modal exists. It does not read live breadth; the
  caveat text beside every number is static.

So ADR 176 shipped the classification, `GATE_OPEN_LABEL` /
`GATE_CLOSED_LABEL`, the reader-facing copy in `GATE_CLOSED_DETAIL`, a
sweepable `breadth_rank_floor`, a migration and a test suite -- and the
gate reaches no reader. `core/breadth.py` says the labels live there "so
the web copy, the MCP tool description and the CLI cannot drift apart";
two of those three consumers were never built.

**A column nobody reads does not announce that it stopped updating.** That
is the whole mechanism, and it is the third instance this week of the same
shape: a thing that is correct in isolation, connected to nothing, failing
silently. See also the label population (ADR 200) and the reversal type
list.

**ANSWERED 2026-09-25: the gate stays dormant, and it is not an
oversight any more.** With `cscan breadth` scheduled (2026-09-24) and the
forward log unstalled (ADR 200), the out-of-sample test ADR 176 asked for
became possible for the first time -- and it **refutes the ranking claim**:
AUC 0.6381 above the floor against 0.6108 below, on 9,656 resolved
predictions. Wiring it would print "Ranking unreliable" on days when
ranking is measurably fine. What really splits across 0.68 is the base rate
(0.5587 / 0.4042) and therefore calibration. -> ADR 176 amended,
RESULTS 2026-09-25.

**Kept for the record, because the reasoning was right even though the
conclusion moved.** The original question was:

- **If yes** -- ADR 176's own evidence is strong (AUC 0.6255 below the
  floor against 0.5154 above it, and the low band *inverts* there, so a low
  `p_touch` above the floor is not evidence against a name) -- then wiring
  it and scheduling the job land together, in one change. The staleness
  fixes itself as a side effect.
- **If no**, then ADR 176 is superseded in practice and should say so, and
  `cscan breadth` becomes a research command with no schedule, which is
  what it already is. Write that down rather than leaving a gate that looks
  live in the code and is not.

**Not urgent, and nothing is currently wrong for a reader**: no stale
number is displayed, because no number is displayed. The exposure is that
the next person to read `core/breadth.py` will reasonably assume the gate
is live.

### ~~`exit_reason = 'timeout'` covers two different facts~~ -- FIXED 2026-09-25

`ExitReason.UNFINISHED` (`'unfinished'`) is what `core.exits.resolve_exit`
now returns when the loop falls through on a window shorter than
`max_hold_days`. Fill and return are unchanged. DESIGN §5.5 carries the
rule. Measured on the live generation that day: 7,569 of 2,263,549
timeouts held fewer than five bars.
**Stored rows change when a backtest next rewrites them**: `nightly`
relabels recent events as their windows complete, and the next `weekly`
relabels the historical ones. No migration, because the value is computed
by `core/` and a SQL backfill would be a second exit implementation
(invariant 2). Original entry kept:


**Found 2026-09-08 from a user question about SPG.** A trade closed because
the forward data ran out is labelled `timeout`, identically to one that
held its full `max_hold_days = 5`. SPG shows five consecutive signals all
exiting on 2026-09-04 -- the last bar -- with holding days 1, 2, 3, 4 and 5.
Only the last is a real timeout.

**Measured: 1,455 of 788,718 timeouts have `holding_days < 5`, or 0.18%.**
Small, and worth stating plainly because an earlier note in this session
implied it contaminated `net_ret` broadly. It does not.

But those 1,455 rows carry a return computed over an arbitrarily truncated
window, and they are counted as completed trades in every statistic that
groups by `exit_reason`. A signal from yesterday sits in the same bucket as
one that genuinely ran five sessions.

**The fix is a distinct reason**, `unfinished` or similar, set when the exit
bar is the last available rather than the horizon. It changes no return,
only what the row claims about itself. Cheap, and it makes the count of
"real" timeouts honest.

**Related, already fixed:** the UI said `N/A: awaiting entry` for a
`next_open` event whose following session has not happened. That read as
"not computed yet" when it means the opposite -- the backtest looked and
correctly wrote nothing. Now `N/A: awaiting next open`.

### ~~`sync --incremental` cannot see backwards~~ -- FIXED 2026-09-25, ADR 201

**Built as a trigger-stamped `events.modified_at`** (migration
`e6b3d9a1f472`). The incremental sync now also ships any older event
changed since the previous `ok` sync, whichever job changed it. The
measurement that made it urgent: August signals carried 6,327
`peak_ret_10d` labels on research and 4,025 on serving, because
`peak_labels` writes past the seven-day overlap. **One full `cscan sync`
after deploying heals the rows rewritten before the trigger existed.**
The rule below no longer binds after that. Original entry kept:


**Found 2026-09-08 the hard way.** `_incremental_bounds` computes
`events_from` as `max(signal_date)` **on the serving database**. Serving
already held 2026-09-08, so an incremental sync shipped only events on or
after that date and skipped every earlier row the cosmetic backtest had
rewritten -- and would have skipped them on every future run too.

The symptom was not an error. `cscan sync --incremental` reported
"synced 118,340 rows" and exited 0, while serving sat at **699,402 events
against research's 10,823,948** and 550 predictions pointed at events
serving had never seen. On the page that read as an empty `Inference`
column: 62 of 63 rows showing "no inference".

**The rule: any job that rewrites events with a `signal_date` in the past
needs a full `cscan sync` afterward.** The cosmetic backtest is one.
`--phase finalize` is another -- it rewrote `cofire_count` on 10.8M rows,
almost all of them historical.

**Worth fixing properly rather than remembering.** Options, cheapest first:

1. **Watermark on `computed_at` rather than `signal_date`.** A rewritten
   row gets a new `computed_at`, so the watermark advances with the write
   rather than with the event's date. Needs the column indexed.
2. **Make `run_backtest` and `finalize` record the oldest `signal_date`
   they touched**, and have sync read that.
3. **Warn when the row counts diverge** by more than some factor. Cheapest
   of all and catches every variant, including ones not thought of.

Until one lands, the workaround is a full sync, which is ~14 tables from
the 1996 cutoff.

### Session 29 corrections -- read before trusting anything above

Five things were stated wrongly during this session and corrected by the
user. They are recorded because each was wrong in a way that would have
shipped:

1. **`in_watch` rows are backtested.** They always were --
   `research/backtest.py` reads both flags from `universe` and writes both,
   and 192,545 watch-only touch events carry entry prices. The "outside
   universe" label was a *display* bug: `v_ticker_events` never exposed
   `in_watch` (fixed, migration `d8c40a5b71e9`). No backtest re-run was
   ever needed.

2. **"N/A: watch universe" was also wrong.** Membership is never the reason
   a result is missing. `entry_date` is: no entry means the window has not
   opened, no exit means it has not closed. AAPL is in the watch universe
   *with* a -4.27% return on 2026-08-31.

3. **Live inference in the poller is NOT superseded by the button.** Both
   ship. The poller and `nightly` populate the column; the button covers
   arbitrary tickers and past dates.

4. **"Not backtested" on TS, RPRX, QCOM, ADM, EXPE, CIEN was a false
   alarm** -- all `in_trade`, all with entry prices, missing only an exit
   because the five-day window opened on 09-03/09-04. `weekly` missed
   nothing.

5. **Stochastic-only signals cannot be touch-backtested, ever.** No band
   level means no fill price. That is structural, not a gap, and no full
   backtest changes it.

**The one real data gap** is 4,407 touch events with neither flag --
outside the universe entirely for that quarter, never backtested. That is
the cosmetic-backtest item, and it is a scoped change to the backtest's
candidate query rather than a re-run.

**Agreed in session 29 (2026-09-08), not yet built.** In build order --
each depends on the one above it.

1. **Persist the fitted model and serve it from numpy.** ADR 174 refits
   every run so a fit cannot outlive the feature code that built it. A
   per-ticker button cannot wait 12 minutes, so the artifact has to be
   stored -- but keep the guarantee by stamping it with `git_sha`,
   `config_hash` and a hash of the ordered feature list, and **refusing to
   serve** when any disagrees with the running code. A stale artifact then
   fails loudly instead of scoring the wrong columns.

   **Export weights to `.npz` and do the forward pass in `core/` with
   numpy, not torch.** The model is a 3-layer MLP plus six linear heads --
   ~282k parameters, about 1.1 MB float32 -- and dropout is a no-op at
   inference, so a prediction is nine matmuls and a softmax. That keeps the
   2 GB ARM wheel off the Pi entirely and satisfies invariant 1, since it
   is arithmetic with no IO. Refresh **weekly**; a config change forces a
   refit through the hash check automatically.

2. **Rename `p_adverse_*` in the UI. "Moves 3% against" did not read
   clearly -- user's words, 2026-09-08.** Shipped as a placeholder in
   `MODEL_FIELD_LABELS`, not as a settled name. It is side-adjusted -- against a short is *up* -- so "falls
   3%" would be wrong half the time and wrong in the expensive direction.
   No better phrasing agreed yet; the current wording ships as a
   placeholder. `MODEL_FIELD_LABELS` in `web/lib/format.ts` is the one
   place to change it.

3. **The inference column.** Drop `STR`; tighten the Signal-to-bands gap;
   add an `Inference` column right of `Fired` whose cell is a small square
   with an ellipsis -- no number in the grid, which is what stops a reader
   eyeball-ranking a column the breadth gate says is not rankable. Clicking
   opens a modal with the full distribution, `p_touch_*`, `p_adverse_*`,
   the interval, `n_eff` and the gate tier. **Populated automatically on
   nightly and live poller runs.**

4. **Inference on the ticker/graph page**, for any searched ticker and any
   past report. Deliberate click rather than automatic. Same panel as the
   modal.

   **This SUPPLEMENTS DESIGN 7.9's live inference, it does not replace
   it** (corrected 2026-09-08). Both ship: the poller and `nightly` run
   inference over their own rows so the `Inference` column is already
   populated when the page loads, and the ticker page adds on-demand
   inference for tickers and dates neither job covered. The button is the
   arbitrary-lookup path, not a substitute for the batch one.

   **This is what makes item 1 mandatory rather than convenient.** The
   poller runs on the Pi. Live inference there means the Pi must score a
   model, and a 2 GB torch wheel on ARM is not the way -- the numpy forward
   pass over exported weights is.

5. **`v_screen` still filters `next_open`.** Harmless today -- every real
   query in `screen.ts` reads `v_screen_live` -- but it means the two views
   serve different entry conventions after ADR 177, and the next person to
   query `v_screen` directly will get the superseded model's rows.

6. **~~`breach_depth` should be deleted~~ -- DONE in ADR 177.** Removed
   from `DERIVED_FEATURE_COLS`, the `_breach_depth` function deleted, and
   `TestBreachDepthIsGone` asserts both. `docs/model_spec_adr170.json`
   records why under `features.removed`. **It was also a BUILD.md Phase 6
   deliverable** ("breach-depth features added after the base model
   exists"), which is now **void rather than pending**: the feature cannot
   exist under a touch entry. Worth an ADR note so nobody re-adds a
   look-ahead feature to satisfy a checklist.

**Three loose ends left by session 27**, none blocking:

- **~~`handlers.predict(ticker, as_of)` cannot name a side~~ -- FIXED
  2026-09-25.** Optional `side` argument (handler and MCP tool), filtered
  through the linked event, and the result now carries `side`. Verified on
  the workstation copy: PCG on 2026-08-28 holds a long and a short; with no
  side the handler returned the short silently, and each filter now
  returns its own row. Original text: `predictions`
  keys on `event_id`, because a name can fire a long and a short on one day
  and `p_touch` is directional. The handler takes a ticker and a date, which
  does not identify which, and returns the newest by `(as_of DESC, id DESC)`
  -- deterministic, not correct. Either add an optional `side` argument or
  return both. The screener is unaffected: it joins on the event.
- **~~`clear_predictions` does not clear serving~~ -- FIXED 2026-09-25.**
  `cscan predict --clear` now clears both stores, running every check on
  both before deleting from either. It refuses when serving holds Pi-born
  predictions research has not adopted (the only copy), whatever
  `drop_outcomes` says. Checked read-only that night: all 540 Pi-born
  live-generation predictions on serving were already on research.
  Original text: The foreign-key half was
  fixed 2026-09-08: it now refuses when predictions have resolved outcomes
  and names how many, instead of raising a raw `ForeignKeyViolation` the
  CLI swallowed. `drop_outcomes=True` is the deliberate override. **The
  serving half is still open**: `sync` copies `predictions` keyed on `id`,
  so clearing research and re-running produces new ids for the same event
  and the unique index on the serving side rejects them. Either clear both
  or teach `sync` to delete-then-copy for this table.

- **~~`cscan predict` is not in `nightly`, deliberately~~ -- REVERSED by the
  session-29 plan.** It has to be, and so does the poller: the `Inference`
  column is specified to be populated on load, which means both jobs score
  their own rows. What made it a workstation job was the 2 GB torch wheel
  and a 12-minute refit -- both of which the numpy weight export (item 1)
  removes. `nightly` will load a 1.1 MB artifact and do a forward pass, not
  fit anything.

- **Watch-universe rows never get a prediction, and that is no longer
  what is wanted.** `build_serving_frame` filters `in_trade`, so watch rows
  carry no `p_touch`. The session-29 plan wants them scored for display:
  they are already fully backtested -- AAPL sits in the watch universe with
  a real -4.27% on 2026-08-31 -- and ADR 122 withholds them from
  *statistics*, not from the page. **Scoring them is still extrapolation**:
  the model trains on `in_trade` only, and a name below its SMA200 is a
  different population. So the number is displayable but must be marked as
  out-of-population, not presented as equivalent.

- **~~`p_touch_2/5/10` are written but only `p_touch_3` is displayed~~ --
  addressed by the session-29 modal.** All six fields plus the quantile
  fan, the interval, `n_eff` and the gate tier go in the modal; the grid
  cell is a button with no number, which is what stops a reader
  eyeball-ranking a column the breadth gate says is not rankable.

- **The signal times the market; it does not select stocks.**
  Cross-sectional demeaning collapses SNR from 0.100 to **0.024** and the
  ceiling from +0.281% to +0.003%, because the signal *is* the shared
  market move. **It cannot be run market-neutral.**
- **Relative weakness is momentum, not reversion.** A name doing much worse
  than its sector has `net_ret` **−0.165%**; one holding up has **+0.166%**,
  monotone across quintiles. "Everyone is down but this one is down more"
  is a reason to avoid, not to buy.
- **The short book loses money**: `net_ret` −0.175% over 101,153 short
  events against +0.233% over 62,271 longs, with stops firing at the same
  rate (0.219 vs 0.215) but targets at half (0.116 vs 0.198). A strategy
  question, measurable independently of any model.

### Where Session 25 left off

**2026-09-04.** The holdout was spent (ADR 172) and the directional question
turned out to be mis-posed (ADR 173). Priorities, in the user's order:

1. **~~Side-blindness fix~~ — MEASURED, AND IT DOES NOT HELP.** `signal_type`
   is now a feature (23 features, two categoricals) and the A/B is done:
   `terminal_h5_q50` **+0.356% -> +0.111%**, `terminal_h10_q50` **+0.579% ->
   +0.061%**, coverage 17/20 -> 15/20. Both target heads went *down*, and
   the pre-registered prediction failed in the informative direction (peak
   fell more than terminal, the capacity signature rather than the mechanism
   one). Likely redundancy: `bb_pctb` near 0 with a low `k_full` already *is*
   a confluence-low. **The feature is kept anyway** -- free, faithful to the
   strategy, and its absence was a real defect in the spec. **ADR 172's
   retirement of the directional heads is now final.** RESULTS 2026-09-04 §4.

   **The ceiling was 0.281% all along**, computed rather than fitted:
   per-`signal_type` medians from train applied to validate, which is the
   best any predictor using that feature could reach at tau=0.5. Your
   hypothesis IS in the data and correctly ordered -- `confluence_low`
   +0.672%, `confluence_high` +0.226%, longs above shorts -- but the spread
   is 0.446 pp against a 5-day sd of 4.66%, a signal-to-noise ratio of
   **0.096**. **Do not re-open this by adding another directional feature**
   unless it carries information `signal_type` does not; the prize is
   bounded and small. RESULTS 2026-09-04 §5.

   **Why width is predictable and location is not** (§6): the terminal
   family is U-shaped on the holdout -- +7.49 / +0.32 / **−0.72** / +3.22 /
   +13.30 across tau. The model reads the *spread* of the signed return and
   not its *centre*, because volatility is autocorrelated and returns are
   not. **This does not mean the strategy has no edge**: asymmetric exits
   (5% target, ATR x2 stop) turn predictable dispersion into P&L without
   predictable direction.
2. **More history — measured, real, second priority.** RESULTS 2026-09-04
   has the numbers: 2022 coverage error falls 23% (0.0503 -> 0.0385) when
   train starts 2002 instead of 2010. Blocked from reaching production by
   the two data walls below.

**The dispersion model is the shipping checkpoint.** It held out of sample:
+7.71% mean improvement on 79,956 unseen events, coverage 17/20. What must
not ship with it is a directional midpoint -- `terminal_h*_q50` is negative
out of sample as currently specified.

**`capitalscan_hist` (11 GB) still exists — and is now KEEP, not
"drop freely".** Re-inspected 2026-09-10 during a disk cleanup.

Contents, measured rather than remembered:

| table | size | span |
|---|---|---|
| `events` | 5,655 MB | 6,749,793 rows, **2002-01-02 to 2026-09-03** |
| `indicators` | 2,502 MB | |
| `bars` | 1,431 MB | 7,230,415 rows, **1998-09-30 to 2026-09-03** |
| `path` | 1,151 MB | |
| `universe` | 22 MB | 99 quarters |

`config_hash 70b036b3660f21dc`, `crit_mcap` dropped, alembic
`e2c7a94b3d15` (production is `a7c2e9f4b105`, so its schema is stale).

**It answers the check item 2b above asks for.** That item says to confirm
the store holds 2008 and 2000-02 events before re-running anything. It
**holds 2008 and does not hold 2000-2001**: `bars` reach 1998 but `events`
begin 2002-01-02. The 2008 decline, which is the one item 2b actually
needs, is present.

**This line used to say "drop it freely, since `scratchpad/hist/*.sh`
rebuilds it". That is false and was the reason to check.** `scratchpad/`
does not exist, was never tracked in git, and those scripts are gone.
`dropdb capitalscan_hist` is therefore **irreversible** short of
re-deriving the scripts and re-ingesting 28 years of bars.

Nothing references it -- zero hits in code, zero in `.env.local`, no
process connects to it -- so "unused" is true and "disposable" is not.
11 GB against 344 GB free buys an open experiment that is no longer cheap
to reconstruct.

**Re-verified 2026-09-13, workstation Docker volume growth (28 GB -> 40
GB) traced.** Asked because the container volume grew visibly; the growth
is entirely in the live `capitalscan` database (26 GB: `events` 17 GB,
`path` 4.7 GB -- ordinary growth from ingestion and backtest writes, this
week's included) plus ~3.1 GB of `pg_wal`, both expected. `capitalscan_hist`
itself is unchanged: still 11 GB, still frozen (`max(path.computed_at) =
2026-09-04`, no write since), still the only source for the 2002-2021
window item 2b needs. Free space is now 355 GB (was 344 GB). Confirms the
KEEP decision above still holds -- nothing about today's growth touched it.

**Correction, 2026-09-14: "those scripts are gone" was false.** They were
never in the project repo, but they were never deleted either --
`build_events_chunked.sh` and the rest of the `scratchpad/hist/` toolchain
(`build_hist.sh`/`2`/`3`, `build_labels.sh`, `chain.sh`,
`chain_score.sh`/`2`, `score_arms.py`, plus `events_chunks_done.txt`, the
restart marker, and every build/arms/labels log from the original 2026-09-03
to 09-04 run) were sitting untouched in a past Claude Code session's own
temp working directory the whole time -- a location outside the repo that
nobody thought to check because it isn't part of the project. Found by
searching for the literal filename after being asked directly whether a
Claude Code scratchpad could hold it. Copied into the repo at
`scripts/hist/` (2026-09-14) so this stops being true.
`capitalscan_hist` is therefore reconstructable without re-ingesting 28
years of bars from scratch -- the KEEP decision above still holds (it is
still the only *currently populated* source for the 2002-2021 window, and
rebuilding costs real time), but "irreversible" no longer describes the
downside of losing it.

### Bugs found in flight and NOT fixed

- **~~`fetch_membership_changes()` returns a navigation box~~ -- REMOVED
  2026-09-24.** Wikipedia deleted the "Selected changes to the list"
  section, so `tables[1]` became a sector navbox returning 11 rows of
  garbage. Retired per the owner's 2026-09-04 call and ADR 198: the
  universe has expanded past the S&P 500 into NYSE, Nasdaq and ETFs, so an
  S&P-membership-changes scraper is vestigial. Gone now:
  `fetch_membership_changes`, `run_membership`, `UniverseFrozenError`,
  `is_reviewed`, the `cscan membership` command and
  `test_membership_freeze.py`. **`fetch_current_constituents` stays** --
  `run_tickers_refresh` needs it, and nightly never touched the removed
  path. `data/universe_union.csv` stays as the permanent record of the
  2010-2026 union. **The ADR was written 2026-09-21 and the code lived
  three more days**; a decision is not a deletion.

- **~~CLAUDE.md says `cscan indicators` "writes nothing until it
  finishes"~~ -- FIXED 2026-09-24.** Measured 2026-09-04: it writes
  incrementally (270k -> 614k -> 7.2M rows observed mid-run), because
  `run_indicators` moved to per-chunk writes on 2026-08-26. The stale
  warning survived **twenty days** after being recorded here as false,
  which is the part worth keeping: an entry that names a defect in
  another document does not fix it, and nobody re-reads the entry.

- **All model numbers before 2026-09-04 came from unseeded initialisations.**
  `_run` called `module_factory()` *before* `torch.manual_seed(seed)`, so
  seeds controlled only the batch shuffle. Fixed, with cuDNN pinned. **The
  ADR 170 validate figures (17/20 coverage) were measured under the bug and
  a re-run gave 14/20**, so the recorded spread understates run-to-run
  variance. `docs/model_spec_adr170.json` needs re-recording against the
  fixed code, and its "seeds" field currently implies a reproducibility the
  measurements did not have.

### Data walls that block the history work

- **`shares_outstanding` starts 2008-12-31** -- the SEC XBRL mandate floor,
  not a fetcher setting (`yahoo_shares_full` carries 181,662 rows with a
  NULL `period_end`: current shares, no history). `crit_mcap` is in
  `required_criteria`, so **nothing before 2009 can ever be `in_trade`** in
  production. The hist store works around this by dropping `crit_mcap`,
  which is why its numbers are internal to that store.

- **The pre-2010 universe is survivorship-biased and the bias runs the wrong
  way.** Lehman, Bear Stearns, Merrill, Wachovia, Fannie, Freddie, Ambac,
  MBIA and CIT are absent from `tickers`; only **18 of 1,561** rows carry a
  `delisted_on`. Training on that 2008 shows a bear market with the zeroes
  removed. **Symbol reuse is a live hazard**: `WM` is Waste Management
  today and was Washington Mutual until 2008.

- **Both walls are cleared by the same purchase** (Norgate ~$70-100/mo,
  Sharadar, or CRSP): historical fundamentals *and* delisted securities.
  The user's decision 2026-09-04 is **free sources only for now**, so this
  stays closed.

- **Tested 2026-09-22 with the free proxy, and it does not justify buying.**
  Re-running the 2010-vs-2002 training-window test on `capitalscan_hist`
  under today's model: adding 2002–2009 cuts the 2022 start-of-decline
  cell's error by only 7–10% per family, leaving `trough` at 0.083 against
  a ±0.05 tolerance. It clearly helps the downside family overall (`trough`
  0.0228 → 0.0146) and **hurts the headline `peak` family in the 2023 bull
  market** (0.0183 → 0.0253). Clean data would sharpen `p_adverse_*`; it
  would not fix the transition. → RESULTS 2026-09-22. The lever for the
  transition is regime-aware, not more years.

### Deferred by the 2026-09-04 pivot

**Both entries here shipped and are deleted.** The dispersion model
reached the site as `p_touch` (ADR 174), and the calibration caveat is
`core.calibration.MODEL_CAVEAT`, written into every `predictions` row
and rendered on the screener. The coverage-decay figures it carried
(2024 0.0182, 2025 0.0311, 2026 0.0480) live on in that constant.

- **Re-measure the ADR 170 baseline under the seeding fix**, since every
  published figure predates it.

### Operational, small

~~**Reserve DHCP leases**~~ — **done 2026-09-01.** All three reserved:
workstation 192.168.1.14, `wivie` 192.168.1.12, the Pi 192.168.1.30. The
addresses are written into configuration (the Pi's `pg_hba.conf`,
connection strings on every end), so a reshuffle would have broken the sync
with an error that reads like an auth failure.

**`cscan weekly` and `monthly` are still manual, and stay that way until
the cutover** (user's decision, 2026-09-01). `nightly` runs from Task
Scheduler on the workstation (13:15 daily, now through
`scripts/run_job.ps1`, which refuses if the resolved config hash is not the
serving one) and the poller from `capitalscan-poller.timer` on the Pi.
Neither `weekly` nor `monthly` has a Windows task and every `runs` row for
them was hand-typed.

**Deliberate rather than pending.** `monthly` is a universe-membership pass
and `weekly` is the backtest plus stats; a sweep is a `weekly` in all but
name, so they have been running whenever research ran. Both will be
`systemctl enable`d on `wivie` at the cutover, where the units already
exist. `scripts/install_schedule.ps1` would register them here and
deliberately has not been run.


~~**`core.exits.resolve_exit` builds three pandas Series per forward bar**~~
— **tried 2026-08-29, made it slower, reverted.**

The reasoning was that `path_metrics` had the same pattern and vectorising it
paid, so replacing `.iloc[i]` with `to_dict("records")` should too. Measured
on 3,940 real `resolve_exit` calls: **7.24s before, 8.26s after**, with
run-to-run noise around 0.6s. No improvement, possibly a small loss.

**Why the analogy failed.** `path_metrics` scans the *entire* forward window
every call, so building the rows once amortises. `resolve_exit` **breaks on
the first exit** -- mean holding is 4.0 bars of a 5-bar window, and most exits
land on bar 1 or 2. `to_dict("records")` materialises every row in the window
up front, so it builds five dicts to use two. Six cheap `.iloc` calls beat
that.

**The general lesson, which is the part worth keeping:** an optimisation that
pays in a full scan can lose in an early-exit loop, and "same pattern, same
fix" is a hypothesis rather than a conclusion. The profile said
`resolve_exit_for_entry` was 18.8s per ticker; it did not say the Series
construction was the expensive part of it, and I did not check before
rewriting.

`capitalscan/tests/unit/test_exits_row_access.py` is kept: it pins that dicts
and Series produce identical results across every branch of the DESIGN 5.5
order, which is worth having whether or not anyone optimises here again.

**If revisited**, profile inside `resolve_exit` first to find where the 18.8s
actually goes -- `mfe_mae`, the slicing in `resolve_exit_for_entry`, and
`_exit_on_bar`'s float conversions are all untested hypotheses.

Superseded text follows.

**`core.exits.resolve_exit` builds three pandas Series per forward bar.**
Measured 2026-08-28 while profiling compute after the `path_metrics`
vectorisation: `resolve_exit_for_entry` is 18.8s per ticker and is a thin
slicer, so the cost is inside `resolve_exit`'s loop, which does

    bar        = window.iloc[i]
    prior_ind  = ind_window.iloc[i - 1] if i > 0 else ind_at_entry
    own_ind    = ind_window.iloc[i]

`max_hold_days` is 5, so that is up to 15 Series per entry and roughly
111,000 per ticker at 7,428 entries. It is the same pattern
`path_metrics` had, tripled.

**The safe version keeps the logic untouched.** `_exit_on_bar` reads its
rows by string key (`bar["open"]`, `high`, `low`, `close`), so plain dicts
built once from numpy columns substitute exactly, at a fraction of the
construction cost. That preserves the pinned DESIGN §5.5 evaluation order
and the early `break`, which a fully vectorised rewrite would not.

**Do not vectorise the loop itself.** It terminates on the first exit and
the order of checks is the specification, not an implementation detail;
`core/exits.py` is the single exit implementation (invariant 2) and is
pinned by the property tests, including `mfe >= realized_return`. Any
change here is test-first per CLAUDE.md.

Expect less than the microbenchmark suggests: `path_metrics` was 11.5-27.5x
faster in isolation and 1.14x end to end. At 18.8s of a ~36s per-ticker
budget the ceiling is real but Amdahl-bounded.

**`signal_reports` has no `signal_type`, so `v_screen_live` matches on
`(ticker, signal_date)`.** Migration `d5e91a7c3b48` had to stop resolving
`fired_at` through `event_id`, because ADR 150's nightly sweep nulls that
column by design. The columns the sweep guarantees are `ticker`, `fired_at`
and `state_json`, and `state_json` carries no signal type — only `ticker`.

The consequence is small and real: a ticker firing two different signal
types on the same day gives both events the **same** earliest `fired_at`.
Exact today (all 157 linked events on 2026-08-28 had exactly one report),
and far better than the NULL it replaced, but wrong in principle.

~~**The fix is a `signal_type` column on `signal_reports`**~~ — **built
2026-08-29** (`a4c8d19f6e02`). The column exists on both databases and the
poller writes it from the next session. It is nullable and deliberately not
backfilled: `state_json` carries indicator state but no signal type, and the
events that would have supplied it are the ones ADR 150 deleted, so a guessed
value would be fabrication where NULL is true.

**One step remains**: `v_screen_live` still resolves `fired_at` by
`(ticker, signal_date)`. It should prefer `signal_type` where the column is
populated and fall back to the match where it is NULL. Deferred because no
row carries the value yet — the view has nothing to prefer until the poller
runs. Worth doing after Monday's session, when real rows exist to test
against.

Superseded text: the fix is a `signal_type` column on `signal_reports`, written by the
poller and matched on in the view. It is a schema change plus a poller
change plus a view change, which is why it is here rather than in that
migration.

**The workstation is 1.58x faster than the Flow X13 laptop, and the laptop
does not throttle.** Measured 2026-08-28 with `scripts/cpu_bench.py`, which
drives the real hot path (`bollinger`, `stochastic`, `atr`, `detect`,
`resolve_exit`) on synthetic bars for ten minutes at 8 workers, both
machines otherwise idle:

| | workstation (3700X, 65W, DDR4-3600) | Flow X13 (5950HS, 35W, LPDDR4X-4266) |
|---|---|---|
| steady | **2.138 units/s** | 1.352 units/s |
| sustain | 1.171 | **1.012** |
| one unit, cold | 3.8s | 5.8s |

Two independent measurements agree: the steady ratio is 1.58 and the cold
single-unit ratio is 1.53.

**The mechanism is power budget, not heat.** The prediction going in was
thermal decay -- a 13-inch chassis giving back its 4.6 GHz boost over a
two-hour arm. That did not happen: `sustain` 1.012 is flat across the whole
run, flatter than the desktop's own 1.171. The laptop is simply slower from
the first bucket, because 35W split eight ways cannot hold the all-core
clocks a 65W desktop does. Zen 3's IPC advantage is real and the power
envelope eats it.

**The laptop's first number was taken before its power plan was fixed, and
the corrected picture is more interesting.** Re-run the same day:

| | steady | plateau 150-480s | final 2 min | decay |
|---|---|---|---|---|
| laptop, before | 1.352 | 1.467 | 1.375 | 0.938 |
| laptop, after | 1.971 | **2.267** | 1.650 | **0.728** |
| workstation | 2.131 | **2.270** | 2.283 | 1.006 |

**At its plateau the laptop equals the workstation exactly** -- 2.267 against
2.270 -- and holds it for five or six minutes before falling 27%. The
workstation never falls. So the thermal decay predicted at the start does
exist; it was invisible in the first run only because the laptop was capped
so low it never got warm. Fixing the power plan traded "slow and flat" for
"fast then decaying", and for a 2h40m arm the floor is what matters. Ten
minutes does not establish the floor -- it was still declining in the last
bucket.

**The benchmark predicted the real workload well, which is worth recording
because it was not obvious it would.** `cpu_bench` deliberately drives
`core/` only, which performs no IO (invariant 1), while a real compute chunk
also reads bars and indicators from Postgres -- over the LAN, for the laptop.
The prediction was 1.58x and the measured chunk times came in at 1.42-1.62x
(workstation 90-97s, laptop 133-152s). The database reads did not dominate.

**One caution learned the hard way.** A dead runner looks exactly like a slow
one: the laptop's first launch was killed when its ssh session closed, and
the stale `runs` row -- `status='running'`, no process behind it -- read as a
chunk taking 361s and counting. That produced a confident "the laptop is 10x
slower on the real workload" which was entirely wrong. Check for a live
process before believing a duration, exactly as the `status='running'` note
in CLAUDE.md says.

**The Pi is 9.1x slower and cannot take even one arm.** Measured the same
way, 2026-08-28, 4 workers (it has 4 cores):

| machine | workers | steady |
|---|---|---|
| workstation 3700X | 8 | **2.138** units/s |
| laptop 5950HS | 8 | 1.352 |
| Raspberry Pi 4 Model B | 4 | **0.234** |

One arm is ~2h40m on the workstation, so ~24h on the Pi -- longer than the
whole rest of the sweep. Handing it a single arm moves the finish from ~17h
to ~24h, because the sweep ends when the *slowest* machine does. Both 5/4/1
and 6/3/1 are strictly worse than 6/4.

The intuition that a third machine is free parallelism is right in general
and wrong here: free only holds while the extra machine finishes inside the
others' runtime. It has 2.4 GB free as well, against 24-ticker chunks that
hold gigabytes on the workstation.

**What that means for splitting a sweep.** The laptop is worth ~63% of a
workstation. Balancing 10 arms gives roughly 6 to the workstation and 4 to
the laptop, for ~16h wall clock against ~27h on the workstation alone. It
needs no Postgres and no data copy -- point `DATABASE_URL_RESEARCH` at the
workstation over the LAN, since arms write disjoint `config_hash` rows and
cannot collide.

**`sustain` measured the wrong thing first, and the fix is the interesting
part.** It compared the last minute against the *first* and reported 1.75 on
a machine that was not throttling at all. `ProcessPoolExecutor` uses spawn
on Windows, so eight workers each pay an interpreter start plus
pandas/numpy imports -- about 90 seconds of ramp, all of it inside the
baseline. Any machine looks like it accelerates against that. The baseline
now excludes `--warmup` (default 120s).

### The coverage gate watches a family the product does not display

Established 2026-09-08 by the seven-year rolling test (`RESULTS.md`), run
twice and identical to four decimals.

Under the shipped configuration, split by task family:

| family | shipped as | heads within 5% |
|---|---|---|
| `peak` | `p_touch_2` / `_3` / `_5` / `_10` | **10/10** |
| `trough` | `p_adverse_3` / `_5` | **10/10** |
| `terminal` | **nothing** | 6/10 |

**All four heads the model fails are `terminal` heads.** The `terminal` head
backs only `q05..q95`, which ADR 172 established is negative out of sample
and which no surface displays. Every probability that reaches a reader comes
from `peak` or `trough`, and both are perfect.

That is the whole coverage failure: five hypotheses, four refutations, ADR
179, and two 22-minute runs chasing a miscalibration in the one family
nobody sees.

**The rule this leaves: weight coverage work by whether a head reaches a
surface.** A head that no view reads is worth fixing only after every head
that is displayed is already right. The aggregate "26/30 heads pass" hides
exactly this, and reading it as a single number is what kept the wrong
target in view for so long — split by family before drawing any conclusion
from it.

**Open question this raises and does not answer:** why the `terminal` head
alone is miscalibrated when it shares a trunk with the other two families.
Multi-task interference was tested and refuted earlier, but that test was
pooled across families and this result says pooling is exactly what hides
the signal. Worth re-running per family.

Not urgent. Nothing a reader sees is wrong because of it.

### The shipped probabilities run about 5 points low, and the cause is not the model

Measured 2026-09-08 on 4,020 resolved in-population forward-log rows
(`RESULTS.md` has the full table).

Ranking is sound: bucket `p_touch_3` eight ways and the realised rate is
**monotone across all eight**, 41.6% to 87.1%. Nothing crosses.

The level is not: the shipped value falls outside the bucket's **own** 95%
Wilson interval in most bands, always too low. **The exact count depends on
the partition** — six of eight with equal-width buckets, **four of eight**
with the equal-count buckets the shipped page uses, one of which sits 0.3pp
from its edge. `RESULTS.md` 2026-09-09 carries the reconciliation. The
direction is the durable part; the count is not.

The cause is a base rate that will not sit still:

| population | 3% touch rate |
|---|---:|
| train (2010-2021) | 35.1% |
| **validate (2022-2023)** — where ADR 174 fits the isotonic tables | **43.2%** |
| holdout (2024-2026) | 44.1% |
| last twelve months | ~49.5%, ranging **36.5% to 65.0%** |
| forward log (Aug-Sep 2026) | 57.1% |

**The month-to-month swing is larger than the model's entire Brier skill of
0.079.** So the ordering is the durable part of the output and the level is
not: expect understatement in a rising market and overstatement in a falling
one.

**What is ruled out.** ADR 179's rolling window was the obvious fix and is
refuted — it degrades `peak`, which is the family `p_touch_3` comes from.

**What must not be done.** Recalibrating on the forward log would correct the
level and destroy the only clean evidence the project has. ADR 179 forbids
it, and this measurement is why that rule is absolute rather than a default.

**What is shipped instead.** The modal says it in plain terms: "Use these to
rank signals, not as exact odds." Honest, and not a fix.

**Ideas, none tested.** A recency-weighted calibration sample that still
never touches the forward log. Conditioning the reliability table on a
regime feature such as the ADR 176 breadth reading, which is already
computed and already known to separate. Publishing the trailing realised
rate beside the model's number so a reader can see the gap themselves.

## Scheduled later

### Broker position sync — intended, and it collides with invariant 7

**Owner's intent, 2026-09-25:** hook up the Charles Schwab API later so
`positions` auto-populates from the account instead of being typed by
hand. That is the reason `positions` was kept rather than retired the same
day (see the entry above).

**The seam is already built and the schema needs no change.** ADR 048
point 2: *"`positions` table with a `source` column, valued
`user_declared` now and `broker_synced` later. All exit logic reads
position state from this table."* So this was anticipated from day one and
the column is waiting.

**What is NOT planned, and was unrecorded until now: the credential
conflict.** Invariant 7 reads *"No broker client, no order placement, **no
brokerage credentials**. The absence is the safety property, not a disabled
flag."* A read-only Schwab sync needs OAuth tokens, which are brokerage
credentials. **ADR 048 plans the seam and invariant 7 forbids the key**,
and nothing in the repo noted that the two disagree.

So this is not a "wire it up when convenient" task. It needs an ADR that
amends invariant 7 deliberately, along these lines:

- from *"no credentials exist"* to *"read-only credentials only, and no
  code path capable of placing an order"*
- the strong half stays and becomes **testable rather than asserted**: a
  test that no order-placing endpoint is referenced anywhere in the repo,
  the way `mcp/` is already pinned against importing `sqlalchemy`. The
  guarantee weakens from "we hold no key" to "the key is read-only and the
  code physically cannot trade", which is worth having only if it is
  enforced.
- confirm Schwab actually offers a read-only scope before relying on one.

**Settle authentication BEFORE real holdings land, not after.** The app is
public and unauthenticated by choice at
`https://capitalscan.tail397b3b.ts.net`. Today that is harmless: `positions`
is empty and **nothing in `web/` renders `v_positions`** (checked
2026-09-25). The moment the table holds real positions, any future
positions page publishes the portfolio to anyone with the link. Reordering
that after the fact is much harder than deciding it first.

**The practical unknown is token lifetime, not the API.** An unattended
nightly sync needs a refresh-token story; if Schwab's refresh cycle is
short, re-auth is the part that makes this annoying rather than the
endpoints. Check their current docs rather than any recollection here --
broker APIs move, and the Schwab API is itself the successor to TD
Ameritrade's.

### A serial `n_eff` — **scoped as ADR 166, deliberately not started**

**Ordering (user's decision, 2026-09-02): Phase 6 first, this after the
move.** It changes what a stored `cell_stats` column means and
`cell_stats`, `handlers/` and the serving views all read it — a
schema-adjacent change is the wrong thing to have in flight while the
research machine is relocating. It blocks nothing in Phase 6.

**The design shifted once it was measured.** The work was scoped as "add a
serial correction to match the cross-sectional one". The measurements say
the serial axis is the *tighter* of the two — cluster size caps at 6 under
`max_hold_days = 5`, CV 0.46 — while the axis already corrected runs 1 to
185 with CV 0.85 and is collapsed to `k_bar = mean(cofire_count)`. So the
heterogeneity problem is in what ships, not in what is missing, and the
answer is one estimator covering both axes rather than a second scalar.

ADR 166 has the full case, the two rejected alternatives, and the free
validation that decides it (cluster-robust intervals on every fire against
today's cluster-head intervals — agreement retires the filter with
evidence, divergence means one is wrong).

**One cheap prerequisite, worth doing before Phase 6 builds on it.** The
2026-08-29 cluster-size finding — a ~300 bp spread, the largest effect
measured anywhere here — rests on a 10+ fire bucket that **does not exist**
in the measured population, where clusters cap at 6. It was computed on a
population mixing entry kinds. Re-run through `research/returns.py`.

## Closed, refuted or answered — do not reopen

## DONE 2026-09-22: `cscan events` is 8.6x faster serial, 16.6x with `--workers`

Was the top item. Kept as the record of what the time actually was, because
the first lead was wrong and the second was not where anyone expected.

**Measured on `capitalscan_hist`, 20 tickers over 2002–2026**, the same
52,683 rows and the same fingerprint `bafaf852fc869334` every run:

| | seconds | vs original |
|---|---:|---:|
| original | 337.9 | — |
| serial, after | 39.4 | **8.6x** |
| `--workers 8` | 20.3 | **16.6x** |

A 5-ticker run confirms the same output against the original code
(`e6c4958fb12ee9bf`, 19,850 rows) on both paths.

**Where the time went**, from `py-spy` on the worker rather than reasoning.
The worker sat at 105% of one core with **no active Postgres query in 60
samples over 5 minutes**: CPU-bound in Python, not waiting on the database.

1. **Membership resolved per bar.** `in_trade` and `in_watch` each masked
   and sorted the whole `universe` frame, and `run_events` called both for
   every bar — 106,275 rows scanned twice per bar.
   `core.universe.membership_for` resolves one ticker's timeline once and
   answers by bisect; `in_trade`/`in_watch` delegate to it, so there is
   still one implementation and ADR 129's fail-closed contract is unchanged.
2. **Close-confirmed flags assigned inside the loop.** `bar[field] = ...`
   on a *new* label takes pandas' missing-key insert path: **17.0 s of a
   48.6 s two-ticker profile across 24,832 calls**. Now one vectorised pass
   per ticker, same source rows (ADR 108).
3. **The prior indicator row found by scanning every indicator date per
   bar.** The dates are sorted, so it is a bisect.
4. **`--workers`**, spawn-mode processes across tickers. Tickers are
   independent — the debounce key is `(ticker, signal_date, bound)` — so
   nothing crosses between them. Workers open their own connections and read
   their own slices; **the parent's reads moved inside the serial branch**,
   which is most of what held 8 workers to 1.3x. Rows are sorted before the
   write so parallel and serial send the same rows in the same order
   (ADR 060). **Default stays 1**: the nightly's five-day window is not
   worth process startup.
5. **Progress output** via the house `track`, so a long pass is no longer
   indistinguishable from a hang.

`detect`'s signature probe was not widened — it still reads only `low`,
`high`, `ts`, `ticker` from the bar and one indicator row (TESTS §3).

**A wrong lead, recorded so nobody repeats it.** One snapshot showed
Postgres running `UPDATE events ... WHERE e.run_id = $1` and neither store
indexes `events.run_id`, so an index was added to the throwaway
`capitalscan_hist` (`zz_events_run_id`, built in 18 s). **It changed
nothing**: the next chunks took 1,128 s and 1,180 s. The per-chunk fill-in
UPDATE is not the bottleneck. A `run_id` index on production is therefore
not worth a migration on this evidence.


Removed from this file on 2026-09-21 because each is built, refuted, or
answered. The full entries are in git history (`git log -p -- docs/BACKLOG.md`);
the reasoning that matters lives where each line points.

**Refuted or answered — the reason these stay listed is to stop a re-run:**

- **ADR 179's rolling refit window** — built, tested and refuted 2026-09-08; ADR 193's expanding window replaced it. → ADR 179, ADR 193
- **Phase 6 refinement, all four candidates** — tried and refuted 2026-09-02. → RESULTS 2026-09-02
- **Cluster size as a feature** — real, and not usable (2026-09-02). → RESULTS
- **Coverage 17/20** — superseded 2026-09-06/07 by the family split: `peak` and `trough` are 10/10, only the undisplayed `terminal` head fails.
- **Depositary listings' pre-2018 history** — the proposed fix would fabricate numbers (2026-09-02). Do not backfill.
- **123 tickers with no sector** — mostly not equities; not a gap to close.
- **675 tickers' `next_open` positions after a full `weekly`** — out-of-universe rows; no scheduled job may touch them (2026-09-15). → OPERATIONS
- **`wivie` behind on migrations** — a `pg_restore` carries schema and data together; never `db migrate` a store you are about to restore. → TIMINGS, cutover
- **Pi-only operation after the workstation goes away** — obsolete 2026-09-01; the workstation stays the heavy-research box.

**Built or fixed:**

- Reaching the app from off the LAN — Tailscale Funnel on `wivie` proxying to the Pi, live 2026-09-20 at `https://capitalscan.tail397b3b.ts.net`. → OPERATIONS
- The forward log calibrated on its own outcomes — predictions are insert-only. → ADR 195
- Serving and research minting one id range; the Pi's predictions adopted into research. → ADR 196
- Adoption keyed on the label, so every adopted row went unlinked — now keyed on the debounce slot. → ADR 197
- The weekly refit learning nothing from fixed splits — expanding window. → ADR 193
- `cscan predict --publish`; weekly publish checks the config hash (2026-09-17/19).
- The exit-invariant "flake" — a real sub-tick counterexample; bounds compare at 4 decimals. → TESTS §3.4
- The quantile-fan gap — one pre-writer run, not the CDF (2026-09-17). → RESULTS
- Dead `bars`/`indicators` joins in the feature frame — removed 2026-09-17.
- Nightly slot 16:30 → 13:15, and no Saturday nightly (2026-09-17/19).
- `predictions` upsert vs natural-key view, and `event_id` surviving a sync. → ADR 191, ADR 192
- `nightly` fetching the published artifact (2026-09-10).
- Path capture paused mid-flight on 2026-09-08 — resumed by every nightly since.
- Nightly's trading-day guard, the research poll path's sequence guard, `run_sync` overwriting `events.id`, research-machine portability, the `max_hold_days` sweep, counting every fire, ETF `mcap_usd`, the `wait_and_poll` harness — all 2026-09-01. → ADR 163, ADR 165, OPERATIONS
