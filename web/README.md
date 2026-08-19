# CapitalScan web

`/` and `/ticker/[sym]`. TypeScript routes selecting from the Postgres
views (ADR 070, ADR 076, ADR 118).

**MCP is for LLM callers, not for deterministic retrieval.** This app never
calls the Python handlers. `/chat` will, through the MCP server; these
routes read the views directly, and the view is the shared contract that
keeps them in agreement.

## Run

    npm install
    npm run dev

`next.config.ts` reads the repository's root `.env.local` — the same file
`jobs/db.py` reads — so there is one copy of the credentials and no
`web/.env.local` to keep in step. Anything already in the environment wins,
so this still works:

    DATABASE_URL_MCP=postgresql://capscan_ro:<pw>@localhost:5432/capitalscan npm run dev

`DATABASE_URL_MCP` is the read-only role from `cscan db grant-readonly`.
Unset, it falls back to `DATABASE_URL_RESEARCH` and the status strip says
`read-write role` so the fallback is visible rather than silent.

## Checks

    npm run typecheck
    npm run test
    npm run build

`tests/live.test.ts` runs against the real database and **skips itself when
no connection string is present**, which is how CI runs. Locally it is the
only place gate item 1 is actually tested rather than eyeballed, so run the
suite with the env available.

## Routes

| Route | Reads | Query parameters |
|---|---|---|
| `/` | `v_screen_live`, `v_stats`, `v_universe` | `date`, `stats=1`, `all=1`, `limit` |
| `/ticker/[sym]` | `v_chart`, `v_ticker_state`, `v_events` | `range` (6m/1y/2y/5y), `all=1`, `limit` |

Neither accepts a `split` parameter and neither query names `split_key`.
`tests/boundary.test.ts` asserts it against the source.

## What the status strip's two dates mean

    signals 2026-08-18  ·  bars 2026-08-18  ·  fresh · 0 sessions

Two different facts, shown separately because they can diverge.

`bars` is the last ingested daily bar and drives the staleness badge, in
**trading** days. `signals` is the newest date the feed has a row for.

Since ADR 119 the feed reads `v_screen_live`, which is detection-time
(`entry_kind = 'touch'`), so the two usually agree and the "trails bars by
Nd" badge is the exception rather than the rule. It appears when the feed
falls behind the ingest. The reverse also happens and is normal: the poller
writes today's events before that night's bar ingest, so signals can lead
bars by a day.

`v_screen` still exists and still filters `entry_kind = 'next_open'`, which
only `cscan backtest` writes. It is the statistics grain, not the feed's.
