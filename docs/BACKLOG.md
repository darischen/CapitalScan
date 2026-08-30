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

### ~~Move the poller off research (ADR 158)~~ — **built and deployed 2026-08-28**

**Steps 1-6 are done and on the Pi.** `cscan poll --serving` writes the
serving store, skips the research push, and refuses to start against a
stale target. `sync.pull_live_records` brings `runs` (scoped `job='poll'`),
`signal_reports` and `poller_sessions` back to research inside nightly,
before its sweep. `capitalscan-poller.timer` fires at 00:00 PT and
`scripts/pi/wait_and_poll.sh` waits until 06:45.

**Verified before enabling**, because three of these would have failed
silently:

- `pull_live_records` against live data, run twice: 3 runs, 698 reports, 3
  sessions, and research's counts unmoved the second time (1,656 / 18 / 34)
  -- so it upserts rather than duplicating.
- The staleness guard on real serving data: watermark 2026-08-27 against a
  last trading day of 2026-08-28, one day of lag, passes. That is the
  legitimate case, since nightly syncs after the close.
- **The Pi was holding arm 3's config.** `cscan poll` resolves config, so
  its poller would have written events under `fda16796c6e82ee4` and the
  site -- which reads `serving_config`, pinned to `a38d3ca6b58295e8` --
  would have shown nothing. Reverted and re-checked before the timer was
  enabled.
- The Pi's clock: `America/Los_Angeles`, PDT, matching the workstation to
  the second. `06:45` in the wrapper is 06:45 PT, not UTC.

**What is left**, and it is deliberately left until a live session proves
the change:

1. `_sweep_provisional_poll_rows` against **research** is now dead code.
   Removing it before the migration is proven would make a rollback harder,
   so it stays until a session has run.
2. `wait_and_poll.ps1` is kept for reference and for its CSV export, which
   is read-only against the database. **It must not be started while the Pi
   polls** -- two pollers would double-write, one to research with a push
   and one to serving directly.

#### Original mechanism

Mechanism decided in ADR 158. The work, in order:

1. **`poll.py` takes a target engine.** Today `run_poll` calls
   `db_io.get_engine()` at `poll.py:867` and everything downstream inherits
   research. Thread the engine through instead, defaulting to serving.
2. **Delete `_push_live` and its `run_live_sync` call.** Once the poller
   writes serving natively there is nothing to push. This also removes the
   failure path whose docstring says "never fails the poll".
3. **Drop the research half of the sweep.** `_sweep_provisional_poll_rows`
   against research becomes dead — research never receives provisional
   rows. The serving half stays, and keeps the `sync_ok` guard added
   2026-08-28.
4. **Write the poller's `runs` row to serving.** `events.run_id` has a
   foreign key and serving carries only a narrow `runs` subset.
5. **A serving -> research pull for `signal_reports` and
   `poller_sessions`,** added to nightly *before* its sweep. These two are
   **not** provisional: the sweep preserves reports deliberately and never
   touches sessions, and they are the durable record a past date's
   fired-at timestamps come from (1,656 and 18 rows, 2026-08-03 onward).
   They currently sync research -> serving, so this reverses their
   direction. Omitting it silently stops research accumulating what ADR
   084 has Phase 6 analysing, and the gap would be invisible until someone
   queried data that was never written.
6. **A staleness guard, which is the one new safety requirement.** The
   poller would read `universe` and `indicators` from serving, one sync
   behind. Equivalent during a session today, wrong after a missed sync,
   and silent either way. It should refuse to start when serving's
   watermark predates the last trading day.
7. **`wait_and_poll.ps1` becomes a systemd timer.** The Pi is Linux and
   already up 24/7.

**Measured facts this rests on**, so a future reader does not re-derive
them: the poller reads only `universe`, `indicators`, `market_days` and
`events`, and serving carries all four; it writes `poller_sessions`,
`bars_live`, `events`, `quotes_live`, and serving carries all four.

**What it is not.** Not a heat or CPU fix — the poller sleeps five minutes
between sub-second bursts. Not a route to moving the research database,
which is 22 GB against 27 GB free on the Pi's SD card. Running
`wait_and_poll.ps1` on the Pi *unchanged* buys nothing at all, because it
would still write research over a tunnel originating from the workstation.

The payoff is that the workstation stops being pinned open during market
hours: it can be shut down, updated, developed on, or given a rebuild
without a second writer on `events`.


