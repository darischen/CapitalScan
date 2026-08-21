# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

## Open

### The deployed site is open — `SITE_AUTH_DISABLED=1` (ADR 138)

**Turned off deliberately 2026-08-20**, at the user's request and with
their reasoning recorded: a Vercel deployment URL is hard to discover, and
the content is public market data plus one operator's own analysis. No
PII, no credentials, no user accounts — there is no user model at all.

**Reaffirmed the same day when the item was reviewed.** The user's argument
is that there is no edge to protect: Bollinger Bands and the stochastic
oscillator have been studied exhaustively by people paid to do it, and
ADR 112 is the house result that no cell here survives correction. The thing
an observer would learn from this site is already public, and the part that
is not public — that the measured edge is absent — is published deliberately.

Implemented as an explicit variable rather than by weakening the default.
`SITE_PASSWORD` unset still returns 503; only `SITE_AUTH_DISABLED=1`
opens the site, and only that exact string — `0`, `true` and `yes` are all
refused, because a loose truthiness check would make `=0` mean "open",
which is the opposite of what someone typing it intends.

**What to check before deciding this is permanent.** `/api/chat` spends
Anthropic tokens per request. It is currently harmless because the route
connects to MCP on `127.0.0.1` and fails *before* any model call — an open
page, not an open wallet. That stops being true the moment MCP is
reachable remotely. If ADR 118's boundary ever moves, restore auth or
carve `/api/chat` out of the opt-out **in the same change**.

**To restore**: delete `SITE_AUTH_DISABLED` in Vercel and redeploy. The
middleware and its 38 tests are unchanged and still enforce everything.

---

### The serving store grows without bound and is at 80% of the free tier

Measured 2026-08-20: **410MB against Neon's 512MB free tier.** A "5 GB"
reading was misread earlier and briefly recorded in ADR 137 as a
correction; that has been withdrawn. 512MB is the limit.

**The real problem is not the current number, it is the direction.**
`run_sync` deliberately never deletes — the docstring says why: a bug in
the cutoff arithmetic would otherwise silently empty the served history.
But that means `ServingParams.history_years` bounds what is *sent*, not
what is *stored*. Rows that age past three years stay on serving forever,
and every nightly adds a day.

So the store only grows, from 80%, on a 512MB ceiling.

**Rough rate**: a session adds roughly 620 bars and 620 indicator rows
across the trade universe plus its events — small daily, and monotonic.
The question is months rather than days, but it has no natural stopping
point.

**What would settle it**, in increasing order of what they claim:

- **Prune on serving**: delete rows older than the cutoff after each sync.
  Straightforward, and it makes the deleted-by-mistake failure mode real
  again — which is exactly what `run_sync` avoided. Would need the delete
  scoped by the same cutoff expression the insert uses, and a test that
  the two cannot drift.
- **Narrow the window**: two years is 266MB, one year 139MB. Buys time and
  costs screener history.
- **Pay**: Neon Launch is 10GB, and full history is 2,149MB.

Not acted on: all three change what the deployed site is or how the sync
behaves, and the store is not full today.

**Reviewed 2026-08-20 and deliberately left.** The user's read is that 100MB
of headroom against a daily increment measured in kilobytes is a long
runway, and that all three fixes cost something real now to solve a problem
that is months away. Revisit when the number moves, not on a schedule.

---

---

### Expanding the universe beyond the S&P 500 seed

**Largely done 2026-08-21 — see ADR 143.** Nasdaq listings at or above $5B
are ingested, `min_mcap_usd` is $20B, and 541 tickers are ever `in_trade`
against 378 before. What remains open is below.

Raised 2026-08-21: the user intends to add other markets and more ETFs,
prompted by NBIS not resolving. NBIS is not excluded by a criterion -- it is
Netherlands-domiciled, so it is not an S&P 500 constituent, so it never
enters `tickers` and is never evaluated at all.

**Most of the machinery already works.** QQQ was added by hand and
participates fully: 5,280 daily bars, 66 universe evaluations, `in_trade`
true at $289B, 29,343 events. Market cap resolved without a CIK because
`shares_outstanding` already has a Yahoo fallback -- 68 of 653 tickers use
it today. The four criteria in `required_criteria` read price, SMA200,
slope and relative return, none of which mention an index.

