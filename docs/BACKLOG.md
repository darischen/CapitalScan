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

### Site auth is off, and the Pi migration is when that has to change

`SITE_AUTH_DISABLED=1`, turned off deliberately 2026-08-20 with the reason
recorded: a Vercel deployment URL is hard to discover, the content is public
market data plus one operator's analysis, and there is no user model at all —
no PII, no credentials, no accounts.

The argument was that there is no edge to protect. Bollinger Bands and the
stochastic oscillator have been studied exhaustively by people paid to do
it, and ADR 112 is the house result that no cell survives correction. What
an observer would learn is already public, and the part that is not — that
the measured edge is absent — is published on purpose.

**Moving to a Raspberry Pi changes the premise, and the flag should go with
it.** That reasoning rests on the URL being obscure; a LAN service is not
obscure to anything on the LAN. And `/api/chat` spends Anthropic tokens per
request — harmless today only because the route reaches MCP on `127.0.0.1`
and dies before any model call. On the Pi, with MCP local, an open page
becomes an open wallet.

`docs/PI_MIGRATION.md` makes deleting the flag part of the migration rather
than a follow-up. **Delete the key; do not set it to `0`.** The middleware
opens only on the exact string `"1"`, so `=0` is already refused, but a key
left lying about is a decision waiting to be flipped by accident. With
`SITE_PASSWORD` unset the site returns 503 rather than falling open, which
is the correct direction to fail.

---

### Expanding the universe beyond the S&P 500 seed

**Largely done 2026-08-21 — see ADR 143.** Nasdaq listings at or above $5B
are ingested, `min_mcap_usd` is $20B, and 543 tickers are ever `in_trade`
against 378 before.

**NBIS is resolved, and this entry used to blame the wrong cause.** It said
NBIS "never enters `tickers` and is never evaluated at all". Untrue since
ADR 143: it has a `tickers` row, 7 universe evaluations, 460 daily bars and
2 events. It is not `in_trade` because `crit_rel_return` needs 757 daily
bars and Nebius relisted in October 2024 — the criterion cannot be *judged*,
and `is_tradeable` treats that as failing.

**The entrant blackout is now visible rather than silent.** ADR 149 added
the watch universe: a name passing market cap and the SMA200 slope, short
only on a criterion unjudgeable for want of history, is `in_watch` with
`watch_reason = 'history'`. ARM, GEV, SNDK, ALAB and NBIS — $1.18T — sit
there fully computed, so each arrives with measured history the day it
graduates. `crit_rel_return` itself stays at 756 bars (user's decision,
2026-08-24).

**What remains open.**

**A mechanical rule for further expansion.** ADR 035's survivorship argument
does not survive hand-picking: the S&P union is *complete*, failures
included, while a ticker added because it looks interesting today is
selected on the outcome the study measures. Any expansion needs a rule that
could have been written in 2010 and applied mechanically — "the Nasdaq-100
union" qualifies, "NBIS and a few others" does not.

**`config_hash` and the rebuild.** Universe definition is config (ADR 060),
so broadening it invalidates every measured row. Measured 2026-08-24, that
is ~1h18m compute plus a 45m harness plus statistics.

**Relative return needs a benchmark that means something.**
`crit_rel_return` compares against the S&P series in `market_days`.
Defensible for a Nasdaq name; for a foreign listing it quietly changes what
the criterion tests.

---

### `events.sector` and `events.mcap_usd` are NULL on every row

Raised 2026-08-25 while building the model's training matrix. Both columns
exist on `events` and both are empty:

    events.sector      0 of 227,543 populated
    events.mcap_usd    0 of 227,543 populated

The real values live elsewhere — `tickers.sector` carries 11 GICS levels
after ADR 148, and `universe.mcap_usd` carries 47,181 values with quarterly
history from 2010-03-31. `research/features.py` reads both from those
sources and the frame is correct.

