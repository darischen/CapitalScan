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

### `ServingParams.history_years` is set for a limit that turned out to be 10x larger

ADR 137 chose three years by measuring against **512 MB**, which is what
Neon's free tier was assumed to be. The account's actual limit is **5 GB**,
and the synced store reports **0.45 GB — 9% used**, not the 80% that ADR
recorded.

Every window fits:

```
1 year   139 MB     3 years  393 MB (current)     full  2,149 MB
2 years  266 MB     5 years  638 MB
```

Full history is 43% of the plan. So the three-year cut is now a *choice*
about what the deployed site should show, not a constraint — and the
tradeoff it was making (no screener dates before 2023-08-21, chart ranges
beyond ~2 years stopping early) is no longer being paid for anything.

**What would settle it**: change `history_years` and re-run `cscan sync`.
The job upserts and never deletes, so widening the window adds rows
without disturbing what is there. ADR 137's measurement table should gain
the corrected limit either way, so the next reader does not re-derive a
constraint that does not exist.

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
