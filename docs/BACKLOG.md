# Backlog

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

---

## ~~`predictions` upserts on `event_id`, but the view reads a natural key~~ — **resolved 2026-09-10, ADR 191/192**

The cause is gone. `Remap` rewrites `event_id` into the target's id space at
sync time, so the reference is valid on serving and the **existing** UNIQUE
index on `event_id` does the deduplication for free. `_clear_remap_collisions`
resolves the one real collision this creates, between the sync's `id`-keyed
writes and the Pi's `event_id`-keyed ones.

Verified on serving after the first production run of the new path:
19,705 of 19,705 `event_id`s resolve **and point at their own event**, 0
duplicate natural keys, `v_screen_live` unchanged at 163 rows.

Two things deliberately left as they are:

- **The natural key is still not UNIQUE**, and should not be.
  `next_open` and `touch` are genuinely two events for one signal — KO on
  2026-08-24 carries `p_touch_3` of 28.2% and 39.6% for exactly that
  reason. The uniqueness that matters is on `event_id`.
- **The view keeps its `LIMIT 1` lateral.** It is now belt-and-braces
  rather than the fix. Removing it would make the page depend on an
  invariant no query enforces at read time, which is the shape that
  produced this in the first place.

---

## `cscan predict` fits an artifact and never publishes it

Found 2026-09-10 during the bull-reversal cutover, by the Pi being unable
to score.

`artifact.publish()` is called in exactly one place: the `weekly` chain
(`cli.py:3467`). A standalone `cscan predict` writes `data/model/predictor.npz`
locally and stops there, so `model_artifact` on serving keeps naming the
previous generation. The Pi's `predict --serving --from-artifact` then
refuses on a config mismatch -- correctly, and confusingly, because the
refit it is complaining about *did* happen.

ADR 185 put the publish in `weekly` deliberately: the refit belongs to the
weekly cadence and `nightly` only scores. That reasoning is about *when to
refit*, not about *who may ship the result*, and a manual `predict` across
a `config_hash` change is exactly the case it does not cover.

Worked around by hand this time:

```
uv run python -c "from capitalscan.jobs import artifact as a, sync as s; print(a.publish(s.serving_engine()))"
```

Options, cheapest first:

1. `cscan predict --publish`, defaulting off. One flag, no behaviour
   change for the scheduled path.
2. Publish whenever `predict` refits at all, on the argument that an
   unpublished artifact has no reader. `model_artifact` conflicts on
   `config_hash`, so this cannot clobber the generation being served.
3. Leave it, and put the manual command in the cutover runbook.

(2) is the honest one: the only reason a fit stays local today is that
nobody wrote the line. The risk it raises is publishing a fit made from a
half-built generation, which argues for (1).

## The bull reversal has no close-confirmed half

**Accepted as-is on the live-badge question (user, 2026-09-09):** the badge
freezing at fire time is fine, because `live reversal` and `reversal` are
two different claims and a reader seeing "live" knows it is a statement
about a moment that may have passed.

**That reasoning holds for the bear side and not for the bull side**, which
is what is still open.

`SignalParams.enabled_signal_types` carries `bear_close_above_upper` and
**not** `bull_close_below_lower`:

```
bb_lower_touch, bb_upper_touch, stoch_oversold, stoch_overbought,
confluence_low, confluence_high, bear_close_above_upper
```

So a bear reversal that develops after its signal fires is caught the next
morning by the close-confirmed badge. A bull reversal that develops after
its signal fires is caught by nothing — the live badge froze at fire time
and no solid badge will ever appear. EXPE on 2026-09-09 is the worked
example: it fired 09:46 at 265.12 against a 266.95 open, below its band but
still below its open, crossed above the open later, and displays nothing.

`bull_close_below_lower` already exists as an indicator
(`core/indicators.py:208`), an enum value (`core/types.py:40`), a signal
rule (`core/signals.py:295`) and a frontend label — it is dormant, not
missing. The badge branch for it is written and tested.

**The cost of enabling it is the part needing a decision.**
`enabled_signal_types` is a hashed field, so adding to it **moves
`config_hash`**: a new serving generation, a full backtest rebuild (~2h),
and `cell_stats` recomputed. That is not a display change, and it is the
reason this is a decision rather than a one-line edit.

---

## `test_stop_exits_land_at_or_beyond_the_stop_level` failed once and would not reproduce

2026-09-09, during the four-gate run for ADR 188. It failed in the combined
`unit + property` run with coverage on, then passed on:

- the single test, replayed (hypothesis stores falsifying examples, so a
  real counterexample should have come back)
- the whole property tier alone, 29 passed
- the identical combined command, twice, 3,092 passed

Nothing in that change touches exits, config or the resolver.

**Worth chasing rather than shrugging at.** `TESTS.md` §3 names the exit
invariants as one of the five tests carrying the correctness load, and a
gate that fails one run in four teaches everyone to re-run it. The two
candidates are a hypothesis deadline tripped by coverage instrumentation
(which would be a test-harness problem, not a code one) and genuine
cross-test state leaking under `-p no:randomly`.

The evidence to get next time it happens: the full `Falsifying example`
block. It was lost to a `tail` on the first run and never came back.

---


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

## Open

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

1. **~~Check whether two identical fits agree~~ -- mostly answered
   2026-09-07, downgrade.** The base arm ran twice across the market-state
   experiments and gave **identical** steps [566, 417, 589] and identical
   25/30 both times. Both were after the label backfill; the differing pair
   ([426,426,467] vs [521,512,469]) straddled it. That points at the labels
   moving, not at framework non-determinism -- the killed `path backfill`
   ran `incomplete_only=False` over 300 tickers and the label pass rewrote
   979,828 rows. Not proof, but enough to stop treating every A/B as
   suspect. **What remains: re-check any A/B whose arms straddled a label
   backfill.** ADR 172's holdout run and the arm A/B/C/D comparison should
   be checked against their run timestamps. Measured 2026-09-06: two
   fits with identical code, identical `DEFAULT_SEEDS` and an identically
   sized frame (157,938 rows) gave steps [426, 426, 467] against
   [521, 512, 469], and 5/30 heads failing against 4/30.

   Two candidate causes, unseparated: seeding that ADR 173 did not fully
   close, or the intervening label backfill having moved train labels (the
   killed `path backfill` ran `incomplete_only=False` over 300 tickers and
   the label pass rewrote 979,828 rows).

   **Until this is settled, every single-run arm comparison in
   `RESULTS.md` carries unquantified variance** -- including ADR 172's and
   the arm A/B/C/D result whose whole margin was 0.7pp. Fit twice, compare
   step counts and coverage. One fit's cost to know whether any A/B in this
   project means anything.

2. **Re-run `cscan outcomes` and read it. Free, but it has a chain in
   front of it.** Measured 2026-09-06: the resolver is idempotent and
   correct, and it resolved nothing on its second run because the labels it
   needs were not there. The dependency, which nothing documented:

       events -> backtest (writes entry_price) -> path backfill
              -> extremum labels -> outcomes

   `path_backfill` scopes to `entry_price IS NOT NULL`, and `entry_price`
   is written by the backtest. The last backtest ran 2026-08-30, so of the
   125 unresolved predictions **112 have a NULL `entry_price`** and cannot
   be pathed, labelled or scored until one runs again (~2 h). The other 13
   have partial paths whose forward windows are genuinely still filling.

   **Predicting before `entry_price` exists is correct**, not a bug: the
   prediction is made at signal time and the entry is a later fact. Only
   resolution waits.

   So the forward log advances at the pace of `nightly`, not of the clock.
   Put `cscan outcomes` at the end of that chain once it has been watched a
   few more times by hand. It is idempotent and
   already installed. Every day it runs, more predictions resolve on data
   nothing has iterated against. It is the only uncontaminated measurement
   in the project, so check it before trusting any other number. Put it in
   `nightly` once it has been watched a few times by hand.

3. **~~Add market-level trend features~~ -- RUN 2026-09-07, AND IT DOES NOT
   FIX THE TRANSITION.** Index state plus breadth moves the target cell
   0.0778 -> 0.0550 and **triples the error everywhere else** (2023_above
   0.0199 -> 0.0608, heads passing 25/30 -> 16/30). The five index-state
   features do nothing alone (0.0778 -> 0.0771); breadth carried the whole
   gain, and breadth alone leaves the target cell at 0.0715. There is no
   combination here that fixes the transition without losing more than it
   gains. **Do not retry index-state features.** Full numbers in
   `RESULTS.md`.

3b. **Ship the two breadth features anyway -- they are a net win for a
   different reason.** `breadth_ma_above` and `breadth_mean_dd`, computed
   from `indicators` in 3.7s, take 30 heads from 25 passing to **26** and
   **halve** the 2023 error (0.0199 -> 0.0085). ALL-cell mean abs error
   0.0262 -> 0.0225. That stands on its own and does not depend on the
   transition story. Needs: two columns on `events`, a backfill, and
   `RAW_FEATURE_COLS`. Note it makes 2022_below slightly worse
   (0.0236 -> 0.0377), so confirm the gate still passes 26/30 before
   adopting.