So the work is not "support non-index names". It is deciding what the
universe *is*, and paying for the change.

**Four things bite, in increasing order of awkwardness.**

**The `config_hash` and the rebuild.** Universe definition is config (ADR
060). Broadening it moves the hash and invalidates every measured row --
another full `cscan backtest`, ~3h31m at the current population.

**ADR 035's survivorship argument does not survive hand-picking.** The S&P
union is *complete*: every historical member is present, including the ones
that failed. A ticker added because it looks interesting today is selected
on an outcome the study is trying to measure. Any expansion needs a rule
that could have been written in 2010 and applied mechanically -- "the
Nasdaq-100 union" is such a rule; "NBIS and a few others" is not.

**Relative return needs a benchmark that means something.** `crit_rel_return`
compares against the S&P series in `market_days`. For a Nasdaq name that is
defensible; for a foreign listing or a sector ETF it quietly changes what
the criterion tests.

**ETFs are not companies.** `sector` and `industry` are NULL on QQQ, and
`cell_id` is built from component columns -- so an ETF either lands in a
NULL-sector cell or needs its own dimension. `days_to_earnings` has no
meaning for one either, and ADR 041's earnings-window exclusion silently
does nothing.

**Still open, and the sharper half.** ETFs are not companies: `sector` and
`industry` are NULL on QQQ, and `cell_id` is built from exactly those
columns, so an ETF lands in a NULL-sector cell rather than being excluded.
`days_to_earnings` is meaningless for one too, so ADR 041's earnings-window
exclusion silently does nothing. **IBIT makes this concrete rather than
hypothetical** -- a spot Bitcoin trust with no sector, no industry and no
earnings date, now on the non-filer list and one `cscan bars` away from
being tradeable and uncellable at once.

**Also still open**: NYSE. The current seeds are the S&P union and Nasdaq,
so a $50B NYSE-listed name outside the index is still unreachable, exactly
as NBIS was.

**What would settle it**: decide whether ETFs get their own cell dimension
or are excluded from cells while remaining tradeable, and pick a mechanical
NYSE rule that could have been written in 2010.

---

---

### `cscan db sync-config` writes only to research, never to serving

Found 2026-08-21, when the deployed site kept showing the previous session
after a `config_hash` change.

`db_sync_config` calls `db_io.get_engine()` and writes one row. That engine
is the *research* store, and there is no second target -- unlike `cscan db
migrate`, which applies to both by default and prints a visible `skip` line
when one is unset. So the serving store's `serving_config` row can only be
updated by a full `cscan sync`, which copies that table as one of its
fourteen.

**Why it is not merely cosmetic.** `web/lib/db.ts` pins every connection
from `serving_config`:

```sql
SELECT set_config('capitalscan.default_config_hash',
                  (SELECT config_hash FROM serving_config LIMIT 1), false)
```

That is ADR 115 working as designed -- the deployed site reads its config
from a table so it cannot drift with whatever a session happens to set. The
consequence is that `ALTER DATABASE ... SET` has **no effect on the web
app**, and a stale `serving_config` row makes the site query a config
generation nobody is writing to. It renders an empty or outdated screener
with no error, because `current_setting(..., true)` returns NULL rather
than raising.

Cost this morning: the site served 2026-08-20 for five hours after the
research GUC moved, and the diagnosis went to the server and the browser
before the table.

**A test already encodes the rule and cannot enforce it here.**
`test_v_positions_config.py::test_the_stored_row_matches_the_live_config`
compares the stored row against the live config -- but it runs against the
research store, so it stays green while serving is stale.

**What would settle it**: give `db_sync_config` the same two-target loop
`db migrate` uses, including the visible skip when `DATABASE_URL_SERVING`
is unset. Small change; the reason it is here rather than done is that it
touches the serving store and this was found mid-session with a poller
running.

---

The file is kept so the next finding has a home. Adding one means saying
what is wrong, what it costs, and what would settle it.
