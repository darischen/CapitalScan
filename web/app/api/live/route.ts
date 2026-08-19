import { NextResponse } from "next/server";

import { normalizeSymbol } from "@/lib/format";
import { liveBar, liveQuote } from "@/lib/ticker";

/**
 * `GET /api/live?sym=TSM&bar=2026-08-18` — everything on the ticker page
 * that moves during a session: today's partial candle and the last quote.
 *
 * The ticker page is a server component and renders once. The poller
 * rewrites `bars_live` and `quotes_live` every five minutes, so without
 * this the candle *and* the big live price were both snapshots of page-load
 * time that drifted quietly for the rest of the session.
 *
 * **One endpoint for both** because they must move together. Refreshing the
 * candle alone would put a price on the chart that disagrees with the
 * number printed above it, which is worse than two numbers that are stale
 * in agreement.
 *
 * `bar` is the page's current bar date; `liveQuote` needs it to say whether
 * the quote is ahead of the last closed bar. Omitting it is allowed and
 * costs only that flag.
 *
 * Nulls are 200s, not 404s: "the session is closed" and "the poller does
 * not cover this ticker" are both answers, and the page draws nothing for
 * either.
 *
 * ADR 076 and ADR 118 as with `/api/chart` — bounded SELECTs, no handler,
 * no MCP. Deterministic retrieval.
 */

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const sym = normalizeSymbol(params.get("sym") ?? "");
  if (!sym) return NextResponse.json({ error: "sym is required" }, { status: 400 });

  const bar = params.get("bar") ?? "";
  if (bar && !/^\d{4}-\d{2}-\d{2}$/.test(bar)) {
    return NextResponse.json({ error: "bar must be YYYY-MM-DD" }, { status: 400 });
  }

  try {
    const [candle, quote] = await Promise.all([liveBar(sym), liveQuote(sym, bar)]);
    return NextResponse.json(
      { bar: candle, quote },
      // Not cached at all. The whole point is freshness, and a shared cache
      // on a five-minute write would hand back exactly the stale candle
      // this endpoint exists to replace.
      { headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      { error: message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]") },
      { status: 500 },
    );
  }
}