3c. **The transition is still unexplained and unfixed.** The model can be
   told what the market is doing and does not convert that into a wider
   distribution at the top without over-widening everywhere. Open
   questions, none tested: is it capacity, too few transition-with-bad-
   outcome examples, or a real limit? A per-regime calibration layer
   (ADR 174's reliability table fitted separately above and below the
   200-day line) would sidestep the model entirely and is cheap.

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

   **2b. Rebuild on 2002-2021 -- now the justified test.** 2a refuted the
   ratio explanation, which leaves count: 57,085 -> 126,252 decline events
   is information reweighting cannot fabricate.
   `capitalscan_hist` (11 GB) is on disk and was shelved after being judged
   against a different question, so that negative result does not transfer.

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

   **Check `capitalscan_hist` actually holds 2008 and 2000-02 events before
   re-running anything.** Falsifier: refit on a window containing those
   declines and the coverage errors should shrink toward zero with no
   architecture change. If they do not, the label-shift story is wrong too.

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

7. **Refit the reliability tables on clean data.** Blocked until item 1 has
   accumulated enough resolved rows -- roughly 2026-12 at ~15k events a
   month. The current intervals are fitted on validate and are a lower
   bound on the true uncertainty.

### ~~ADR 179 is decided but not built -- the rolling refit~~ — **wrong; built, tested and REFUTED 2026-09-08**

**This entry was stale for two days and cost real time on 2026-09-10.** It
was read as an open task and work started against it; only reading ADR 179
itself stopped the build. The ADR carries two amendments this entry never
picked up, and the second withdraws the exact clause described here.

What actually happened: `roll7` against `fixed` **inverted which task family
works** -- `terminal` 6/10 -> 10/10, `peak` 10/10 -> 6/10, `trough` 10/10 ->
8/10. Every shipped probability reads `peak` or `trough`; `terminal`
displays nowhere. The aggregate, 26/30 against 24/30, concealed it.

**What survives ADR 179 and already ships:** the forward log is never
trained on, weekly rather than nightly (ADR 184), a refit means refitted
reliability tables, and newly closed labels enter training only after
serving as forward-log evidence.

**The lesson for this file:** an entry describing an ADR must not outlive an
amendment to it. Check the ADR before treating any entry here as open.

### The weekly refit currently learns nothing, and the fix is untested

**Measured 2026-09-10.** This is the real open item the entry above hid.

`split_key` is assigned at event creation and never moves (invariant 5), so
the fixed bounds mean the weekly refit trains on identical rows every week:

| split | events | range |
|---|---:|---|
| train | 1,815,728 | 2010-01-05 -> **2021-12-31** |
| validate | 374,869 | 2022-01-03 -> 2023-12-29 |
| holdout | 516,615 | 2024-01-02 -> 2026-09-09 |

**516,615 events since 2024 never enter training**, and the reliability
tables are equally frozen on 2022-2023. The refit differs only by seed.
ADR 184's split of refit-from-score is right; the refit half is a no-op in
information terms.

**`roll7` does not settle this, because it changed two things at once:**

    fixed   train 2010-2021 -> 12 years, validate 2022-2023
    roll7   train 2019-2025 ->  7 years, validate 2026

It moved the window forward **and cut it by 42%**, then scored a different
period. The loss was attributed to recency; a shorter fit is the other
explanation, and it had already produced one false result in this same test
when a five-year window fell through to `DEFAULT_STEPS`.

**The untested option is an expanding window** -- keep the 2010 start, move
only the end. More recent data *and* more of it, where `roll7` traded one
for the other. Running 2026-09-10 with the control the first test lacked:
`fixed_v26` is today's training window scored on 2026, so the window and the
validation year can finally be separated. `scripts/rolling_window_test.py`.

Adoption, if it wins, is `config_hash`-neutral -- a training-time filter
slicing on `signal_date`, never a rewrite of `split_key`. Editing
`SplitParams.train_end` would move the hash and is the wrong lever.


### `exit_reason = 'timeout'` covers two different facts

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

### ~~`predictions.event_id` cannot survive a sync~~ — **fixed 2026-09-10, ADR 191**

Both proposed fixes landed, in the order the entry recommended.

**(1) Join on the natural key** — shipped 2026-09-09 (`f7d3a02e5c18`).
`predictions` gained `signal_type` and `entry_kind` and the views join the
same five columns `events` syncs on. That restored the display.

**The deeper defect it left behind, and the entry did not see it.** Fixing
the *join* left the *column* wrong, and a wrong column that resolves is
worse than one that does not. Measured on serving 2026-09-09, before ADR
191:

| | |
|---|---:|
| serving predictions | 20,200 |
| `event_id` matching no event | 7,403 |
| `event_id` matching the **WRONG** event | **3,035** |

PRGO's 2026-08-05 prediction pointed at an SMTC event from 2020-07-13;
NRG's at PKX from 2018. Those links resolve and join cleanly. Nothing read
the column after the natural-key join, which is the only reason it was
harmless — **a dangling id is findable with one outer join; one that
resolves to the wrong row is invisible to every check that asks whether it
joins.**

**ADR 191 remaps the reference at the sync boundary** rather than shipping
or nulling it, and the existing UNIQUE index on `event_id` then deduplicates
for free. Verified after the first production run: **19,705 of 19,705
resolve and point at their own event, 0 duplicate natural keys**,
`v_screen_live` unchanged at 163 rows.

Option (2), preserving `events.id` through the sync, stays rejected for the
reason given: it collides with the poller's own inserts. Option (3),
computing predictions on serving, shipped separately as ADR 181/184/185 and
now runs on the Pi in seconds — the two coexist, which is exactly why the
collision handling in ADR 191 was needed.


### `sync --incremental` cannot see backwards -- a full sync is required after any historical rewrite

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

### Paused mid-flight, resumable (2026-09-08)

**`cscan path capture` is checkpointed and stopped at 1h53m.** Not a
failure. ADR 178's cosmetic pricing gave 3,609,960 out-of-universe events
an `entry_price`, so `path_capture` saw 1,411 tickers of work instead of
the usual handful and would have taken days -- while blocking `finalize`,
`outcomes` and tomorrow's nightly.

**The checkpoint is in the data, not a file.** `fwd_window_days` is written
per event as each ticker completes, and the query skips anything already
carrying it. Killing between tickers loses nothing.

| | |
|---|---|
| events with a forward window | 2,218,130 |
| tickers remaining, scoped (`30915c5`) | **535** |
| tickers remaining, unscoped | 1,409 |

**Resume with `cscan path capture` whenever.** It picks up exactly here and
now does 38% of the original work, because commit `30915c5` scopes all
three `FROM events` reads in `path_backfill.py` to `(in_trade OR
in_watch)`. Cosmetic rows keep their entry price for display and get no
forward path -- `path` feeds the labels that feed the model, so a display
concession must not reach it.

**Do it after the current chain**, and in slices rather than one run: it is
pure catch-up with nothing downstream waiting.

**Still to decide: bundle `--cosmetic` into `weekly`** (user's call,
2026-09-08). Weekly already runs the backtest, so cosmetic pricing belongs
there rather than as a one-off -- but only alongside the scoped path
pipeline, or every weekly re-creates this hour.

**Measured 2026-09-08, and the guess above was close: `wivie` is 3.41x the
workstation, not 1.58x.** `scripts/cpu_bench.py` at 8 workers, ten minutes,
machine otherwise idle:

| | cores / threads | steady | sustain |
|---|---|---:|---:|
| workstation (3700X, 65W) | 8 / 16 | **2.138 units/s** | 1.171 |
| Flow X13 (5950HS, 35W) | 8 / 16 | 1.352 | 1.012 |
| **`wivie` (i5-7200U, 15W)** | **2 / 4** | **0.627** | **1.111** |

`sustain` above 1.0 means it got *faster* over ten minutes: no thermal decay
at all, which is what a 15W part with almost no boost headroom does. The
constraint on this machine is core count, not heat, and no amount of cooling
changes it.

**Worker count barely matters there, which is worth knowing before anyone
tunes it.** Eight workers on two physical cores oversubscribes 4x and costs
almost nothing:

| `wivie` `--workers` | steady | sustain | vs workstation |
|---|---:|---:|---:|
| 8 (the documented default) | 0.627 | 1.111 | 3.41x |
| 4 (its thread count) | **0.660** | 1.025 | 3.24x |

**5.3%.** Not worth changing the systemd units for, and worth recording so
the next person does not spend an afternoon tuning `--workers` expecting
more. A 2-core box is core-bound whatever you ask of it; the fix is a bigger
box, not a better flag. Quote **3.2-3.4x** and do not pretend to more
precision than that.

**The 1.58x in this file is a different laptop and must not be applied to
`wivie`.** The Flow X13 is an 8-core 5950HS. `wivie` has **two** physical
cores. Anything quoting 1.58x for the Debian laptop is wrong by better than
a factor of two.

So the cosmetic backfill is ~9h x 3.41 = **~31 hours** of continuous work,
inside the 36-54h guess but still not a two-day-and-forget job.

Projected from the workstation budgets at 3.41x:

| job | workstation | `wivie` |
|---|---|---|
| `nightly`, cold | 35-40 min | **~2h-2h20m** |
| `weekly` | ~36 min | **~2h** |
| `backtest --workers 8`, full universe | ~2h | **~6.8h** |
| `bars --hourly --backfill` | 4.5-5.5h | **~15-19h** |

`nightly` and `weekly` still fit an overnight window, so the cutover holds
for the scope it actually covers. The full backtest does not, which is
already the rule -- CLAUDE.md keeps arms and heavy research on the
workstation permanently.

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

