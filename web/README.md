# CapitalScan web

`/` and `/ticker/[sym]`. TypeScript routes selecting from the Postgres
views (ADR 070, ADR 076, ADR 118).

**MCP is for LLM callers, not for deterministic retrieval.** This app never
calls the Python handlers. `/chat` will, through the MCP server; these
routes read the views directly, and the view is the shared contract that
keeps them in agreement.

## Run

    npm install
    DATABASE_URL_MCP=postgresql://capscan_ro:<pw>@localhost:5432/capitalscan npm run dev

`DATABASE_URL_MCP` is the read-only role from `cscan db grant-readonly`.
Unset, it falls back to `DATABASE_URL_RESEARCH` and the status strip says
`read-write role` so the fallback is visible rather than silent.

## Checks

    npm run typecheck
    npm run test
    npm run build

## What the status strip's two dates mean

    signals 2026-08-13  [trails bars by 5d]  ·  bars 2026-08-17  ·  fresh · 0 sessions

Two different facts, shown separately because they diverge.

`bars` is the last ingested daily bar and drives the staleness badge, in
**trading** days. `signals` is the newest date `v_screen` has a row for.

They come apart because `v_screen` filters `entry_kind = 'next_open'` and
only `cscan backtest` writes that kind — `cscan events` and the poller both
write `touch`. So the screener trails the last full backtest, which is a
five-hour job. See `DECISIONS.md` Open items.
