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

**Decided 2026-08-20: delete it, bundled.** The user's reading is that it
may once have been intended as penny-stock protection, that it currently
does nothing and affects nothing, and that it is not worth a rebuild of its
own. So the field comes out in the next change that moves `config_hash` for
a reason that stands on its own, in the same commit, with the ADR for that
change recording that market cap subsumes a price floor.

**This item stays open until that happens** — it is scheduled, not settled,
and the thing that would close it is a commit rather than a decision. If a
hash-moving change lands without removing it, that was a missed chance and
this entry is the reminder.

---

The file is kept so the next finding has a home. Adding one means saying
what is wrong, what it costs, and what would settle it.
