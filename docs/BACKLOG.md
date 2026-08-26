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

### Site auth is deliberately off on the Pi (decided 2026-08-26)

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

### Three nightly fetchers ask one ticker at a time; the daily one batches

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

**`run_actions` is a different and worse problem -- see the entry below.**
It is also per-ticker, but batching it would save almost nothing, because
its cache key never expires and so it almost never fetches.

Measured in the same nightly:

    bars_daily    batched      1,470 tickers    4.9 min
    bars_hourly   per-ticker   1,470 tickers   54.5 min
    actions       per-ticker     531 new only  21.3 min  (rest cached forever)

**~55 minutes of every nightly** goes on a request shape the daily path
already solved, and it grows linearly with the universe -- which just grew
58%. `yf.download` accepts a ticker list at `interval="1h"`, so the fix is
the same one daily already uses.

**`run_earnings` is the third**, and it is Finnhub rather than Yahoo.
`RATE_LIMIT_PER_SEC = 0.8` against 1,462 tickers is **30.5 minutes of pure
waiting** before any overhead; measured 2026-08-26 at ~38 minutes, about 80%
of theoretical.

Finnhub's earnings-calendar endpoint takes a date range without a symbol,
returning every issuer that reports in the window. One call could replace
1,462, filtered locally against the ticker list. That is a different fix
from the Yahoo batching -- a different endpoint, not a list parameter -- but
the same shape of win.

Updated tally, all measured in one nightly on 1,470 tickers:

    bars_daily    batched      4.9 min
    bars_hourly   per-ticker  54.5 min   -> fixed 2026-08-26, ~1 min
    actions       per-ticker  21.3 min   -> cache never expires, see above
    earnings      per-ticker  ~38 min    -> unfixed

**Worth measuring first:** whether the nightly hourly step is actually one
window per ticker or several. If several, the win multiplies; if the step
is short in normal operation and only slow now because the universe grew
58%, it may be less urgent than it looks tonight.

---

### `fetch_actions` caches on the ticker alone, so splits are never refreshed

Raised 2026-08-26. **Correctness, not performance.**

```python
@cached(source="yahoo_actions", key_fn=lambda ticker: ticker)
def fetch_actions(ticker: str) -> pd.DataFrame:
```

The key carries **no date**. Once a ticker's actions are cached the file is
read forever and the network is never touched again. Measured on the live
cache:

    1,490 cached files
    AAPL, ACN, ADBE    2026-07-31    cached 26 days ago, never refreshed
    ZWS, ZTO           2026-08-26    tonight's new NYSE tickers

AAPL's splits and dividends were fetched on 31 July. Every nightly since has
read that file. **A split after that date is invisible**, and stays invisible
until someone deletes the cache by hand.

**Why this matters more than a stale quote.** `bars` are split-adjusted, and
a missed split corrupts price history rather than just aging it. The
indicators computed from those bars are wrong, and nothing raises -- the same
silent-failure shape as ADR 145's adjusted-shares defect.

It also explains the timing: `actions` took 21.3 min tonight rather than the
~49 min 1,470 tickers implies, because only the ~531 new NYSE tickers missed.
On an ordinary night it costs nearly nothing, because it does nearly nothing.

**The fix is a key that can expire**, e.g. `ticker_asof` bucketed to a week
or month, so a refresh happens on a cadence rather than never. That also
makes the batching question moot for this fetcher: a fetch that does not run
does not need to be faster.

CLAUDE.md's `yahoo_daily` -> `_v2` account is the precedent, and this is the
sharper version of it: there the key's *meaning* went stale, here the key can
never expire at all.

---

### `fetch_actions` freezes every ticker's corporate actions forever — **correctness**

Known and listed before; **measured 2026-08-26** and it is worse than the
one-line note suggested.

```python
@cached(source="yahoo_actions", key_fn=lambda ticker: ticker)
def fetch_actions(ticker: str) -> pd.DataFrame:
```

**The key is the ticker alone.** No date, no window. The first fetch of a
ticker answers every later fetch of it, permanently. This is the exact
failure CLAUDE.md's cache section describes -- a key that does not capture
everything determining the output -- and here the missing input is *when
the question is asked*, which for a corporate-action history is the whole
question.

**Proof, from three cohorts that fell out of when each ticker entered the
universe.** Cache-file mtime against the newest `ex_date` those tickers
have:

    cohort cached      tickers    max ex_date        actions after
    2026-07-31           640      2026-07-31              0
    2026-08-21           314      2026-08-20              0
    2026-08-26           533      August present        118 tickers

Each cohort's history stops **exactly at its cache date**. And the fresh
cohort shows 118 of 533 (22%) with an August action, so ~141 of the 640
stale tickers should have one. They have **zero**. Not fewer — none.