- **`handlers.predict(ticker, as_of)` cannot name a side.** `predictions`
  keys on `event_id`, because a name can fire a long and a short on one day
  and `p_touch` is directional. The handler takes a ticker and a date, which
  does not identify which, and returns the newest by `(as_of DESC, id DESC)`
  -- deterministic, not correct. Either add an optional `side` argument or
  return both. The screener is unaffected: it joins on the event.
- **`clear_predictions` does not clear serving.** The foreign-key half was
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

**`capitalscan_hist` (11 GB) still exists** and holds the extended-history
store: bars back to 1998-09-30, events 2002-2026, `config_hash
70b036b3660f21dc`, `crit_mcap` dropped. Rollback is `dropdb
capitalscan_hist`. Keep it if the history work continues; drop it freely if
not, since `scratchpad/hist/*.sh` rebuilds it.

### Bugs found in flight and NOT fixed

- **`fetch_membership_changes()` returns a navigation box, not the changes
  table.** Wikipedia deleted the "Selected changes to the list" section, so
  `tables[1]` is now a sector navbox and the function returns 11 rows of
  garbage. Its one caller is `run_membership()` (`cscan membership
  --backfill`), which raises a clear error rather than corrupting anything,
  and **nightly is unaffected** -- `run_tickers_refresh` uses only
  `fetch_current_constituents` and the SEC CIK lookup. The user's decision
  (2026-09-04) is to **retire it**: the universe has expanded past the S&P
  500 into NYSE, Nasdaq and ETFs, so an S&P-membership-changes scraper is
  vestigial. Not done; needs an ADR because ADR 035 leans on it.

- **`cscan events` has no `--workers` and no progress output.** A single
  pass over 2002-2026 ran **4h40m at ~99% of one core and wrote nothing**,
  because it accumulates and upserts once. There is no way to distinguish
  20% done from 80%, and a failure at hour nine loses everything. The
  workaround that worked is ticker chunking
  (`scratchpad/hist/build_events_chunked.sh`, 25 chunks of 60, each writing
  on completion, with a done-file for restarts). **Chunk by ticker, never by
  date**: `cofire_count` is the one cross-ticker feature and
  `cscan backtest --phase finalize` computes it separately, which cli.py
  says "cannot live inside a resumable per-chunk loop".

- **CLAUDE.md says `cscan indicators` "writes nothing until it finishes".**
  Measured 2026-09-04: it writes incrementally (270k -> 614k -> 7.2M rows
  observed mid-run). The warning is stale and misleads anyone diagnosing a
  slow run.

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

### Deferred by the 2026-09-04 pivot

**Both entries here shipped and are deleted.** The dispersion model
reached the site as `p_touch` (ADR 174), and the calibration caveat is
`core.calibration.MODEL_CAVEAT`, written into every `predictions` row
and rendered on the screener. The coverage-decay figures it carried
(2024 0.0182, 2025 0.0311, 2026 0.0480) live on in that constant.

- **Re-measure the ADR 170 baseline under the seeding fix**, since every
  published figure predates it.


### ~~Coverage is 17/20~~ -- superseded 2026-09-06/07

**Deleted rather than updated, because every number in it was about the
four-head model.** At six heads the gate reads 25-26 of 30, and the failure
was diagnosed: the label distribution moves between train and validate
(`peak_ret_10d` q75 0.0539 -> 0.0710, `fwd_ret_5d` q50 0.00366 -> 0.00074),
with each coverage error's sign forced by that shift. Five candidate causes
were tested and four refuted -- market regime (Simpson's paradox in the
first attempt; the real 2x2 shows 0.0778 above the 200-day line against
0.0236 below), CRPS grid truncation, volatility scale, and multi-task
interference. Full record in `RESULTS.md`.

**What matters now is not the gate but the product**: `p_touch` is
calibrated in every regime (bias +0.0000) and only *ranks* in some, which
ADR 176's breadth gate handles. The quantile fan the gate scores is not
displayed anywhere.


## ~~Phase 6 refinement~~ — **all four tried and refuted, 2026-09-02**

Every item was built and measured the day the section was written. None
improved the result. RESULTS 2026-09-02 has the tables; the summary:

| # | idea | outcome |
|---|---|---|
| 1 | volatility-scaled baseline | **vacuous** — the scaled constant has *higher* pinball loss on all 20 heads, up to 3.6x, so it is an easier bar and passing it means nothing |
| 2 | volatility-normalised targets | **no effect** — `peak_h10_q50` coverage error is −0.0509 either way; the tree was already doing the scaling |
| 3 | low-based breach depth (ADR 069) | **+0.087% / +0.025%** on the directional heads against baselines of −1.08% / −2.04%. Real, negligible. Shipped as a feature anyway |
| 4 | two-stage, dispersion then direction | **+0.000035 / −0.000012**, noise |

**One prerequisite had to be fixed first and it invalidated the earlier
numbers.** The fits were unseeded with `bagging_fraction` and
`feature_fraction` at 0.7, so identical code gave 17/20 and 18/20. Item 3's
first pass read as a −0.25% regression; seeded, it is a +0.087%
improvement. The sign flipped on noise. Now seeded and deterministic.

**They fail in two distinct ways, and both are informative.** Items 2 and 4
fail because the information was already present — a tree's splits capture
volatility scaling, and predicted dispersion is a function of features the
directional model already has. Item 3 fails the opposite way: it *is* new
information (correlation with `bb_pctb` is only −0.054) aimed squarely at
direction, and it still buys under a tenth of a percent.

**What stands.** The directional heads lose to a constant — `terminal_h5_q50`
−1.13%, `terminal_h10_q50` −2.29% — after every refinement. The coverage
failure is irreducible from train data: once the model absorbs the
volatility change, a level shift remains that nothing fitted on 2010-2021
can anticipate.

**Three independent findings agree**: ADR 112 (no cell survives FDR), check
5 (no directional head beats a constant), and these four attempts. Phase 6
can close on that.

**Still not done, and deliberately.** The holdout (2024-2026, 82,957
events) is untouched and spent once, at the end. Nothing is promoted —
ADR 067's failure path leaves the incumbent serving and there is no
incumbent, so `handlers/predict.py` keeps returning `NotFound` and the
`predictions` table stays empty. Writing it would put model output on the
site, since `v_screen_live` already joins it.

---

### Depositary listings have no pre-2018 history — **investigated 2026-09-02: the proposed fix would fabricate numbers**

Not a wrong number — a missing one, and the distinction matters when
coverage is quoted.

`is_depositary_listing` (a name predicate, `"american depositary"`, not a
column) switches the share *source* to Yahoo rather than scaling an
ordinary-share count, which is what closed the ADR-ratio class (NTES from a
$1,666.9B peak to $82.1B; zero depositary rows above $1T). Yahoo's
`shares_full` series starts around 2018, so the early years are unpriced.

**Re-measured 2026-09-02**, against a `universe` that has since gained
`config_hash` (so the row counts are larger than the "360 of 970" this
entry used to quote):

    22 depositary tickers
    6,480 of 17,460 universe rows have no mcap_usd   (37%)
    first priced row  2016-12-31
    shares_outstanding: yahoo_shares_full 2015-11-05 .. 2026-09-01
                        sec_xbrl          2011-03-31 .. 2026-08-05

**The proposed fix was to derive the ADS ratio from
`dei:EntityCommonStockSharesOutstanding` against the Yahoo ADS count and
extrapolate it backward. Measured, that is not safe.**

The ratio is computable wherever both sources overlap. Per ticker, across
that window (SEC filings matched to the nearest Yahoo observation within 45
days):

| ticker | n | median | min | max | CV |
|---|---|---|---|---|---|
| NTES | 8 | 4.989 | 4.879 | **25.000** | **0.931** |
| HTHT | 8 | 9.779 | **0.512** | 10.032 | **0.722** |
| TCOM | 7 | 0.986 | **0.126** | 1.032 | **0.567** |
| RYAAY | 8 | 5.013 | 2.002 | 6.477 | **0.368** |
| PDD | 5 | 3.923 | 1.524 | 4.000 | **0.312** |
| LI | 7 | 1.875 | 1.751 | 2.000 | 0.058 |
| VOD | 5 | 10.026 | 9.453 | 10.337 | 0.035 |
| ONC | 9 | 12.484 | 12.077 | 13.288 | 0.033 |
| ARGX | 9 | 0.993 | 0.927 | 0.997 | 0.028 |
| NICE | 10 | 0.996 | 0.964 | 1.045 | 0.024 |
| BNTX | 8 | 1.000 | 0.955 | 1.018 | 0.019 |
| SIMO | 8 | 3.962 | 3.956 | 4.059 | 0.009 |
| ABVX | 3 | 0.999 | 0.991 | 1.000 | 0.005 |
| ARM | 3 | 1.000 | 0.993 | 1.000 | 0.004 |
| BLTE | 4 | 0.984 | 0.980 | 0.986 | 0.003 |

15 of 22 tickers have a usable overlap. **Ten are stable. Five are not**,
and the instability is not noise — NTES runs 4.99 to 25.0 and HTHT 0.512 to
10.03, which are ratio *changes*, the thing an ADR does when the depositary
bank restates the receipt.

**The reason this kills the approach is sharper than "a third are
unstable".** The ratio can only be *measured* where both sources exist,
which is 2018 onward. It is *needed* where only SEC exists, which is before
2018. So the extrapolation is unverifiable exactly in the region it would
be applied, and the tickers most likely to have changed ratio during the
unobserved years are the ones already observed changing during the
observed ones.