### ~~`scan_candidates` spends 70% of its time on pandas row access~~

**Closed 2026-08-27: 56.8s -> 31.0s on 29,509 bar rows, 1.83x, identical
16,218 events out.** Two changes, each with the test written first:

- close-confirmed flags attached **once per ticker** instead of written into
  a freshly materialised Series per bar (56.8 -> 32.7s, the bulk of it)
- the t-1 indicator row narrowed from 28 columns to the **five** `detect`
  actually reads (32.7 -> 31.0s)

Narrowing bought far less than an isolated `iloc` microbenchmark predicted
(1.85x there, ~5% in the real loop). Recorded as measured.

**Its real value was a defect.** `test_candidates_indicator_columns.py`
asserts the carried field list against `core/signals.py`'s own source, and
it caught `k_fast` -- read only when `sp.require_fast_agreement` is set, a
**sweepable flag**, so omitting it passes every default-config test and
would have changed the event set only inside an ablation arm that enables
it. Arms 2 and 3 were queued at the time. A hand-maintained list ships that
bug.

`core.signals.detect` is untouched and stays that way: its signature *is*
the look-ahead guarantee (one row, never a frame), so vectorising it means
passing frames, which defeats the signature probe.

Both callers benefit -- `run_backtest`'s compute phase and the harness
ladder, which runs it six times per chunk.

#### Original entry


Profiled 2026-08-27, with numbers, because this is the single largest
remaining cost in the pipeline and the fix is mechanical rather than
clever.

**The harness is 75 minutes and `_lookahead_counts` is ~77% of it.** The
ladder calls `_event_set` six times per chunk -- four shift levels, base,
and the shuffled control -- and each is a full `scan_candidates` over every
bar. One call is **86.2s for 25 tickers / 105,343 bar rows**, so six is
517s per chunk, ~58 minutes over 1,336 tickers on 8 workers. The four
sanity checks together are 10.7s per chunk.

**The six calls are not redundant.** Base, four shifted frames and one
shuffled frame are genuinely different inputs; there is nothing to cache
between them without weakening the test. The cost is one layer down.

**`cProfile` on one `_event_set`, 29,509 bar rows / 6 tickers:**

    82,667,988 function calls in 56.8s     -- 2,800 calls per bar

    scan_candidates                56.8s
      pandas Series.__setitem__    20.3s   (29,389 calls, one per bar)
      pandas .iloc/.loc getitem    20.8s
      core.signals.detect           9.5s   <- the actual work

**Roughly 40 of 57 seconds is pandas row access, not detection.**
`for _, bar in bar_group.iterrows()` materialises a Series per bar, and
something performs a `Series.__setitem__` per bar on top of it. The inner
t-1 lookup was already fixed (`searchsorted`, 125x on that line, 2026-08-21)
-- the per-row loop around it was not.

**Why this is the right target.** `scan_candidates` is the one detection
implementation (invariant 2), so every caller gets the win, and unlike the
ladder itself, making it faster costs nothing in guarantee. Speeding up the
ladder directly -- caching `base`, sampling tickers, reusing scans between
levels -- trades away the look-ahead test, which CLAUDE.md calls the
highest-risk silent failure in the system.

Rough ceiling: if row access becomes vectorised column work, 57s -> 15-20s
would take the harness from ~75 min to ~30 min, the same order as the
hourly-ingest win (54.5 -> 5.4 min).

**Constraint that governs any rewrite.** `detect()` may read only `low`,
`high`, `ts`, `ticker` from the bar and receives **one indicator row, never
a frame**. The signature probe is what makes the look-ahead guarantee real
(CLAUDE.md testing section). A vectorised version must preserve that
boundary or it defeats the test it is speeding up.

Not attempted yet. Write the test first, and measure before/after on the
same data rather than reasoning about it -- three theories about harness
timing were wrong today before this profile settled it.


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

### ~~`events.sector` and `events.mcap_usd` are NULL on every row~~

**CLOSED 2026-08-27.** Both halves. Migration `c2b91e4a7d08` backfilled the serving generation on both databases; `db_io.fill_event_sector_and_mcap` (`1151db7`) populates them on write as a post-pass, the same shape as `add_cofire_count`, so neither writer carries the lookup into its per-ticker workers. Verified live: the rebuild running that night wrote 482,568 rows with **99.9%** carrying both. The ~0.1% that stay NULL are the 123 tickers with no sector on file and events predating their first `universe` evaluation -- absent, not wrong (invariant 4). The asymmetry the entry warned about is now stated in the database itself via column comments: `mcap_usd` is point-in-time, `sector` is the accepted snapshot.

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

