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

**What would settle it**: name the rule (Nasdaq-100 union? a liquidity
floor? an explicit ETF list?), decide whether ETFs get their own cell
dimension or are excluded from cells while still being tradeable, and
schedule the rebuild alongside the next hash-moving change rather than on
its own.

---

### `cscan bars --tickers` builds a filename the OS refuses, and exits 0

Found 2026-08-21 ingesting 317 Nasdaq tickers in one invocation.

`jobs/fetch/yahoo.py`'s `_batch_key` is `tickers_start_end` -- every symbol
joined, verbatim, into the cache filename. At 317 tickers that is several
thousand characters, past the Windows path limit, and the write fails:

```
OSError: [Errno 22] Invalid argument:
'...\data\cache\yahoo_daily_v2\AAOI-AAON-ABVX-ACAD-...-CAKE_2005-10-11_2026-08-21.parquet'
```

**The command exits 0 and writes no bars.** The fetch succeeds, the parse
succeeds, and the failure happens on the cache write after the data is in
hand -- so the exit code describes the job and not the outcome, which is the
same shape as the `VACUUM` failure CLAUDE.md already documents.

Worked around by chunking into batches of 20 (longest filename 100 chars).
That is why tonight's ingest ran as 16 invocations rather than one.

**What would settle it**: hash the ticker list into the key --
`sha256(",".join(sorted(tickers)))[:16]` -- so the filename is bounded
regardless of batch size. Sorting also makes `A,B` and `B,A` one cache entry
rather than two, which they should always have been.

**Bumping the source is not required.** CLAUDE.md's rule is that the source
string must move when a fetcher's *output* changes for unchanged arguments.
This changes only where the answer is filed, so existing entries go
unreferenced rather than wrong -- they can be deleted or left to rot. Worth
saying explicitly, because the instinct after reading that section is to bump
the source for any cache change, and doing so here would discard a working
cache for nothing.

---

The file is kept so the next finding has a home. Adding one means saying
what is wrong, what it costs, and what would settle it.