**What it breaks, in order of severity.**

`_read_corporate_actions` feeds two consumers, both in the hourly path:
`_back_adjust_hourly`, which divides pre-split bars by the ratio, and
`validate_bars`, which uses splits to tell a legitimate price jump from an
anomaly. A missing split therefore does two harmful things at once -- it
leaves hourly bars unadjusted across the ex-date, and it makes the
validator reject the real bars around it as implausible. Silent in both
directions.

Six splits since 2026-07-01, one of them after the 07-31 freeze:

    SCCO  2026-08-11  1.012

Small ratio, so the damage today is minor. That is luck, not design: a
4-for-1 like `CRWD 2026-07-02` landing after a ticker's freeze would put a
75% discontinuity into its hourly series with no error anywhere.

**The apparent speedup is the bug wearing a disguise.** `actions` ran in
**0.3 minutes** on 2026-08-26 against a documented 21.3, and 94,736 rows
were "written" — all of it read from disk. A correct fetcher costs ~1,470
requests at `RATE_LIMIT_PER_SEC = 0.5`, about **49 minutes**. Anyone
tuning nightly from the 0.3 figure is optimising a cache hit.

**The fix is two changes and they must land together.**

1. Put the date in the key: `key_fn=lambda ticker: f"{ticker}_{date.today():%Y-%m-%d}"`,
   and bump `source` to `yahoo_actions_v2` so no pre-existing entry can
   answer the new question.
2. **Batch it first, or nightly grows by ~49 minutes.** `fetch_actions`
   is one request per ticker; `_fetch_daily_batch` already shows the shape,
   and `bars_hourly` went 54.5 min -> ~1 min on exactly this change.

Doing (1) without (2) is correct and will get reverted for being slow.

---

### `cscan sync` has no incremental path, and it is half of nightly

Measured 2026-08-26. A full sync ships **7,469,519 rows in 114.2 minutes**.
The rows that actually changed since the previous night:

    table         shipped      new
    bars        3,346,546    1,457
    indicators  3,346,546    1,415
    events        684,734    1,003
    total       7,469,519   ~3,875

**A 1,900x amplification**, and roughly **114 of nightly's ~227 minutes**
for 0.05% of the payload.

Two causes compounding.

`run_sync` selects by `cutoff_date`, never by "changed since the last
sync". There is no watermark, no `updated_at` predicate, no window.

And `ServingParams.history_years` is **30**. It was 3, sized against Neon's
512MB free tier; when the Pi replaced Neon the constraint disappeared and
the value was raised, which quietly turned `cutoff_date` into "the
beginning of time". Neither change was wrong on its own. Together they mean
every nightly re-reads the entire served history.

**The pattern already exists here.** `run_live_sync` takes a
`LiveWatermark`, ships only what is past it, and runs ~78 times a session
at no noticeable cost. `run_sync` predates it.

Proposed shape -- a bounded window rather than a strict watermark, because
the sync is an upsert and re-shipping is free:

    run_sync(since_days=7)   # nightly:  ~10k rows, seconds
    run_sync()               # rebuild:  everything, 114 min

Seven days rather than one so a failed night, or a restated bar, heals
itself without anyone noticing. That is the same reasoning `run_indicators`
and `run_events` already use for their 5-day nightly windows.

**What it must not lose.** A full pass is still required when the target is
empty, or when the `config_hash` being served changes -- otherwise the
window ships one day of rows onto a database that has none of the history
they belong to. The 2026-08-26 sync was exactly that case and was correct
to be full.

---

### Watch-universe fires are invisible on the site

Raised 2026-08-26 from a real miss: CCJ fired `confluence_high` at 06:45:40,
the poller logged it, and the home page never showed it.

    CCJ  in_trade=f  in_watch=t
    universe 2026-06-30: crit_above_sma200 = f  (mcap, slope, rel_return pass)

Nothing is broken. `FEED_DOMAIN` in `web/lib/screen.ts` filters `AND
in_trade`, and CCJ is a watch name -- it fell below its 200-day SMA, which
ADR 149's `pullback` route admits to watch rather than trade. The event is
recorded honestly with `in_trade=false` stamped at creation (ADR 122).

**27 of today's 164 fires are in this position**: `pullback` 23, `history`
4. `in_watch` appears **nowhere** in the web layer and in no view
definition, so the whole population is write-only as far as the site is
concerned. That defeats the point of the watch universe, which exists to
be *detected* on while staying out of training.

ADR 149 already requires more than just showing them:

> Notifications for watched names **must say which reason admitted them.**
> A watch alert that looks identical to a tradeable one is how the
> distinction is forgotten.

