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

### NYSE, the same treatment Nasdaq got

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
