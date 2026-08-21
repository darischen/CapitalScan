# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

## Open

### The deployed site is open — `SITE_AUTH_DISABLED=1`

**Turned off deliberately 2026-08-20**, at the user's request and with
their reasoning recorded: a Vercel deployment URL is hard to discover, and
the content is public market data plus one operator's own analysis. No
PII, no credentials, no user accounts — there is no user model at all.

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

### `UniverseParams.min_price` is declared and enforced nowhere

Found 2026-08-20 while checking why `cscan nightly` warned that SBNY was
"possibly delisted". `min_price: float = 1.0` appears exactly once in the
tree — its own declaration in `core/config.py`. No criterion reads it and
`is_tradeable` never sees it.

**Same shape as `rebalance_freq` before ADR 135**, which sat unused from
Session 9 until it was given a consumer. A config field that nothing reads
is a claim the system makes and does not honour: someone tuning
`min_price` would change a number, see the hash move, rebuild, and get
identical results.

**Currently harmless, which is why it is here rather than fixed.** The four
enforced criteria already exclude penny stocks through market cap — SBNY
trades at $0.64 with a $0.03B cap and fails `crit_mcap` by three orders of
magnitude. Adding a fifth criterion would change the universe definition
and therefore `config_hash`, invalidating every measured row, to exclude
names that are already excluded.

**What would settle it**: either add `crit_min_price` to
`UniverseParams.required_criteria` and accept the rebuild, or delete the
field and record that market cap subsumes it. Deleting also moves
`config_hash`, so neither option is free — which is the argument for
deciding deliberately rather than drifting.

---

The file is kept so the next finding has a home. Adding one means saying
what is wrong, what it costs, and what would settle it.

---

## Closed 2026-08-20

Five items, all recorded where the reasoning belongs rather than
summarised here.

| Was | Outcome |
|---|---|
| `in_trade` failed open | **ADR 129.** 6h25m of rebuild; 11.9% of the training population left the trade universe. ADR 112 re-established rather than assumed — still zero cells surviving FDR |
| Benchmark record vs database | **Never disagreed.** Both figures were right; neither carried its `config_hash`. Fixed by labelling, not re-measuring — `RESULTS.md` |
| A delisted ticker passed the health filter forever | **ADR 135.** AET passed all four criteria at 2026-06-30 on November 2018 data, 31 quarters with no bars |
| `is_active` / `delisted_on` not derived from `last_bar` | **Done.** 18 names stamped, HUBB and Q re-admitted, 19 junk bars deleted and the fetch guarded |
| Three "documented limitations" | **Two were buildable.** Half-days fixed (`d2f6b48e1a07`) — the calendar already knew. Fonts self-hosted. The edge bar rejected in **ADR 136** |

## Closed 2026-08-19

The poller's four-hour timestamp offset (ADR 127), today's live candle
(ADR 128), the live price reading a stale `quotes_live` row (ADR 131), the
`/chat` and `/` date disagreement (ADR 132), the ticker history's blank
outcomes (ADR 133), the live price outliving the session (ADR 134), and the
Neon sizing objection — which measured 157,915 rows against ADR 053's
~200k estimate and did not hold.