### ~~Three of DESIGN §7.3's twenty-two features are not built~~

**Closed 2026-08-27.** `f1c8a260d94e` added the three columns and
`a7d4e91c2b35` backfilled them; both databases are at `a7d4e91c2b35` and
683,562 of 693,024 serving rows carry values. The 9,462 left NULL are
`in_watch` or neither -- tickers whose bars and indicators serving does not
carry, so NULL is correct under invariant 4.

The split into two revisions was not cosmetic: the original combined the
`ADD COLUMN`s with a 1.37M-row `UPDATE`, and Alembic holds a revision's
locks until it commits, so the catalogue-only ALTER's ACCESS EXCLUSIVE was
held for the whole twenty-minute update and the site stopped answering.
**Never put DDL and a long DML in one revision.**

#### Original entry


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

### ~~A long `cscan sync` is not atomic, and the source can move under it~~

**Closed 2026-08-28.** Every read now runs inside one `REPEATABLE READ`,
`READ ONLY` transaction on a single connection, so all fourteen tables see
the same snapshot however long the sync takes. `READ COMMITTED` -- the
default -- takes a fresh snapshot per *statement*, so sharing the
connection alone would have fixed nothing.

`READ ONLY` is belt-and-braces: a sync must never write to research, and
declaring it makes an accidental write fail at the database instead of
succeeding quietly. Postgres takes no locks for this (readers never block
writers under MVCC), so the cost is one long-lived connection.

`test_sync_snapshot.py` pins the mechanism. Proving isolation behaviourally
needs two concurrent sessions against a live database, which the
integration tier may not do here.

#### Original entry


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

### ~~A full `cscan sync` costs 1.5+ hours and 3 GB, mostly to rewrite rows~~

**CLOSED 2026-08-26, `4ad1d41`** — same fix as the incremental entry below. The 3 GB resident set was the same `to_dict("records")` cost.

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

### ~~`universe` cannot say which config produced it~~

**CLOSED 2026-08-27.** Migration `d4a17c93f60b`: `config_hash` added, primary key now `(ticker, as_of, config_hash)`, applied to both databases. Nine readers and three views scoped (`3e87f66`). Pre-existing rows were tagged `'unknown'` rather than guessed at -- `runs` for the universe job recorded only the quarter -- then a tagged 66-quarter pass ran and the unknowns were deleted behind a verification gate. **78,204 rows, all `a38d3ca6b58295e8`, 66 quarters.** Arms can now coexist, so arms 2 and 3 need no universe pass of their own and the production arm no longer has to run last.

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

### ~~The three ablation arms, and the order they must run in~~ — **all three run 2026-08-28**

All three completed. **Zero cells survive FDR in any arm**, and arm 3 — the
widest universe, with the most statistical power of the three — produced the
*worst* min q. See RESULTS.md 2026-08-28. The hypothesis this was built to
test (that the universe filters were excluding tradeable names and suppressing
an edge) is closed.

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

### ~~Three nightly fetchers ask one ticker at a time; the daily one batches~~

**CLOSED 2026-08-26.** All three batched: `bars_hourly` 54.5 min -> ~2 (`4f97d8b`), `actions` 21.3 -> ~2 (`9b008c9`), `earnings` 43.5 min -> 33 seconds (`647ee25`). The earnings one also found that Finnhub truncates a calendar response at 1,500 entries silently, so the naive single bulk call would have dropped AAPL and most of the universe while looking 43 minutes faster.

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

### ~~`fetch_actions` caches on the ticker alone, so splits are never refreshed~~

**CLOSED 2026-08-26, `9b008c9`.** Superseded by the measured entry below, which has the cohort evidence.

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

### ~~`fetch_actions` freezes every ticker's corporate actions forever — **correctness**~~

**CLOSED 2026-08-26, `9b008c9`.** The key carries the date, `source` moved to `yahoo_actions_v2`, and `fetch_actions_many` batches the incremental window so dating the key did not cost 49 minutes. **Still to do: run `cscan actions` once by hand** — the 30-day default window reaches back to 2026-07-27 and the oldest frozen cohort stopped at 2026-07-31, so a single run repairs it.

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

### ~~`cscan sync` has no incremental path, and it is half of nightly~~

