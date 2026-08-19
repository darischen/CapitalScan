import { NextResponse } from "next/server";

import { SEARCH_LIMIT, searchTickers } from "@/lib/ticker";

/**
 * `GET /api/tickers?q=am` — type-ahead over `v_universe`.
 *
 * **Every name, in the trade universe or not.** The ticker page is where
 * you go to look at something you are *not* trading — SMCI being the case
 * that prompted it — so a search that only offered tradeable names would
 * make the page unreachable for exactly the tickers it is most useful for.
 * Each hit carries `inTrade` so the menu can say which is which.
 */

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const q = (params.get("q") ?? "").slice(0, 40);

  try {
    // An empty query returns nothing rather than the six largest companies.
    // `searchTickers` enforces it; this is the endpoint saying the same.
    const hits = q.trim() === "" ? [] : await searchTickers(q, SEARCH_LIMIT);
    return NextResponse.json(
      { tickers: hits },
      // The universe moves quarterly. A minute of caching removes most of
      // the keystroke traffic and cannot show anything meaningfully stale.
      { headers: { "cache-control": "private, max-age=60" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      { error: message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]") },
      { status: 500 },
    );
  }
}