**Decided 2026-08-26 (user):** stamp `watch_reason` onto `events`, the same
way ADR 122 stamps `in_trade` -- not a read-time join to `universe`. The
reason a fire was admitted is a property of *that fire*, and joining
current membership would silently relabel a March event with today's
reason. Display copy is human-readable, not the enum: `Watch Universe:
Below 200-Day SMA` and `Watch Universe: Insufficient History`.

Scope:

- migration: `events.watch_reason text`; `v_ticker_state` and
  `v_screen_live` project `in_watch` and `watch_reason` (neither exposes
  `in_watch` today)
- backfill: **448,566** rows where `in_watch`
- `run_events` and `poll.py` stamp it going forward
- `FEED_DOMAIN`: `AND (in_trade OR in_watch)` -- one constant, and it feeds
  the screener rows, the calendar counts, the default-date query and the
  last-fire empty state, so all four move together
- `Ticker.tsx:102` becomes three states; today it says "outside trade
  universe" for CCJ, which is true and misleading
- statistics **suppressed** on watch rows. ADR 149: "no statistic reads
  `in_watch`." Cell stats are computed over the trade universe, so printing
  `p_hit` beside a watch row attributes a population's number to a name
  outside it. DESIGN 11.9's `suppressed` state already exists for this.

**Run it before a nightly, never after.** The migration is DDL on `events`,
which `run_events` writes -- ACCESS EXCLUSIVE against a live writer. And
the `run_events` change ships in the same commit, so migrating first means
tonight's rows carry the reason from birth instead of needing a second
backfill.

---

### The Pi is serving the pre-NYSE generation — **highest priority**

Raised 2026-08-26 after a 109.7-minute sync shipped 7,345,158 rows of the
**wrong config**.

    research GUC        f66729c7eda212a4     <- never moved after the rebuild
    serving_config      f66729c7eda212a4

    research events     a38d3ca6b58295e8  1,367,228   <- the NYSE rebuild
                        f66729c7eda212a4  1,110,115   <- what shipped

    Pi events           f66729c7eda212a4    556,185
                        a38d3ca6b58295e8        158   <- poller pushes only

`run_sync` chooses what to copy by reading
`current_setting('capitalscan.default_config_hash')` on the **source**. That
GUC still held the pre-NYSE hash, so a full sync faithfully copied the old
generation and reported `ok`. Nothing failed; the wrong question was asked.

**The ordering is a trap, and it cuts both ways.**
`test_cli_config_resolution.py` records one half: *"The Postgres GUC must not
move until a backtest has written events under the new hash... pointing them
at a config with no rows yet returns an empty screener rather than an
error."* The other half is this: once the backtest **has** written them,
moving the GUC is not optional, and nothing checks that it happened.

**So the repair is not one command.** Running `cscan db sync-config` alone
would point the serving views at a hash the Pi holds **158** rows for, and
the site would go nearly empty until a re-sync finished ~110 minutes later.

The order that works:

1. `cscan db sync-config` (moves `serving_config` and the GUC)
2. `cscan sync` (~110 min, ships the 1,367,228 new events)
3. verify `SELECT config_hash, count(*) FROM events` on the Pi before
   trusting the site

**Worth a guard.** `run_sync` could compare the GUC against the newest
`config_hash` in `events` and refuse, or at least warn, when it is about to
copy a generation older than one that exists. A sync that spends 110 minutes
shipping superseded data should not report `ok` in the same words as one
that shipped the current one.

---

### `run_indicators` holds every ticker's frame in memory before writing

Raised 2026-08-26. It computes all tickers, concatenates the frames, then
converts the whole thing to Python dicts for `db_io.upsert`. Measured on a
1,462-ticker full-history run: **11 GB resident at peak, 2.5 GB free of 32**,
and write throughput decayed from ~900 rows/s to **~46 rows/s** as memory
filled before recovering. Total run 54 minutes, most of it the single write.

`cscan backtest` already has `--chunk-size` for exactly this shape of
problem, checkpointing per chunk. `cscan indicators` has no equivalent, so a
full-history run over a growing universe gets worse every time the universe
grows.

**Chunk the write by ticker group**, the way the backtest chunks by ticker.
That bounds peak memory to one group, keeps throughput at the fast rate, and
makes the job restartable rather than all-or-nothing.

**It also removes a diagnostic trap.** Because nothing is written until every
ticker finishes, a mid-run `count(*)` returns the pre-run number and looks
exactly like a hang. Two working runs were killed for that reason on
2026-08-25.

---

### ORKA is flagged inactive while trading at $6.9B — **known, accepted, do not re-raise**

Raised 2026-08-26. The row contradicts itself and the market:

    is_active     False
    delisted_on   None          <- no delisting date
    name          Oruka Therapeutics Inc. Common Stock
    live screener $104.86, volume 778,973, market cap $6.94B
    bars          2005-10-11 .. 2026-08-20  (5,246)