**The hazard is the shape, not the outcome.** An unqualified `sector` in a
query over `events JOIN tickers` resolves to the `events` copy: a column
that exists, is spelled correctly, and is empty. Both features were one
unprefixed name away from shipping as all-NULL and no test would have
objected, because "the column is there" and "the column has values" are
different claims. `test_model_features.py` pins the qualified sources.

Either populate the two columns at event creation or drop them from the
schema. Leaving an empty column beside a populated one of the same name is
the trap standing.

---

### Three of DESIGN §7.3's twenty-two features are not built

Raised 2026-08-25. §7.3 says all twenty-two are "already on the event row".
Three are not, measured against `information_schema`:

| Feature | Blocked by |
|---|---|
| distance to mid in ATR units | `bb_mid` absent from `events` |
| `atr_14 / close` | `atr_14` present, `close` absent |
| `vix_pct_252d` | absent from `events` |

Each exists in `indicators` at t−1, so the fix is a join or three new event
columns. Both are decisions rather than lookups: a join at frame-build time
reintroduces the sourcing question the module's look-ahead argument rests
on closing, and `entry_price` cannot serve as the `close` denominator
because it is priced at *t*.

The nineteen built features are clean. Adding these three is a measurable
increment against a fitted baseline, which is the right way to add them —
the same sequencing ADR 069 uses for breach depth.

---

### Sector is a current snapshot applied to historical events

Raised 2026-08-25. `tickers.sector` has no history, so a company GICS
reclassified in 2018 carries its post-2018 sector on its 2010 events. Mild
look-ahead of the kind ADR 135 names.

Accepted for now and recorded rather than inherited silently:
reclassifications are rare, the alternative is dropping the only
categorical DESIGN §7.3 asks for, and point-in-time GICS history is a data
source this project does not have. `universe.mcap_usd` has the same shape
of problem and does **not** suffer it, because that table is evaluated
quarterly and the lateral is bounded by `as_of <= signal_date`.

---

### A long `cscan sync` is not atomic, and the source can move under it

Raised 2026-08-25, observed rather than reasoned about. A sync that ran
**1h45m** copied its fourteen tables in foreign-key order while the research
database changed underneath, and the copy captured different moments for
different tables.

Measured on the Pi immediately afterwards:

    QQQ    5,282 bars   5,282 indicators   30 in_trade universe rows
    SPY    5,510 bars   5,510 indicators    1
    VOO        0 bars   4,013 indicators    0
    IBIT       0 bars     656 indicators    0

VOO and IBIT have indicators, no bars, and no `in_trade` universe row. The
sequence explains all three:

| copied | table | state of the source then |
|---|---|---|
| ~03:05 | `universe` (4th) | before ADR 154; VOO/IBIT not `in_trade` |
| ~03:30 | `bars` (6th) | filter is `EXISTS (... u.in_trade)` on the **source** — skipped them |
| ~04:12 | `indicators` (7th) | after ADR 154 — included them |

**Nothing is corrupt and a reader sees nothing wrong**: the Pi's own
`universe` copy also says those two are not `in_trade`, so no surface
selects them.

**It does not repair itself.** Nothing is scheduled -- `Get-ScheduledTask`
has no entry for `nightly`, and every row in `runs` got there from a
hand-run command -- so the Pi keeps this state until someone runs `cscan
nightly` or `cscan sync`. "It repairs at the next sync" is true and
misleading in a system where the next sync is a person remembering.

It is recorded because
the *shape* is a real hazard — a table whose predicate reads another table
gets that predicate evaluated at its own copy time, not at a snapshot.

The clean fix is a **repeatable-read transaction on the source** for the
whole run, so every table is copied from one consistent snapshot. That is a
few lines and it costs a long-lived read transaction, which on a database
also being written by the poller means bloat. Worth measuring before
adopting.

The cheaper mitigation is simply not to change the universe while a sync
runs — which nobody would think to write down, and which is exactly why
this entry exists.