Applying a constant median backward would give NTES a market cap wrong by
up to 5x across its early history, silently and plausibly — the same defect
class ADR 146 closed for the x1,000 scale errors, reintroduced by a
correction. A missing value is correct under invariant 4; a fabricated one
is not.

**What would actually work**, and none of it is available here: a source
publishing ADS ratio-change *events* with effective dates, so the ratio is
carried rather than inferred. Failing that, the honest position is the
current one.

**So this stays open as a coverage caveat, not as work.** State it aloud
when quoting ADR coverage: 37% of depositary universe rows carry no market
cap, those names are absent from the trade universe before ~2017 rather
than present at a fabricated size, and that absence is survivorship-relevant
in the direction of understating early coverage.

---

### 123 tickers still have no sector — **mostly not equities; not a gap to close**

**Measured 2026-08-27, because this reads like easy data-fetching work and
is not.** Of the 123:

    123   no sector
    101   no CIK at all -- not SEC filers
     29   is_active
     27   appear in `universe` under the serving config
      4   are `in_trade`

**The active ones are preferred shares and baby bonds**, which is why the
lookup fails: AQNB (Algonquin), BEPJ/BIPH/BIPJ (Brookfield), BNH/BNJ,
CMSA/CMSC/CMSD (CMS Energy), DUKB (Duke). A preferred share **has no GICS
sector** -- GICS classifies the issuer's equity, and these are fixed-income
instruments wearing an equity ticker. They also have an empty `name`, so
the ticker refresh never resolved them as securities either.

Filling these means inventing a classification the instrument does not
have, which invariant 4 forbids in the same breath as forward-filling a
null. **The real question is scoping, not fetching**: whether preferreds
and baby bonds belong in the universe at all. That is a decision, and it
belongs in an ADR rather than in a backfill script.

The remainder are the delisted and renamed names the original entry
describes, and those genuinely 404.

#### Original entry

ADR 148's backfill resolved 254 of 352. The rest are delisted or renamed —
YHOO, FB (now META), PCLN (now BKNG), TWTR, ATVI, CERN, FRC, SIVB — and
Yahoo 404s on them.

**None reaches the training population**, so this blocks nothing. It stays
recorded because the training frame raises on a missing sector by design
(ADR 147), so a future ticker that fails to resolve will stop a build, and
whoever hits it should find this rather than rediscover it.

---

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

---

### ~~Pi-only operation, after the workstation goes away~~ — **obsolete 2026-09-01, the premise is gone**

**This entry was contingency planning for a scenario that did not
happen**, and it is kept only for the hardware measurements, which are
still good.

Two things killed the premise:

- **A working replacement was found and staged.** `wivie` (Debian 13,
  native PostgreSQL 17.11, SSD, DHCP-reserved at 192.168.1.12) takes the
  scheduled research role. The Pi never has to run `nightly` or `weekly`.
- **The workstation is not going away.** It leaves the *scheduled* role at
  the cutover and stays the heavy-research box — backtests, sweeps,
  rebuilds — because it is the faster machine. The move relocates the
  house, not the hardware.

So the USB SSD, the `PGDATA` relocation, the memory tuning and the
weekly-vs-monthly cadence question are all moot: **research never lands on
the Pi.** The Pi keeps exactly the role it has, serving plus the poller,
and its 27 GB free is no longer a constraint on anything.

**The measurements below stay useful** and are the reason this is not
simply deleted: the Pi is 9.1x slower than the workstation on the real hot
path, which is what proves it could never have taken a sweep arm, and the
`sustain`-measured-the-wrong-thing lesson applies to any future benchmark
here.

#### Original entry

The workstation is leaving (house move, planned). The Yoga 900 that was to
replace it **will not power on and takes no charge**, so the fallback is the
Raspberry Pi 4 running everything: nightly, weekly, the poller, and the site.
By then Phase 6 should be finished and nothing else should need a rebuild.

**CPU is not the problem.** Measured 2026-08-28 with `scripts/cpu_bench.py`
(see the benchmark section above): the Pi steadies at 0.234 units/s against
the workstation's 2.138, so **9.1x slower**. Applied to jobs measured on the
workstation:

| job | workstation | Pi, projected |
|---|---|---|
| `nightly` (mostly network-bound fetches) | 37 min | **~1.5-2h** |
| `weekly` = `run_backtest` compute + finalize, no harness | ~2h | **~18h** |