**CLOSED 2026-08-26, `4ad1d41`.** Nightly passes `incremental=True`; `cscan sync` still copies everything. The bound is the target's own watermark minus `SYNC_OVERLAP_DAYS`, so a store that missed a fortnight gets a fortnight. Profiling while it ran also settled *why* it was slow, and it was not the obvious answer: the Pi sat at load 1.11 of 4 cores with the SD card 15% utilised, while the workstation held 894 MB and 53.8% of one core building 7.4M Python dicts in `to_dict("records")`.

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

### ~~The nightly sweep deletes serving's rows before the sync can replace them~~

**Closed 2026-08-28.** The serving sweep runs **after** `run_sync` and only
when it succeeded (`sync_ok`). The guard matters as much as the order:
sweeping after a *failed* sync is the original bug with extra steps.

The trade is explicit. Reversed, the worst case is superseded provisional
rows surviving one night -- stale, not absent, and ADR 140 is explicit that
they were never authoritative. The next successful sync clears them.
Absent rows had no such recovery.

`test_nightly_sweep_ordering.py` pins the order, the guard, and the fact
that only the *serving* sweep moved -- research is the source of truth and
its sweep still runs first.

#### Original entry


**Realised 2026-08-26**, first time the sync has failed inside the window.

`cli.py::nightly` runs `_sweep_provisional_poll_rows` against **serving**
and then `run_sync`. The ordering is deliberate and the comment says why:
*"Runs before `run_sync` so the authoritative rows land after the
provisional ones are gone, never the reverse."* Reversed, a half-failed
sync leaves provisional rows it was just told to drop.

But it converts a sync failure into **visible data loss**. That night the
sweep removed the Pi's poller rows for 2026-08-26, the sync died 53.8
minutes in, and serving held **zero** events for the current session while
research held 670. Not stale -- empty. The site showed nothing for today.

**The trigger was physical, not logical.** The Pi is on **WiFi** (`eth0` is
DOWN and has never carried a byte; `wlan0` has received 14.2 GB) with
`brcmf_cfg80211_set_power_mgmt: power save enabled`, and the machine was
being handled to fit a case. Postgres logged `could not receive data from
client: Connection reset by peer` while psycopg logged `server closed the
connection` -- each blamed the other, which is what a torn TCP connection
looks like from both ends. `vcgencmd get_throttled` returned `0x0`, so no
under-voltage was involved.

**Mitigated, not fixed.** The incremental sync cut the transfer from 114
minutes to **1m37s**, shrinking the exposure ~70x. A 97-second window is
still a window, and the failure mode is unchanged.

The durable fix is that the sweep and the sync are one outcome or neither:

- sweep **after** a successful sync rather than before, accepting that a
  half-failed sync briefly leaves superseded provisional rows -- which are
  stale, not absent, and ADR 140 already says they were never authoritative
- or scope the sweep to exactly the rows the sync just wrote over, so a
  sync that never ran deletes nothing

The second is stricter and needs the sync to report what it shipped. The
first is a two-line change and fails toward stale rather than empty.

**Also worth doing regardless**: plug the Pi into Ethernet, and disable
WiFi power save. Neither is a code change and both remove failure classes.

---

### ~~Watch-universe fires are invisible on the site~~

**CLOSED 2026-08-26.** Migration `a4f8c21d7e63` applied to both databases, frontend merged in #42 and deployed to the Pi. Verified on the rendered page: CCJ present with a `watch-mark` span and the label `Watch Universe: Below 200-Day SMA`, no `SCREEN_QUERY_FAILED`. Backfill landed 379,737 `pullback` and 62,983 `history` on research; 5,844 rows stay NULL because they predate the first `universe` snapshot, which is the honest answer. **This overrides `c8d3a1f70b25`'s separate-view design, deliberately** -- recorded in the migration docstring. `v_watchlist` is left in place and should be retired in its own revision.

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

### ~~The Pi is serving the pre-NYSE generation — **highest priority**~~

**CLOSED 2026-08-26.** `db sync-config` moved both `serving_config` rows and the research GUC to `a38d3ca6b58295e8`, and a 114.2-minute sync shipped the generation. Verified independently on the Pi: 341,250 `next_open` + 343,484 `touch`, matching research exactly. Kept for the ordering trap it records.

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

### ~~`run_indicators` holds every ticker's frame in memory before writing~~