---

### A full `cscan sync` costs 1.5+ hours and 3 GB, mostly to rewrite rows

Measured 2026-08-25: a re-sync to an already-populated Pi ran **1h33m and
climbing**, 44 minutes of CPU, resident memory growing 1.26 GB → 3.0 GB.

Almost all of that work changes nothing. `run_sync` upserts every row in
the window regardless of whether it differs, so a second sync rewrites
~5.7M bar and indicator rows to reach the handful that are new. On this run
the only genuinely new rows were SPY's 5,510 bars and 10,179 ETF indicator
rows.

The memory is `pd.read_sql` materialising a whole table, then
`to_dict("records")` per batch on top of it.

**It is not urgent and it is not free.** ADR 153 now pushes the live tables
every poll tick, so the nightly full sync is no longer the only path to the
serving store — but it still runs nightly, and 1.5 hours inside a nightly
chain is most of the chain.

Two cheap options, neither taken:

- **Bound the large tables by `ts >= last_synced`** rather than by the
  30-year cutoff. The cutoff exists to bound the *window*, not to decide
  what has changed.
- **Chunk the read**, so peak memory is a batch rather than a table.

Worth measuring before choosing: it is possible the cost is dominated by
the network round-trips rather than the reads, in which case only the first
option helps.

---

### `universe` cannot say which config produced it

Raised 2026-08-25. `PRIMARY KEY (ticker, as_of)` and **no `config_hash`
column**, so two configs' membership cannot coexist: evaluating a second
config overwrites the first, row for row.

`events` does carry `config_hash`, and ADR 122 stamps `in_trade` and
`in_watch` onto each event at creation, so an arm's membership survives
inside its events. That is what makes a multi-arm comparison possible at
all. But the `universe` table itself only ever reflects whichever config
ran last.

**Two things follow, and the second is the sharp one.**

The poller builds its ticker list from `universe.in_trade`, and `v_universe`
feeds the site. So after an ablation arm runs, **live membership is that
arm's**, whether or not it is the one meant to be serving. Restoring
production means re-running the universe under the production config, which
is another full 66-quarter pass.

And this is the same defect class as the slope literal fixed the same day:
ADR 060 makes universe definition config, while the table storing that
definition's output cannot record which definition it was. A stale
`universe` and a current one are indistinguishable by inspection.

Adding the column would let arms coexist, make the three-rebuild plan
roughly a third cheaper, and let a reader ask "which config said this".
The cost is a migration plus every reader learning to scope on it --
`core.universe.in_trade`, `_load_pollable_tickers`, `v_universe`,
`v_watchlist`, the features lateral, and the sync subset.

---

### The three ablation arms, and the order they must run in

Decided 2026-08-25. Three rebuilds rather than one, so each change is
attributable:

1. **NYSE at the current definition** -- `config_hash a38d3ca6b58295e8`
2. **`sma200_slope_min = -0.01`** -- admits a flat base. Measured cost at
   2026-06-30: +37 tickers at -1%, +74 at -2%, +167 at -5%.
3. **`crit_rel_return` dropped from `required_criteria`** -- replaced by the
   history it implies. Measured cost: **64 names pass everything else and
   fail on this alone**, including AAPL at $4,250B, UNH $377B, MRK $317B,
   QCOM $195B. Trade universe 184 -> 248, +35%.

Each arm is a 66-quarter universe pass plus a backtest, roughly 6-8 hours,
so **18-24 hours across the three**.

**Run the production arm last**, because of the overwrite above. Otherwise
the live site and the poller serve the last ablation rather than the
chosen definition.

**Worth stating about arm 3.** `crit_rel_return` compares against the
*sector median*, so by construction roughly half of every sector fails it
-- measured 440 pass, 448 fail at 2026-06-30. It is also a momentum filter
inside a mean-reversion study: requiring three-year outperformance selects
recent winners, while the signal looks for dips. Those pull against each
other and the tension has never been measured. Arm 3 is that measurement.

