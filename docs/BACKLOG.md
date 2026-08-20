# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

## Open

Nothing.

The file is kept so the next finding has a home. Adding one means saying
what is wrong, what it costs, and what would settle it — the entries below
are gone because they were settled, not because they were forgotten.

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