**The budget is two days, not overnight** (user's decision, 2026-08-28), and
under that budget neither figure is a constraint at all. An 18-hour weekly
started Saturday morning is finished Saturday night with a day to spare.
Speed was never what blocks this migration, and an earlier reading of this
section that treated the Pi as marginal on time was wrong.

A second candidate appeared the same evening: a Lenovo IdeaPad Flex 4-1580,
i5-7200U (**2 cores / 4 threads**, 15W), 8 GB RAM, Samsung 850 EVO. Perhaps
4-6x slower than the workstation rather than 9.1x, so a weekly around 8-12
hours. **Unmeasured** — run `scripts/cpu_bench.py` on it before quoting that,
because two of this session's hardware estimates were wrong until measured
(the Flow X13's throttling, and `path_backfill` over the LAN).

Its advantage over the Pi is not speed. It is 8 GB of RAM against 3.8, and an
SSD instead of an SD card.

**Storage is what blocks it.** Measured the same day:

    research database        24 GB   (events 13 GB, path 6.3 GB,
                                      indicators 2.3 GB, bars 1.7 GB)
    Pi free space            27 GB   on the SD card
    serving DB already there  5.4 GB
    external storage         none attached

24 GB into 27 GB leaves ~3 GB, before WAL, before vacuum's temp space, and
before the growth every nightly adds. A weekly writing ~2M events plus path
rows would fill it. And a write-heavy 24 GB database on an SD card is the
wrong medium twice over: random-write IOPS and write endurance.

**A USB SSD is a prerequisite, not an optimisation.** It fixes the capacity
and the medium at once. Being procured 2026-08-28: an 850 EVO salvaged from
the dead Yoga plus a USB-C enclosure, about $10. **That single purchase
unblocks either candidate**, which is what makes the machine choice a
secondary question rather than the deciding one.

**Memory is the untested risk.** 3.8 GB total, ~2.4 GB available. `cpu_bench`
ran 4 workers happily, but each of its units is one ticker of synthetic bars;
a real compute chunk loads 24 tickers of full history plus indicators *per
worker*. Expect to need `--workers 2` and a smaller `--chunk-size`, which
pushes weekly past the 18h projection. This needs measuring, not predicting.

**Do the migration test before the move, not after.** While the workstation
still exists: attach the SSD, restore a dump onto the Pi, run one real
`weekly` end to end, and watch memory and free space. If it OOMs or fills the
disk, that is a fixable afternoon with a working machine in the room. After
the move it is a dead system with no fallback.

**Consider running weekly monthly instead.** `weekly` refreshes backtest
labels on newly ingested events (`cli.py::weekly` docstring; the harness is
deliberately skipped). With Phase 6 done and no active research, relabelling
monthly costs a stale `exit_reason` on the most recent few weeks of events
and nothing else — and it turns "a whole day, every week" into "a whole day,
occasionally." Cheaper than any hardware.

**Checklist, in order:**

1. USB SSD attached, `PGDATA` moved onto it, `postgresql.conf` following it.
2. `pg_dump`/restore of research to the Pi, and confirm 24 GB landed.
3. One real `weekly`, timed, with `free -m` and `df -h` sampled throughout.
4. Tune `--workers` / `--chunk-size` from what that run shows.
5. Decide weekly-vs-monthly cadence from the measured duration.
6. Register nightly and weekly as systemd timers, the way the poller already
   is (`scripts/pi/capitalscan-poller.timer`), rather than Task Scheduler.
7. Re-point `DATABASE_URL_RESEARCH` on the Pi at its own local database, and
   delete the workstation's address from it. See ADR 158: the Pi once had
   this pointing at its own *serving* store and a stats run wrote 512
   `cell_stats` rows into the wrong database.


**An exit sweep could skip `path backfill`, `peak-labels` and the stats
passes, and save ~30 min an arm.** `net_ret` is written by `compute` alone,
through `research/enrich.py` — the phases after it exist to feed
`cell_stats`, and `cell_stats` cannot respond to an exit-policy change
(`hit_flags` reads `fwd_ret_{horizon}d`, a fixed-window market fact; see
RESULTS 2026-08-28). At ~21 min for `path backfill` plus ~4 min for
peak-labels and stats, that is roughly 4 hours across ten arms on the
workstation.

**On a machine reading the database over the LAN the saving is far larger.**
Measured 2026-08-28 with both machines running: the workstation writes path
rows at **5,292/s** and the laptop at **2,146/s** — 2.47x slower, against
only 1.38x on compute. All 25 of the database's connections sat
`idle / ClientRead` with the laptop at 8.7% CPU, so the phase is bound by
round-trips rather than by work. That is ~50 min an arm remotely, and it is
being spent to feed a statistic that cannot move.

**Not applied to the 2026-08-28 run, deliberately.** It was found with the
sweep already three hours in and running unattended on two machines, and
the day had already produced three failures caused by interrupting running
work — a stray `config.toml`, a killed remote process, and an IPv6 binding
regression. Five hours of machine time on a weekend was not worth a fourth.

The saving is real for the next sweep. Take it there, with the phases made
conditional on a flag rather than by editing the sequence.

---

### ~~Nightly has no trading-day guard, unlike the poller~~ — **added 2026-09-01 as a reduced pass**

Raised by the user 2026-08-30, after seeing a nightly terminal open at 13:15
on a Sunday.

**Correct observation.** `scripts/pi/wait_and_poll.sh` checks `trading_days`
and exits with `[SKIP]` on a non-trading day (verified against a real firing
2026-08-29). `scripts/run_nightly.ps1` has no such check and runs seven days
a week.

**It was poller-only on purpose, and the reason does not transfer.** The
poller's guard is a *correctness* one: polling a closed market writes signals
off the previous session's stale quotes into the store the site serves, and
adds a `poller_sessions` row that pollutes ADR 084's `coverage_pct`. Nightly
has no equivalent failure — its steps are idempotent, a weekend run fetches
bars that do not exist yet and recomputes an unchanged 5-day window.

**So the cost is waste, not wrongness**: roughly 35-40 minutes twice a week,
plus the API rate limit it spends and ~100k rows it re-syncs for nothing.

**And there is a real argument against adding one.** Nightly is the catch-up
path. Its 7-day lookback means a Saturday run repairs a Friday failure, and
a guard would leave that broken until Monday. Corporate actions and the
earnings calendar also update on non-trading days.

**Decided 2026-09-01 (user): add it, in the honest form this entry
describes.** `_is_trading_day` gates the three price fetchers
(`run_bars_daily`, `run_bars_hourly`, `run_market`) and nothing else --
they are the only steps that provably have nothing to fetch when there was
no session.

`run_actions` and `run_earnings` still run, because corporate actions and
calendar revisions land on non-trading days. The recompute and the sync
still run, because they are the catch-up path this entry warned a blanket
skip would break.

Reads `trading_days` rather than the weekday, so a holiday counts, and the
same table backs the poller's guard so the two agree by construction.
**Fails open**: an empty or unreachable calendar runs the full pass, since
a guard whose purpose is avoiding waste must cost waste when it breaks
rather than skipping a night.

#### Original reasoning

**Undecided rather than rejected.** If it is added, the honest version is a
*reduced* weekend pass — skip the bar fetchers and the sync, keep the
calendar fetchers and the catch-up — not a blanket skip that also disables
the repair path.

### ~~The research poll path has no sequence guard, and it bit on 2026-08-31~~ — **fixed 2026-09-01**

`assert_sequences_are_ahead` (ADR 158's 2026-08-28 consequence) refuses to
start the poller when any target sequence sits at or below its table's max
id. It runs only inside `cli.py`'s `if serving:` block, so
`scripts/wait_and_poll.ps1` — the workstation fallback, which wrote
research until 2026-08-31 — skips it.

Hit for real: a fallback poll on 2026-08-31 failed mid-session with

```
duplicate key value violates unique constraint "signal_reports_pkey"
DETAIL:  Key (id)=(1832) already exists.
```

`sync.pull_live_records` copies `signal_reports` serving -> research with
explicit ids, which does not advance research's sequence, so
`signal_reports_id_seq` froze at 1832 while `max(id)` reached 1863 through
the nightly pull. `run_sync`'s sequence reset targets serving only.

Unblocked by hand with
`SELECT setval('signal_reports_id_seq', (SELECT max(id) FROM signal_reports), true)`.

**Fixed 2026-09-01 by option 1**, the guard moved out of the `if serving:`
block so both target paths run it before `run_poll`.

Option 2 was not taken and stays worth doing: `pull_live_records` still
does not reset research's sequences, so the drift it causes recurs every
night and the guard now refuses the poll instead of the poll failing
mid-session. **Refusing is the right failure and it is still a failure** --
the operator has to `setval` by hand before a fallback poll can run. Fixing
the cause makes the guard redundant rather than load-bearing.

**Verified against live data on the day of the fix**, which is why this is
recorded rather than assumed: research's `signal_reports_id_seq` was at
**2007** against `max(id)` **2218**, 211 behind after that night's pull of
899 rows. A fallback poll the next morning would have failed exactly as it
did on 2026-08-31. The guard caught it in a unit-test run that reached the
real database, which is also how the test's own missing stub was found.

`test_poll_sequence_guard.py::test_both_target_paths_are_guarded` asserts
two call sites rather than reading the branch, because the defect was
structural: one call, reachable on one path.

#### Original entry

**Two ways to fix, neither done:**

1. Call `assert_sequences_are_ahead` on the research path too — move it out
   of the `if serving:` block and run it against whichever engine the
   poller is about to write.
2. Have `pull_live_records` reset research's sequences for every table it
   copies into, the way `run_sync` already does for serving.

Low urgency: it only surfaces when the workstation poll path runs, which
ADR 158 exists to retire. Worth doing before that path is deleted, so the
deletion is not what "fixes" it by accident.

### ~~`run_sync` overwrites serving's `events.id`, and the two id spaces have diverged~~ — **fixed 2026-09-01, ADR 163**

**Broke the 2026-09-01 nightly sync. It recurs every day the poller
fires.** Nightly itself exited 0; the sync inside it failed with

```
duplicate key value violates unique constraint "events_pkey"
DETAIL:  Key (id)=(61797210) already exists.
```

**Two unrelated rows, one id**, verified on both stores:

| store | id 61797210 |
|---|---|
| serving | ADM, 2026-09-01, `bb_upper_touch` — written by that day's **poller** |
| research | AA, 2026-09-01, `stoch_oversold` — written by that night's **`run_events`** |

**ADR 158 is what made this possible, and nothing noticed at the time.**
The poller now writes serving natively, so it mints ids from **serving's
own sequence** while research mints from its own. The two databases
allocate independently out of one numeric range. Before ADR 158 serving was
copy-only: every id arrived from research and the spaces could not diverge.

`run_sync` matches on the natural key `(config_hash, ticker, signal_date,
signal_type, entry_kind)` -- which is correct -- and then does `DO UPDATE
SET id = EXCLUDED.id`, stamping research's surrogate id onto serving's
matching row. That collides with whatever unrelated serving row already
holds it. The `id` is in the update set because `db_io.upsert`'s default
overwrites **every** non-key column, which is right for data columns and
wrong for a surrogate key.

**The blast radius is smaller than it looks, because an earlier fix held.**
The serving sweep is guarded on `sync_ok` (closed 2026-08-28), so it
correctly skipped rather than deleting serving's provisional rows with
nothing to replace them. The site keeps showing the poller's rows for the
day; it shows provisional rather than reconciled data, not blanks. That
guard was written for a torn WiFi connection and paid for itself against an
unrelated bug.

**Fixed by option 2 (user's call), recorded as ADR 163.** `events.id` is
now declared local to its store, with the natural key as the identity.
Three changes in `sync.py`: a surrogate `id` is excluded from any upsert
whose conflict key does not name it (both push paths),
`pull_live_records` nulls `signal_reports.event_id` on arrival, and it now
resets research's sequences the way `run_sync` has reset serving's since
2026-08-28.

The alternatives the entry did not consider are in the ADR: disjoint id
ranges fight `run_sync`'s own nightly sequence reset, UUID keys are a type
change on a 61.8M-row table, and copy-only serving is the design ADR 158
removed. The chosen fix is smaller because it stops asking two id spaces to
agree rather than making them agree.

#### Original entry

**Two fixes, and the second is preferred.**

1. **Drop `id` from the sync update set.** Stops the daily failure. Leaves
   a latent skew: `pull_live_records` copies `signal_reports.event_id`
   serving -> research, and once the id spaces differ that value names a
   *different event* on the target. Largely inert today because ADR 150
   nulls those links nightly and `v_screen_live` stopped joining on them
   (`d5e91a7c3b48`), but it is wrong rather than harmless.
2. **Also stop copying `event_id` in `pull_live_records`,** nulling it on
   arrival. Removes the class instead of the symptom, and says plainly that
   a surrogate id means nothing across two stores. The cost is discarding a
   link that is briefly valid for rows the sweep has not reached, and which
   nothing currently reads.

**Either needs an ADR**, because it changes what `events.id` means across
the two databases -- from "the same row everywhere" to "local to its
store". That is a real weakening of an invariant readers may be assuming,
and it should be stated rather than discovered.

**Related, same root, already fixed:** the research poll path had no
sequence guard and failed on 2026-08-31 (`signal_reports_pkey`, id 1832).
Fixed 2026-09-01. That is the same explicit-id/sequence family arriving
from the opposite direction, and it is the third instance found in two
days -- worth treating as a class rather than three bugs.

---

### ~~The research machine is not portable, and it is about to move~~ — **closed 2026-09-01, every box checked**

**The desktop leaves and an old laptop takes its exact role** -- research
database, `nightly`, `weekly`, `monthly`, `sync` to the Pi. The Pi is
unchanged. The laptop may be Linux rather than Windows, so the wrappers
have to work on both, not just carry a different path.

**What is machine-specific today:**

1. **Hardcoded repo path.** `scripts/run_nightly.ps1:27`
   (`Set-Location "C:\Users\daris\Desktop\School\CapitalScan"`),
   `scripts/wait_and_poll.ps1` (three `reports/poller` literals), and all
   three `scripts/tasks/*.xml` (`<Command>`, `<WorkingDirectory>`).
   `scripts/wip_snapshot.ps1` already derives `$RepoRoot` from
   `$PSScriptRoot`; that is the pattern.
2. **Hardcoded `psql.exe`.** `wait_and_poll.ps1:56,188` point at
   `C:\Program Files\PostgreSQL\18\bin\`. The Pi has `psql` on PATH.
3. **Two nightly wrappers that disagree.** The live scheduled task runs
   `run_nightly.ps1` (config-hash guard, exit-code propagation, UTF-8
   log); `tasks/nightly.xml` runs `nightly.bat` (none of that). Whichever
   is imported on the laptop, one is stale.
4. **No `.env.local` template.** `jobs/db.py::_load_env` reads
   `REPO_ROOT/.env.local` correctly, but nothing committed lists the ~18
   keys a fresh machine needs.
5. **No task installer.** Setup means hand-editing Task Scheduler XML, or
   on Linux writing systemd units from scratch.
6. **`capscan` superuser asymmetry.** On the desktop `capscan` is a
   superuser, so `run_nightly.ps1`'s `ALTER DATABASE ... SET
   capitalscan.default_config_hash` succeeds; on the Pi it is not and the
   pin is logged as skipped. The laptop must match the desktop or the
   config pin silently stops working.
7. **Docker vs native Postgres.** The desktop runs Postgres in the
   `capitalscan-postgres` container, which does not restart after a reboot
   -- that is what failed the 2026-08-30 nightly (`ConnectionTimeout`).
   Native Postgres on the laptop removes that failure class and most of
   `CLAUDE.md`'s container caveats.
8. **`$ServingHost = "192.168.1.30"`** in `wait_and_poll.ps1:18`
   duplicates the IP already in `DATABASE_URL_SERVING`.

**Not in scope.** The Pi cannot be a `nightly` fallback -- research is
19 GB and the Pi has ~27 GB free with serving already on it (see the Pi
note in `CLAUDE.md`). "Redundancy" means a manual re-run on the research
machine, not a second machine that can do the job. Making the Pi a real
fallback would need the laptop to expose 5432 to the LAN and the Pi's
`DATABASE_URL_RESEARCH` pointed at it -- the firewall/IPv6 setup CLAUDE.md
already documents scars from -- and is a separate decision.

---

**Definition of done.** Each box is verifiable on both a clean Windows
machine and a clean Linux machine unless it names one.

**Progress, 2026-08-31.** The script pass, the setup artifacts, the
installers, and `cscan preflight` are built and pass on the desktop. What
is left: exercise the *new* `run_job` wrappers with a real scheduled run,
confirm `cscan preflight` on the Pi, and label CLAUDE.md's
machine-specific sections. The old `run_nightly.ps1` still ran the
2026-08-31 nightly; the shim to `run_job.ps1` has not fired from Task
Scheduler yet.

**Progress, 2026-09-01 (evening). The data is on `wivie`.** A 2.59 GB
`-Fc -Z6` dump streamed from the container over ssh in ~8 minutes (19 GB
raw, ~5 MB/s over WiFi), restored in 10 minutes with `-j 4`. Verified by
comparison rather than by exit code -- the restore exited **1**:

    events 13,336,785   bars 8,196,897   indicators 5,997,334
    path 52,793,296     universe 1,407,650   tickers 1,561
    cell_stats 4,096    alembic b7f3c5d21a94

Identical on both machines, every table. The exit 1 was **82 errors of one
kind** -- `role "capscan_ro" does not exist`, the MCP read-only role, which
was never provisioned on `wivie`. GRANTs failed, no data did.
**Provisioned 2026-09-01**: `cscan db grant-readonly`, password `capscan`
matching the other two machines (user's decision -- a read-only role on a
LAN-only host, and consistency is what prevents mistakes at cutover).
Verified: reads 13,336,785 events, `DELETE` returns `permission denied`.
Set `DATABASE_URL_MCP` in `wivie`'s `.env.local` to point MCP at it.

`wivie` is 18 GB against research's 19 GB; the difference is bloat the
restore did not carry, not missing rows. The one-migration drift
(`a4c8d19f6e02`) closed in the same step, since the database was dropped
and recreated rather than restored into.

`cscan preflight` on `wivie` is **all-OK, exit 0** -- role, env, psql, both
databases, schema at head, config hash matching `serving_config`, schedule
installed. **All three timers verified `disabled` and `inactive`**, so
nothing on `wivie` fires and there is no second writer.

**This copy goes stale from here.** It is a rehearsal and a bulk baseline,
not a dump nobody repeats: at cutover the delta is whatever accumulated
since 2026-09-01. The measured 8+10 minutes is the number to decide with --
re-dumping is likely cheaper than building a delta path. The ADR 160
systemd units (`Type=simple`, `Restart=on-failure`, `OnBootSec`, the 19:00
nightly retry) are rendered into `/etc/systemd/system` and
`daemon-reload`ed, **timers still `disabled`** — `systemctl enable --now`
is the cutover step (SETUP.md C1 step 3), gated on the restore. Nothing
fires on `wivie` until then. `run_job.sh`'s lock + `resume-check` guards
are verified there against a real firing.

All three LAN addresses are now DHCP-reserved (Pi `192.168.1.30`, wivie
`192.168.1.12`, the workstation `192.168.1.14`), closing item 8 and the
ADR 152 lease-drift consequence — but the Pi keeps `listen_addresses =
'*'` because that is a boot-ordering race (wlan0 not yet associated when
Postgres binds), independent of whether the address is reserved.

Portability of the scripts:

- [x] No script under `scripts/` contains an absolute path to the repo,
      the venv, or `psql`. Repo root is derived from the script's own
      location; `psql` comes from PATH with a `CAPSCAN_PSQL` override;
      the serving host is parsed from `DATABASE_URL_SERVING`, never a
      second literal. (One deliberate last-resort `psql` fallback path
      remains in `wait_and_poll.ps1`, guarded behind PATH and the env
      override.)
- [x] One wrapper: `scripts/run_job.ps1 <nightly|weekly|monthly>` and
      `scripts/run_job.sh`. `run_nightly.ps1` is a one-line shim.
      `tasks/*.xml` are templates (`{{REPO}}`) that call `run_job.ps1`.
      The `.bat` wrappers are deleted.
- [x] A Linux wrapper exists for every Windows wrapper: `run_job.sh`
      mirrors `run_job.ps1` (repo root, `reports/<job>/` log, config-hash
      guard, exit-code propagation). `scripts/pi/wait_and_poll.sh` already
      covered the poller.
- [x] `grep` for absolute paths in `scripts/` returns only comments and
      `scripts/pi/`.

Setup artifacts:

- [x] `.env.local.example` committed: the four required ingest keys with
      comments, then optional notification / MCP / web blocks, no secrets.
- [x] `docs/SETUP.md` is a from-scratch runbook for both OSes, ending in a
      desktop -> laptop migration checklist.
- [x] `scripts/install_schedule.ps1` registers the three tasks from its
      own location; `scripts/systemd/` holds the three service+timer
      template pairs and `install.sh` that fills `WorkingDirectory` and
      `User` and enables the timers.
- [x] Uninstall documented: `install_schedule.ps1 -Remove`,
      `scripts/systemd/install.sh --remove`.

Verification, the part that answers "does it work on this machine":

- [x] `cscan preflight` exists (`jobs/preflight.py`) and checks
      `.env.local`, `psql`, both DB connections, research schema vs the
      repo's alembic head, config vs `serving_config`, and the schedule.
      `fail` exits 1; `warn` (schedule not yet installed, serving
      unreachable on an ingest-only box) does not. No writes.
- [x] `cscan preflight` exits 0 on the desktop and on the Pi (the Pi
      infers `role: serving` from a localhost `DATABASE_URL_SERVING` and
      skips the research checks).
- [x] The *new* `run_job` wrappers run with no absolute-path or dependency
      error. Container-tested 2026-08-31 in `debian:trixie-slim`, then
      **fired for real by Task Scheduler on 2026-09-01 13:15** --
      `resume-check` decided to run, the config-hash guard passed, and the
      chain closed `exit=0` at 13:52. `wait_and_poll` is now covered on
      both platforms by `scripts/test_wait_and_poll.ps1` (14 assertions)
      and `scripts/test_wait_and_poll_pi.sh` (11), each against a
      throwaway container; both found a real bug on their first run.
- [x] The migration is one section in `docs/SETUP.md` and depends on
      nothing on the desktop afterward.

Follow-through:

- [x] **The two machines need to be functionally identical, not literally
      identical** (user's call, 2026-09-01). Measured the same day:
      workstation Python **3.14.3** (`spawn`), `wivie` **3.13.5** (`fork`),
      because `pyproject.toml` pins only `requires-python = ">=3.11"` and
      `uv` resolved whatever each box had. **Accepted, not a to-do.** The
      code supports the whole range, the spawn-first rule in `CLAUDE.md`
      already forbids depending on `fork`, and nothing in the cutover
      copies an interpreter between machines. Recorded so the difference is
      known rather than discovered. Postgres 16.14 against 17.11 is the
      same kind of fact, with the one real constraint that the cutover
      direction restores and the reverse does not.
- [x] `CLAUDE.md`'s machine-specific sections say which machine each rule
      is about, and the ones that were desktop-only are marked as such or
      generalised. Done 2026-09-01. **Framed as a two-machine document
      rather than a handoff**, because the desktop is not going away: it
      stays the heavy-research box after the move (backtests, sweeps,
      rebuilds) and `wivie` takes the scheduled chain. The labels that
      matter are therefore not "desktop vs laptop" but the two places the
      machines genuinely cannot be identical -- Docker Postgres against
      native Postgres, and Task Scheduler against systemd.
- [x] This entry is struck through and dated when every box is checked.
      **Done 2026-09-01.** `wivie` holds a full copy of research, passes
      `cscan preflight` with its timers dormant, and the cutover is a
      `pg_dump`/`pg_restore` (measured 8 + 10 minutes) plus repointing the
      Pi's `.env.local`. What remains is not portability work -- it is the
      cutover itself, which waits on the move.

---

## Scheduled later

Work that is decided, understood and deliberately not next. Nothing here
blocks the move or the daily chain. Ordered by what each one would change
if it were run.

### ~~The `max_hold_days` sweep — 3 / 5 / 10~~ — **done; arms were already built, read 2026-09-01**

**The arms had been computed on 2026-08-29/30 and nobody read them.**
`h3_t5_atr20` and `h10_t5_atr20` were already in `scripts/exit_sweep.py`'s
grid and already in `events` -- 1,379,144 rows each, 597,605 with
`net_ret`, 2h42m and 2h32m of compute. The open item was analysis, not
machine time.

**Result: the window does not matter, and hold=5 stays.** Counting every
fire, train means are −3.90 / **−2.77** / −3.00 bp for hold 3 / 5 / 10. The
winning margin is 0.23 bp against a standard error of ~0.67, so no arm is
distinguishable from another. RESULTS 2026-09-01 has the table.

**It settles what it was run to settle.** Moving the window 3 → 10 swings
the timeout rate 71.8% → 29.4% — a bigger swing than removing the stop
produced — and moves train mean by 1.13 bp against the stop's ~9 bp. So
`max_hold_days` is not a hidden second dominant parameter, "the stop costs
money" and "five days is the wrong window" are separable, and the case
against shipping stopless remains the tail (ADR 161).

**One methodological finding, promoted to the entry below.** The first pass
filtered `is_cluster_head` and got three *different populations* (104,460 /
78,432 / 51,832), because cluster membership derives from `days_since_head`
which derives from `max_hold_days`. The filter is not invariant to the
parameter being swept. That is a stronger argument for the next item than
the 7-9 bp gap already recorded.

### ~~Count every fire in backtest returns, keep the filter in `cell_stats`~~ — **built 2026-09-01, ADR 165**

`capitalscan/research/returns.py` is the one implementation, exposed as
`cscan stats returns`. Counts every fire by default; `cell_stats` and
`benchmarks` are untouched and keep `is_cluster_head` until a serial
`n_eff` exists.

**The decisive argument turned out to be invariance, not the 7-9 bp gap.**
The gap says the filter is biased. The `max_hold_days` sweep showed it is
**not invariant to the parameter being swept** -- cluster membership
derives from `days_since_head`, which derives from `max_hold_days`, so a
filtered comparison measured three different populations and read as
non-monotonic with the splits disagreeing. A filter that can invent an
effect is a correctness problem for future sweeps, not only an accounting
one.

**Why a module rather than a convention.** Every strategy-return figure in
`RESULTS.md` before this date was hand-rolled SQL, and the `max_hold_days`
table was written that way twice and wrong both times -- once filtering
cluster heads, once omitting `in_trade` and averaging `next_open` and
`touch` together (n 310,749 against the correct 163,424, train mean −2.77
bp against −1.94). Neither error was arithmetic. `BASE_PREDICATES` is a
module constant and `entry_kind` carries a default so a caller cannot omit
them by forgetting.

The module found that second error within minutes of being written,
against the entry that motivated it.

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

### ~~ETF `mcap_usd` wants its own dated migration (ADR 156)~~ — **investigated 2026-09-01: there is no migration to write**

**The entry described work that would be wrong to do.** It said adopting
`netAssets` "rewrites QQQ's `mcap_usd` from $289B to $453B across 66
quarters" and therefore "wants its own dated migration". Neither half
holds.

**`_latest_shares` already does the right thing, and it does nothing to
history.** It selects the newest `shares_outstanding` row with
`filed_on < as_of`, regardless of source. The `yahoo_netassets` rows are
dated 2026-08-27 onward, so they are newer than every quarter already
evaluated. Measured:

    as_of 2020-06-30 .. 2026-06-30  ->  393,100,000  filed 2021-03-17
    as_of 2026-09-30 onward         ->  639,874,291  filed 2026-09-01

So the correction arrives at the **next** quarterly evaluation on its own,
with no migration, and no historical row changes.

**Backdating them would violate the rule the function exists to enforce.**
Its docstring: "a row dated 2016 describes 2016 regardless of which fetcher
produced it, so ordering by `filed_on` alone already prevents a current
count from silently answering a historical `as_of` -- the one thing this
function must not do." A migration stamping today's `netAssets` onto 66
past quarters is exactly that, and DESIGN §2.4 rejects it explicitly.

**The residual is real but smaller and different.** QQQ and SPY have no
share rows between 2021-03-17 and 2026-08-27, so quarters 2021Q2 through
2026Q3 are priced on a five-year-old count. `netAssets` cannot fix that --
Yahoo publishes it as a current figure, with no history.

**It changes no decision.** `min_mcap_usd` is $20e9; QQQ is recorded at
$289B and SPY at $685B, both more than tenfold above the threshold, and
`crit_mcap` is true either way. A wrong recorded number, never a wrong
eligibility call. Left as-is.

### ~~An isolated harness for `wait_and_poll`'s guards~~ — **built 2026-09-01, and it found a bug**

The poller wrappers are the least-tested code in the system and the most
recently changed: `wait_and_poll.ps1` switched to `cscan poll --serving` on
2026-08-31, and the guards it depends on are exactly the ones that failed
that day.

**Built as `scripts/test_wait_and_poll.ps1`. 14 assertions, all passing,
nothing touched outside a throwaway container.**

**It found a real defect on its first run**, which is the entry paying for
itself. `wait_and_poll.ps1` captured the port out of
`DATABASE_URL_SERVING` and then never used it -- both `psql` calls omitted
`-p` and fell back to the default 5432. That worked only because serving
happens to listen there. **On the workstation 5432 is the research
container**, so a serving store on any other port would have had the
calendar guard querying a different database server than `cscan poll
--serving` writes to: a guard reading one machine to authorise writes to
another. Fixed in the same commit.

**The mechanism is a fake repo root, so the shipped file runs unmodified.**
The script derives `$RepoRoot` from `$PSScriptRoot` and reads
`$RepoRoot\.env.local`; copying it into `<tmp>\scripts\` with a scratch
`.env.local` redirects every path it resolves. No test hook, no `-WhatIf`,
no branch that exists only under test.

Two harness bugs worth recording, because both are documented traps that
still caught a fresh script:

- **`$ErrorActionPreference = 'Stop'` spanning a native call.** `docker
  run` writes "Unable to find image locally" to stderr as ordinary
  progress, and PowerShell 5.1 wrapped it in a terminating
  `RemoteException`. Exactly the `cscan nightly 2>&1` trap in `CLAUDE.md`,
  hit again in new code.
- **`Start-Process -PassThru` with redirected streams reports an empty
  `ExitCode`.** Three assertions failed against a script that was
  behaving correctly. Replaced with `System.Diagnostics.Process`, reading
  both streams asynchronously so a full pipe buffer cannot hang in a way
  that looks like the wait loop.

**Still out of scope, deliberately.** The staleness and sequence guards
live inside `cscan poll --serving`, not in this script, so the harness
does not reach them; they have unit tests and both fired against
production on 2026-08-31 and 2026-09-01. The polling loop itself needs
live quotes and a real clock. `scripts/pi/wait_and_poll.sh` has no
equivalent harness yet -- the same fake-root trick would work.

#### Original entry

**The container test for `run_job.sh` is the pattern.** A throwaway
`debian:trixie-slim` proved the wrapper's mechanics — repo-root derivation,
venv discovery, logging, the config-hash guard — without touching
production. The equivalent here is a **throwaway serving database**: a
schema-only restore into a scratch Postgres, seeded `trading_days` and a
watermark, with `DATABASE_URL_SERVING` pointed at it.

**What that exercises**, all of it pre-poll and all of it previously
untested:

- the trading-day check (does a Saturday `[SKIP]`?)
- the staleness guard, including the trading-session counting added
  2026-08-31 for the weekend case
- the sequence guard, on both target paths as of 2026-09-01
- serving host/db parsing out of `DATABASE_URL_SERVING`
- `psql` discovery through PATH and `CAPSCAN_PSQL`

**What it cannot exercise** is the polling loop itself, which needs live
quotes and a real clock. That half stays session-only, which is fine: the
guards are the part that has actually broken.

### ~~Cluster size as a feature, not a discovery~~ — **measured 2026-09-02: real, and not usable**

**Both halves answered, and the entry can close.**

**The ~300 bp effect is real.** ADR 166 flagged that it rested on a 10+
fire bucket that does not exist in the measured population. Re-measured
there (`in_trade`, `next_open`, clusters capped at 6) it is **monotone at
every step on both splits** — train +175.7 → −93.2, validate +230.5 →
−104.3, spreads of 269 and 335 bp. It survives the correction and it holds
out of sample.

**And it is almost entirely hindsight, which this entry suspected and can
now quantify.** `seq_in_cluster` is what a live decision knows;
`cluster_size` is not. Position spans **8.8 bp on train and is not
monotonic**. At position 3 you know the cluster is at least 3, not whether
it stops there (+112 bp) or runs to 6 (−93 bp) — and the effect lives
entirely in that difference.

**For Phase 6: do not build `cluster_size` as a feature.** It leaks. The
label window and the cluster window overlap, so a model handed eventual
size is told part of its own answer; the 269 bp would show as skill in CV
and vanish live. `seq_in_cluster` is safe, costs nothing, and should not be
expected to carry the effect.

**The successor question, stated so nobody re-derives the dead end:** can
*continuation* be predicted at fire k from drawdown depth, band width,
realised vol, days since head? That is the usable form, it is a Phase 6
modelling question rather than a query, and nothing measured so far
addresses it.

RESULTS 2026-09-02 has both tables.


## Half the predictions ship with no quantile fan, and nobody knows why

Found 2026-09-08 while investigating ADR 180. `predictions.q05..q95` come
from the terminal 5-day head via `FAN_TASK`, and `build_rows` writes `None`
for any non-finite value. Across the 5,986 resolved forward-log rows:

| | n | with a fan |
|---|---:|---:|
| in the training population | 4,020 | 1,847 (46%) |
| outside it (ADR 180) | 1,966 | **0** |

The out-of-population column is explained by ADR 180 and is now moot -- those
rows no longer serve. **The in-population 54% is not explained.** Those are
rows the model was fitted on, shipping a `p_touch_3` the screener displays,
while the same ensemble returned a non-finite quantile for the same row.

It does not currently mislead anyone: ADR 172 established that
`terminal_h5_q50` is negative out of sample, so no surface displays the fan,
and `outcomes.pinball_loss` is simply NULL where it is missing. The reason to
chase it is that a probability and a quantile read off *the same CDF* should
not disagree about whether that CDF exists. One of them is wrong, and the
probability is the one being shipped.

Start at `Ensemble.fan` rather than at `build_rows`: `exceedance` sums pmf
bins and stays finite for any finite pmf, so a finite probability beside a
non-finite quantile points at the inversion, not at the distribution.

Cheap first cut: pull one such row, print its pmf, and see whether the CDF
ever crosses the quantile level at all.

## ~~`wivie` is 12 migrations behind~~ — **closed 2026-09-10 by the restore, exactly as this entry predicted**

Both stores now read `a7c2e9f4b105`. The entry's advice was right and is
worth keeping for the next time: **the schema was not a separate task from
the data sync, it was the same task.** A `pg_restore` of the workstation
dump carried both, and `cscan db status` reads head because the dump put it
there -- no `db migrate` was ever run on `wivie`.

Also done in the same pass: the stale 2026-09-01 database was dropped, the
current restore promoted into its place under the name `wivie`'s config
already points at, and `capitalscan.default_config_hash` pinned. That last
one matters more than it looks -- `cscan sync` reads its generation from
the **research** database's GUC, so an unpinned `wivie` acting as research
would have synced the wrong generation and exited 0.

**What remains before `wivie` can serve as research:** `systemctl enable
--now` on the three timers, which is the cutover itself and is deliberately
not done.

<details>
<summary>The original entry, kept because the reasoning generalises</summary>

## `wivie` is 12 migrations behind, and must NOT be caught up with `db migrate`

Checked 2026-09-08. `wivie`'s local research database sits at
`b7f3c5d21a94`; head is `a1c7f3b09d84`. The twelve in between are the whole
prediction chain:

```
e2c7a94b3d15  universe_watch_near_trade      c1e6b73f9a02  market_days_breadth
c3f8a1e07b26  predictions_carry_calibration  d8c40a5b71e9  ticker_events_in_watch
d5a02b18c937  screener_views_prediction_ci   e4b19c86d275  predictions_natural_key
e7b4c92f1a08  predictions_key_on_event_id    f7d3a02e5c18  views_join_natural_key
f2a71d6e8c34  events_trough_ret_columns      a1c7f3b09d84  predictions_model_scored
a9d3e05f7b21  predictions_calibration_json
b4f8c17d29e6  views_expose_calibration_json
```

Its *serving* pointer is fine and already reads the Pi at head — only the
local research database is stale.

**Running `cscan db migrate` there is the wrong move, and the reason is
ordering rather than risk.** The cutover restores a workstation dump onto
`wivie`, which is the only direction that works (ADR 164: 16.14 restores
into 17.11, never the reverse). That dump carries the workstation's schema
*and* its data, both at head. Migrating the stale database first spends
twelve migrations on rows that a `pg_restore` is about to replace, and
leaves a window where `wivie` holds head's schema over an old generation's
data — which looks exactly like a working research database and is not.

Restore first, then confirm `cscan db status` reads head because the dump
put it there. The schema is not a separate task from the data sync; it is
the same task.

</details>

**Still true and separate:** WAL and autovacuum tuning live on the server,
not in a migration, so they must be re-applied by hand on `wivie` after any
restore. → `CLAUDE.md`, `pi-postgres-tuning`.

## The coverage gate watches a family the product does not display

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

## The shipped probabilities run about 5 points low, and the cause is not the model

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

## `bars` and `indicators` are joined into every feature frame for four dead columns

Found 2026-09-08. `META_COLS` carries `bar_low`, `bar_high`, `band_lower`
and `band_upper`, commented "Raw inputs to `breach_depth`, dropped from the
matrix once derived." **`breach_depth` was deleted the same day** (ADR 177's
correction — it reads the signal session's low, which is look-ahead under a
`touch` entry). `DERIVED_FEATURE_COLS` is now `("k_minus_d", "mcap_log")`
and nothing consumes the four.

They are in `META_COLS`, so **this is not a leak** — they never reach the
design matrix, and the signature probe still holds. It is waste, and the
waste is not small: `_SQL` keeps a join against `bars` (4.5M+ rows) and one
against `indicators` purely to populate columns that are then discarded, on
every training and serving frame build.

Delete the four columns, both joins, and the stale comment. Check
`partition_for_training` and `_add_derived` for other references first
(`features.py:98`, `:159`, `:164`, `:583` all still name `breach_depth`,
three of them as history rather than code).

Measure the frame build before and after rather than assuming: this is the
kind of change that looks free and occasionally is not, because a join that
also constrains row count is doing more than it appears to. If it turns out
these joins are inner joins, dropping them **changes the training
population**, which is a config-hash question and not a cleanup.

## The backtest harness cannot run at the current event count

Found 2026-09-09 while running the full backtest. **The harness phase is
the only part that did not complete, and the rebuild is therefore
unvalidated by it.**

`_load_events_for_config` (`cli.py:809`) does `SELECT * FROM events WHERE
config_hash = :chash` into a single DataFrame. That table now holds
**10,824,053 rows** for the live config, roughly double what it held when
the harness was written, because the cosmetic backfill priced `in_watch`
and out-of-universe signals.

Measured directly, mid-run:

```
python PID 30444   commit 70.69 GB   resident  1.77 GB
python PID 20956   commit 23.30 GB   resident  0.13 GB
commit limit 114.3 GB, commit free 0.7 GB
sum of ALL process working sets: 2.9 GB
```

**It exhausts commit charge, not RAM**, which is why it presents as
"system is running low on memory" while almost nothing is resident. That
misleads: three separate attempts were spent lowering `--workers` (8 -> 4
-> 1) and capping WSL, none of which touched the cause. Killing the two
processes freed 39.7 GB of commit instantly.

`--tickers` does not help. The load runs before any ticker filter and is
scoped only on `config_hash`, so a hundred-ticker harness still reads all
10.8M rows.

### The fix, and why it was not done in flight

Select only the columns `run_harness` actually reads instead of `*`. That
is a narrowing rather than a weakening, but it requires knowing exactly
which columns each of the five checks touches, and getting that wrong
silently removes a check rather than failing. The harness is one of the
five things `CLAUDE.md` names as carrying the correctness load, so it is
not a change to make at 04:00 against a sleeping owner.

Chunking by ticker is the alternative and is a larger change: the
no-look-ahead ladder and the signal-path parity check are per ticker
already, so the events frame could be built and discarded per ticker
rather than held whole.

**Do not raise the commit limit to work around this.** A 70 GB reservation
for a frame that needs a fraction of that is the defect; a bigger pagefile
would hide it and make every future run slower.

### What this means for the 2026-09-09 rebuild

`compute` (59/59 chunks, 2026-09-08) and `finalize` (2026-09-08 15:57,
32m51s) both completed and are current -- verified by diffing the compute
path between the chunk sha and HEAD, which found only a formatting reflow.
The **harness last passed 2026-08-30**, before the event count doubled, so
the data written since has not been through it.

That is a real gap and should be closed before the wivie cutover, not
after.

## OPEN NOW: the scheduled nightly is DISABLED and must be re-enabled

Disabled 2026-09-10 12:25 PT so it would not fire at 13:15 into a running
`config_hash` rebuild. Its chain is
`events -> path_capture -> peak_labels -> predict -> sync`, and every step
would have collided: `events` upserting the same natural keys the backtest
was writing, `path_capture` racing `path backfill`, `predict` on a
half-built generation, and `sync` shipping that generation to serving --
`run_sync` reads the research GUC, which already points at the new hash.

**Re-enable after the serving steps finish:**

```powershell
Enable-ScheduledTask -TaskName "CapitalScan nightly"
Get-ScheduledTask -TaskName "CapitalScan nightly" | Select-Object State
```

Verify `State = Ready`. A forgotten disable is silent: no nightly runs, no
error appears anywhere, and the first symptom is stale data days later.
`Get-ScheduledTaskInfo`'s `NextRunTime` still shows a time while disabled,
so that field does **not** confirm it is armed -- read `State`.