**One consequence to decide with arm 3.** The `history` watch route
requires `crit_rel_return` to be `None`. Replacing the criterion with a
plain history check makes a new ticker return `False` instead, and that
route stops firing.

---

### The hourly fetcher asks for one ticker at a time; the daily one batches

Raised 2026-08-26 while `cscan nightly` sat in `bars_hourly` for 15+
minutes. The two fetchers in `jobs/fetch/yahoo.py` have different shapes and
only one of them needs to:

| | cache key | request |
|---|---|---|
| `_download_daily` | `_batch_key` = `tickers_start_end` | **every ticker in one `yf.download`** |
| `_download_hourly` | `_window_key` = `ticker_start_end` | **one ticker, one 60-day window** |

`_download_hourly` passes a single `ticker`, not a list. `yf.download`
accepts a list at `interval="1h"` exactly as it does for daily, so the
batching is available and simply unused.

**The 60-day cap is not the reason.** Yahoo limits hourly to 60 days *per
request*, so the window walk is mandatory either way. Batching tickers
*within* each window is orthogonal to it: 1,470 tickers x 1 window becomes
one request instead of 1,470.

**The prize is the one daily already collected.** `cscan bars --daily
--lookback 8000` did 521 tickers and 2,002,797 rows in **11 minutes**,
against the 2h20m a per-ticker rate predicts. At `RATE_LIMIT_PER_SEC = 0.5`
the per-ticker hourly path cannot beat ~49 minutes for a single window
across the current universe, and CLAUDE.md records the full backfill at
4.5-5.5 hours.

**Two things to get right, and the first has already cost a session.**

**Bump the `source` string, not just the key function.** `_window_key` is a
promise: for these inputs, this is the answer. Batching changes what a key
means, so it needs `yahoo_hourly_v3`. CLAUDE.md's own account of the
`yahoo_daily` -> `yahoo_daily_v2` episode is the warning: a correct fix
merged, CI passed, and **the next nightly still produced stale data**,
because every cached entry answered the post-fix request with the pre-fix
result. There is no error and a hit is indistinguishable from a fetch
except by duration.

**A batched `yf.download` returns a column MultiIndex keyed by ticker**,
not a flat frame. `_download_daily` already parses that shape, so the code
to copy exists, but it is not a one-line change and the single-ticker
degenerate case behaves differently again.

**Worth measuring first:** whether the nightly hourly step is actually one
window per ticker or several. If several, the win multiplies; if the step
is short in normal operation and only slow now because the universe grew
58%, it may be less urgent than it looks tonight.

---

### VOO and IBIT have no share count, so no market cap — **highest priority**

**Superseded 2026-08-25.** SPY, VOO and IBIT now have `tickers` rows, bars
and indicators. `SPY` was added to both `SEC_NON_FILER_TICKERS` and
`core.training.ETF_TICKERS`, which was the step this entry predicted would
be forgotten.

    SPY    5,510 daily bars   2004-09-29 →   in_trade, mcap $685B
    VOO    4,013 daily bars   2010-09-09 →   no shares, no mcap
    IBIT     656 daily bars   2024-01-11 →   no shares, no mcap

**`cscan shares` resolves neither VOO nor IBIT.** The Yahoo fallback that
serves 68 tickers returns nothing for them, and SEC serves no companyfacts
for an ETF by design. So `mcap_usd` is NULL, `crit_mcap` fails, and both
sit outside trade *and* watch — `watch_reason` requires `crit_mcap` to be
true, deliberately (ADR 149).

VOO fails on **nothing else**: SMA200, its slope and relative return all
pass. One missing input keeps out a $600B S&P 500 tracker. IBIT
additionally sits below its SMA200 with a negative slope and has 656 bars
against the 757 `crit_rel_return` needs, so shares alone would not admit
it.

