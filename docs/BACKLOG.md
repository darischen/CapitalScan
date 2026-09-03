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

## Open

### Coverage is 17/20 and the gate needs 20 — three heads, all failing the same way

**This is the only thing standing between ADR 170's model and a real
promotion decision**, so it is the first entry rather than a footnote.

ADR 170's multi-task model lifted coverage from the incumbent's 14/20 to
17/20 (RESULTS 2026-09-03). DESIGN §7.7 check 3 wants all twenty within 5
points of nominal. The three that fail:

| head | coverage | nominal | error |
|---|---|---|---|
| `terminal_h5_q25` | 0.310 | 0.25 | **+0.060** |
| `terminal_h10_q25` | 0.318 | 0.25 | **+0.068** |
| `terminal_h10_q50` | 0.550 | 0.50 | **+0.050** |

**Measured 2026-09-03: all three failures are entirely 2022.** Split by
year, every one passes on 2023 -- `terminal_h10_q50` by six thousandths --
and fails on 2022 by 10 to 12 points. Validate is 2022-01-03 to 2023-12-29,
and 2022's realised 5-day q25 is −3.34% against the model's fitted −1.63%.
The model is well calibrated in an ordinary year and understates downside
in a bear market. RESULTS 2026-09-03 has both tables, and the implication
for the fix: `rv_pct_252d` and `vix_close` are **already features**, so the
model has the inputs to recognise a high-volatility regime and is not
widening its lower tail enough on them. Measure that before building a new
label.

**All three over-cover, all three are terminal, all three are in the lower
half of the fan.** That is not three problems. The predicted lower
quantiles sit too high, so realised returns fall below them more often than
they should — which is the residual regime shift RESULTS 2026-09-02 already
found irreducible from train data, now down from six heads to three and
concentrated in one corner.

**What is worth trying, and what is not.**

- **Not recalibration on validate.** DESIGN §7.6 is explicit that quantile
  heads are *checked* by coverage rather than recalibrated, and fitting a
  correction on the split the gate scores makes the gate circular. This was
  already ruled out on 2026-09-02 and the ruling did not change.
- **Market-level trend features — do this first, it is nearly free.**
  Root-caused 2026-09-03 (RESULTS): of 22 features **only three are
  market-level**, and none spans more than a day — `vix_close` is a level,
  `spx_ret_1d` is one day, `cofire_count` is same-day breadth. Every trend
  feature (`above_sma200`, `sma200_slope_60`, `dd_52w`) is **per ticker**.
  So the model can see that a stock is in a downtrend and cannot see that
  the market is. 2022 spent **185 days** more than 10% below the high
  against a previous record of 68, and the coverage error is **worst on
  shallow-drawdown events** (+0.118) and mildest on deep ones (+0.039) —
  the events that looked ordinary per-ticker while the index ground down.
  `SPY` is already in `bars` from 2004-09-29, so the index analogues of
  those three features need no new data source. **Measure before
  building on it**: one regime episode is thin evidence, and a feature
  fitted to help 2022 may be fitting 2022.

- **A fifth auxiliary task.** ADR 170's gain came from three labels
  supervising a fourth. A `trough_ret_{h}d` label — the mirror of
  `peak_ret` — would add a task whose information is concentrated in
  exactly the lower tail that is miscovered. **Demoted below the feature
  work by the root cause above**: the miscovered events are the ones whose
  *per-ticker* features look benign, and a better lower-tail label does not
  tell the model it is in a market downtrend. It needs a label built, which
  is a `path_labels.py` change, not a modelling one.
- **Longer horizons as auxiliaries only.** ADR 113 dropped h=1,2,3
  deliberately and h=20 was never built. A 20-day head that is trained but
  never reported would regularise without widening the reported fan.

**Nothing here is promoted regardless of outcome until the holdout is
spent**, and the holdout is spent once.

### On-demand per-ticker inference — **measured 2026-09-03: not needed for the model we have**

The question was whether a heavier model could be run per ticker behind a
button on the chart, rather than in batch for the whole universe. Measured,
the premise does not hold for the model that won:

| | parameters | one row | one ticker (64 events) | all 34,195 validate events |
|---|---|---|---|---|
| ADR 170 multi-task, CPU | 259,904 | 0.56 ms | 1.25 ms | **91 ms** |
| ADR 170 multi-task, GPU | 259,904 | 0.71 ms | 0.74 ms | 9.3 ms |
| TabFM, GPU (ctx 4000) | 1.64 **billion** | ~8 ms | ~0.5 s | ~4.5 min |

**The model that won scores the entire validate split in 91 milliseconds on
a CPU.** Batch inference for everything is cheaper than the HTTP round trip
a single button press would cost, so the on-demand path solves a problem
this model does not have.

Worth noting from the same measurement: **one row is slower on the GPU than
on the CPU** (0.71 ms against 0.56 ms), because at this size the whole cost
is kernel launch overhead. The GPU only starts winning around a thousand
rows. A per-ticker inference service would therefore want the CPU anyway,
which removes the last reason the feature would need special hardware.

The button remains the right shape for a *heavy* model, and that is the
form the entry keeps: it is not rejected, it is unnecessary until something
expensive is worth serving. If ADR 169's TabFM work or a successor produces
a model that cannot run in batch, the design is a `web/app/api/predict/
route.ts` beside the five existing routes, reading a cached fan rather than
computing one in the request.

**What would have to be true first, in order.** The gate passes (see the
entry above), the holdout is spent, ADR 067's four promotion checks pass,
and `v_screen_live`'s `LEFT JOIN predictions` is understood to mean that
writing that table puts model output on the site.

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