`is_active = False` with `delisted_on = NULL` is internally inconsistent:
nothing recorded *when* it stopped being active, because it never did.

**The consequence is silent.** `_resolve_tickers(None)` returns active
tickers only, so ORKA is excluded from every job invoked without an explicit
ticker list. It was the single ticker with bars and no indicators after a
full-universe run, and it was found only by comparing `last_indicator` against
`last_bar` rather than by `NOT EXISTS`.

**A second problem sits underneath.** Oruka was formed in 2024 by reverse
merger with ARCA biopharma, so every bar before 2024 is ARCA's price history
under ORKA's ticker. Same class as the depositary pre-2018 entry below, and
worth checking whether other reverse mergers carry the same inherited
history.

**Decided 2026-08-26: leave the flag as it is.** ORKA stays `is_active =
False` and stays out of every job that resolves tickers implicitly. It has 0
events and 0 universe rows, so it contributes nothing to the study either
way, and its pre-2024 bars are ARCA biopharma's, which is a reason to keep it
out rather than a reason to fix it.

**This is written down so it is findable, not so it is re-reported.** A
future session comparing `last_indicator` against `last_bar` will find ORKA
again and it will look like a new discovery. It is not. Do not raise it, do
not re-investigate it, and do not delete the row -- it is currently the only
visible instance of the 82-row flag problem below, and removing it would hide
that class entirely.

**The class is the real item.** 82 tickers carry `is_active = False` with
`delisted_on = NULL`, against 18 that are properly retired with a date. So
for the majority of inactive tickers there is no record of *when* they became
inactive, and "deliberately retired" is indistinguishable from "something set
a flag". Two others in that group still have recent bars -- `FISV` (renamed
to `FI` in 2024) and `UA` (Under Armour's second class) -- and both of those
are legitimately retired, which is what makes ORKA's case distinguishable
only by inspection.

Fixing the class means finding what writes the flag without a date. That is
the work; ORKA is just the thread.

---

### ETF market cap — **investigated 2026-08-26, see ADR 156, not decided**

Raised 2026-08-26, and this probably supersedes the entry below rather than
complementing it. Measured against Yahoo:

    SPY    sharesOutstanding 917,782,016   netAssets   $795B
    QQQ    sharesOutstanding 393,100,000   netAssets   $453B
    VOO    sharesOutstanding None          netAssets $1,687B
    IBIT   sharesOutstanding None          netAssets    $47B

**Yahoo publishes `netAssets` for all four and `sharesOutstanding` for only
two.** So the missing-share-count problem is a missing *field choice*, not
missing data.

And net assets is the **correct** quantity. For a fund, shares times price is
a proxy for assets under management; `netAssets` is the thing itself. Reading
it when `quoteType == 'ETF'` gives VOO a real $1.69T and IBIT $47B instead of
NULL.

That would also make ADR 154's exemption less load-bearing. The funds would
carry genuine market caps and clear `crit_mcap` on merit, rather than being
admitted despite having none. The exemption would still be right for
`crit_rel_return` -- a 2024 fund cannot have 757 sessions -- but the market
cap half would stop being a data gap.

**Superseded by measurement.** `netAssets` and `sharesOutstanding`
**disagree with each other**: for a fund, price times shares *is* net assets
by construction, and it does not hold here.

    SPY   netAssets/price = 1,038,381,651  vs shares 917,782,016  ratio 1.131
    QQQ   netAssets/price =   637,101,310  vs shares 393,100,000  ratio 1.621

Sixty-two percent apart on QQQ. Adopting `netAssets` would move its recorded
market cap from **$289B to $453B**, so this is not filling two NULLs, it is
changing a value that already exists across 66 quarters.

ADR 156 records four options and recommends finding out which field is
right before choosing either. Nothing is blocked meanwhile: ADR 154 already
admits all four ETFs regardless of market cap.

**One clarification, since the shorthand is easy to get wrong.** It is not
that ETFs have no shares: Yahoo reports 917.8M for SPY and 393.1M for QQQ.
The case for `netAssets` is that it is **published for all four** rather than
two, and that it is the quantity itself rather than a proxy -- shares times
price *estimates* assets under management, `netAssets` *is* it.

Needs an ADR when implemented: it changes what `mcap_usd` means for one
instrument class, and `McapPlausibility`'s bounds were written for company
capitalisations.

---

### Superseded: VOO and IBIT have no share count

**Superseded 2026-08-26 by the `netAssets` entry above**, which is the chosen
fix. Kept for the measurements it records. The framing below is wrong in one
respect: the data is not missing, the wrong field was being read.

#### Original entry

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

### 123 tickers still have no sector (29 of them active)

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