Not resolved by assigning a plausible number. Invariant 4 and the ADR 148
precedent both say an unresolved value stays blank. A third source for ETF
units outstanding is the real fix, and it is a decision, not a lookup.

**Related, and cheaper to fix:** QQQ and SPY shares both stop at
2021-03-17, so their market caps are computed from five-year-old share
counts. That understates nothing dramatically today but is a silent staleness.

---

### SPY has one quarter of universe history, not sixty-six

Raised 2026-08-25. Only 2026Q2 was evaluated, so SPY participates in live
screening but contributes no historical events to the study population.

Backfilling means `cscan universe --quarter` for each of the other 65
quarters. **Measured at 2m23s per quarter for a four-ticker subset**, so
~2.6 hours — not the ~10s CLAUDE.md quotes, which is a different shape of
run. It belongs in an overnight slot next to the NYSE rebuild rather than
in a working session.

Statistics are unaffected until then: `cell_stats` reads priced backtest
rows, and SPY has none.

---

### Historical: SPY, VOO and IBIT were not in the database

Raised 2026-08-25. `SEC_NON_FILER_TICKERS` names `QQQ`, `VOO` and `IBIT`,
which reads like three ETFs are tracked. **Only QQQ exists.** VOO and IBIT
have no `tickers` row, no bars, no universe evaluation and no events.

That list is a *skip-list*, not a seed: it says "if you see these, do not
ask SEC for companyfacts". Nothing inserts them. QQQ is present because it
was added by hand.

Measured 2026-08-25:

    QQQ    5,282 daily bars   66 universe rows   34,687 events
    VOO    absent
    IBIT   absent

`SPY` is not on the list at all and should be — it is the S&P 500 tracker
and the most obvious ETF in a study seeded from S&P membership.

**The work**, per the path QQQ took:

1. insert the `tickers` rows (no CIK, no sector — an ETF has neither)
2. `cscan bars --tickers SPY,VOO,IBIT --backfill`
3. `cscan indicators --tickers SPY,VOO,IBIT --lookback 8000`
4. re-run `cscan universe` per quarter so they are evaluated
5. add `SPY` to `SEC_NON_FILER_TICKERS` and to `core.training.ETF_TICKERS`

**Step 5 is the one that will be forgotten.** `ETF_TICKERS` is what ADR 147
excludes from training, and it is deliberately a separate list from
`SEC_NON_FILER_TICKERS` — the first answers "is this an instrument rather
than a company", the second "does SEC serve companyfacts". A new ETF added
to one and not the other trains the model on a fund.

**No `config_hash` move** — adding tickers is not a config change. But it
does change the traded population, so ADR 112 wants re-measuring afterwards
for the same reason ADR 148's sector backfill did.

**IBIT is the odd one and is worth a deliberate decision.** A spot Bitcoin
trust has no sector, no industry and no earnings date. ADR 041's
earnings-window exclusion silently does nothing for it, and ADR 147 keeps it
out of training regardless — so it would be tradeable and watchable while
contributing nothing to the model. That is probably the right outcome; it
should be chosen rather than inherited.

---

### NYSE, the same treatment Nasdaq got — **second priority**

Raised 2026-08-25. The seeds are the S&P 500 union and Nasdaq (ADR 143), so
a large NYSE-listed name outside the index is unreachable — not filtered
out, never evaluated.

**The machinery already exists.** ADR 143's path was: fetch the exchange's
listings with market caps, keep common stock above a floor, upsert into
`tickers`, ingest bars, evaluate. `jobs/fetch/nasdaq.py` does exactly that
and hardcodes `"exchange": "NASDAQ"` in one request parameter. The same
endpoint serves NYSE.

**Four things to get right, and three are already solved.**

- **`_is_common` must hold.** The screener gives preferred series, warrants
  and units the *issuer's* market cap, so they clear any floor on their
  parent's size while their bars are a different instrument. That filter
  exists and is tested.