**CLOSED 2026-08-26.** Chunked at `INDICATOR_CHUNK_SIZE`, and rows land per chunk rather than once at the end — which also removed the failure where a mid-run `count(*)` looked like a hang and two working runs were killed.

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

### ~~ORKA is flagged inactive while trading at $6.9B~~ — purged 2026-08-27

**Closed by deletion (user's decision, 2026-08-27):** 5,235 bars and the
`tickers` row removed; the 92 `bar_rejects` rows are kept as the audit
trail. It had no `universe` rows, no events and no indicators, so nothing
consumed it.

The entry below said "known, accepted, do not re-raise". What changed is
that a second, worse defect surfaced: ORKA's price series runs from
**$3,757,622 to $6.78** with **zero corporate actions** on file to
reconcile the jumps -- a reverse-merger shell carrying a predecessor's
history. It overflowed `numeric(12,4)` and killed a full-history
`cscan indicators` run at 99 of 153 tickers.

**Only a full-history recompute sees this.** Nightly's 5-day window never
reaches the bad region, which is why it sat undetected. Assume other
tickers hide the same shape.

**No blocklist exists**, so `run_tickers_refresh` can re-add it. It was
already `is_active = false` and that prevented none of this.

#### Original entry


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

### ~~ETF market cap — **investigated 2026-08-26, see ADR 156, not decided**~~

**DECIDED 2026-08-26, not yet built.** ADR 156 -> option B via (i): store the derived share count `netAssets / close` with its own `source`. Still to do, and it wants its own dated migration -- it rewrites QQQ's `mcap_usd` from $289B to $453B across 66 quarters, which is a correction rather than a change (the existing figure comes from a share count frozen at 2021-03-17) but is still a rewrite of recorded history.

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

### ~~Superseded: VOO and IBIT have no share count~~ — resolved 2026-08-27

**Closed.** ADR 156's `netAssets` path is live: `cscan shares` on
2026-08-27 wrote `yahoo_netassets` rows for **VOO (2,395,461,971), IBIT
(1,046,421,772), SPY (1,038,151,224) and QQQ (636,519,171)**. Two universe
tickers now lack shares entirely, down from 68.

#### Original entry


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

**`cscan weekly` is still manual.** `nightly` was registered with Task
Scheduler on 2026-08-27 (13:15 daily, `scripts/run_nightly.ps1`, which
refuses to run if the resolved config hash is not the serving one), and the
poller runs from `capitalscan-poller.timer` on the Pi. `weekly` has no
entry and every `runs` row for it was hand-typed.


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

### Pi-only operation, after the workstation goes away

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

### `max_hold_days` has never been swept, and the exit sweep made it the binding constraint

Raised 2026-08-29, straight out of the fourteen-arm exit sweep.

The sweep varied `target_pct` and the stop and found both matter, but it left
`max_hold_days = 5` untouched in every arm. That is now the parameter doing
the most work:

| config | exits on timeout |
|---|---|
| 4% + k=1.5 (the old default) | 33.1% |
| **5% + k=2.0 (shipped 2026-08-29)** | **51.5%** |
| 5% + no stop (best arm, not shipped) | 72.6% |

**Loosening the stop pushed trades out of the stop and into the clock.** At
the shipped config more than half of all trades now end because five days
elapsed, not because the policy decided anything. The exit rule is
increasingly "hold a week and take what is there", and the length of that
week has never been tested.

**Three arms settle it**: `max_hold_days` 3 / 5 / 10 at the shipped target and
stop. Cheap by the same argument as the stop sweep — one knob, same universe,
and the runner already exists.

**It also bears on the no-stop result.** `t5_nostop` won on mean return but
72.6% of its trades hit the timeout, so what looked like "the stop is costing
money" may partly be "five days is the wrong window". Sweeping the window is
the honest way to separate the two before anyone acts on the stopless result.

### The exit sweep's own leftovers

- **`stop_atr_k` at 2.5 and 1.0 were never run.** The sweep tested 1.5, 2.0
  and none. 2.5 was dropped deliberately — at a 5% target its 6.82% stop
  almost never fires, so it duplicates the no-stop arm — but the *tighter*
  side (k=1.0) was never tested at all, and the monotonic trend predicts it
  should be worse. Cheap confirmation that the trend is real rather than an
  artifact of where the grid happened to start.
- **The no-stop arm's era breakdown was never run.** 2020-2021 loses ~18 bp
  under every arm tested and is the period where a stop should matter most.
  One query against existing data, the same way the target curve's era split
  was done.