- **Do not reuse `fetch_listed`'s cache key.** It is `@cached(source=
  "nasdaq_screener_v1", key_fn=lambda: "listed_with_mcap")` — a constant. A
  second exchange through the same function returns the Nasdaq snapshot and
  looks like NYSE has no listings. Either a new source string or a
  key that includes the exchange.
- **Sector comes free.** The screener returns one per listing, and ADR 148
  established the crosswalk. Unlike the Nasdaq round, these names need not
  arrive with NULL sectors.
- **`config_hash` moves and a rebuild follows.** Universe definition is
  config (ADR 060). Measured 2026-08-24: ~1h18m compute, 45m harness, plus
  statistics.

**The rule has to be mechanical**, per ADR 035: "every NYSE listing at or
above the floor" qualifies, and could have been written in 2010. Picking
names because they look interesting selects on the outcome the study
measures.

**Expect it to be larger than the Nasdaq round.** NYSE carries most of the
large-cap universe that is not already in the S&P 500 — REITs, foreign
issuers, and the industrial and financial names an index-seeded universe
misses.

---

### Depositary listings have no pre-2018 history

Not a wrong number — a missing one, and the distinction matters when
coverage is quoted.

`is_depositary_listing` switches the share *source* to Yahoo rather than
scaling an ordinary-share count, which is what closed the ADR-ratio class
(NTES from a $1,666.9B peak to $82.1B; zero depositary rows above $1T). But
Yahoo's `shares_full` series starts around 2018, so **360 of 970 depositary
universe rows are NULL**, and those names are absent from the trade universe
before then rather than present at a fabricated size.

Correct under invariant 4, and survivorship-relevant, so it should be said
aloud when ADR coverage is quoted. Deriving the ratio from
`dei:EntityCommonStockSharesOutstanding` against the ADS count would recover
the history — that, not correctness, is now the reason to do it.

---

### 98 tickers still have no sector

ADR 148's backfill resolved 254 of 352. The rest are delisted or renamed —
YHOO, FB (now META), PCLN (now BKNG), TWTR, ATVI, CERN, FRC, SIVB — and
Yahoo 404s on them.

**None reaches the training population**, so this blocks nothing. It stays
recorded because the training frame raises on a missing sector by design
(ADR 147), so a future ticker that fails to resolve will stop a build, and
whoever hits it should find this rather than rediscover it.

---

### Every fire as its own observation, measured both ways

Raised 2026-08-24. The original intent for `events` was that each fire is a
separate observation — averaging down is real, bleeding out of a long is
real, and different entries against a shared exit produce genuinely
different returns. `cell_stats` filters `is_cluster_head`, which quietly
encodes the opposite.

Kept as-is by ADR 151, because dropping the filter is not free.
`n_eff = n / (1 + rho(c_bar - 1))` already corrects **cross-sectional**
dependence, when many tickers fire on one market move. Cluster-head
filtering corrects **serial** dependence, one ticker firing repeatedly
inside a holding window. Removing it without building the serial equivalent
raises `n` and narrows every interval — the direction that manufactures
significance, with no way afterwards to separate a real edge from one move
counted four times.

**What would settle it:** measure both arms and publish the gap. That number
*is* what the overlap is worth, and it is the only honest way to find out
whether the filter costs anything.

---

### Operational, small

**Reserve DHCP leases** for the workstation (192.168.1.14) and the Pi
(192.168.1.30). Both addresses are now written into configuration — the
Pi's into its `pg_hba.conf`, and connection strings on both ends — so a DHCP
reshuffle breaks the sync with an error that reads like an auth failure.

**`cscan nightly` is still manual.** No Task Scheduler entry exists for
`nightly`, `weekly` or the poller; every row in `runs` was hand-typed.
Definitions sit unimported at `scripts/tasks/*.xml`.
