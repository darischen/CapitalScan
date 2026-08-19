import { NextResponse } from "next/server";

import { normalizeSymbol } from "@/lib/format";
import { chartBefore } from "@/lib/ticker";

/**
 * `GET /api/chart?sym=TSM&before=2025-08-19&limit=252` — the sessions
 * immediately before a cursor, oldest first.
 *
 * **The only endpoint in this app**, and it exists because a chart cannot
 * be server-rendered past the edge of what the reader drags to (ADR 121).
 * Everything else on `/` and `/ticker` is a server component.
 *
 * ADR 076 blesses this shape explicitly: *"TypeScript API routes select
 * from those views"*. It runs `chartBefore`, which is the same `v_chart`
 * read the page already does with a different bound, so the endpoint adds
 * a bound and a JSON encoder and no query logic.
 *
 * ADR 118 still holds: no handler, no MCP. A chart page is deterministic
 * retrieval.
 */

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const sym = normalizeSymbol(params.get("sym") ?? "");
  const before = params.get("before") ?? "";
  const limit = params.get("limit");

  if (!sym) {
    return NextResponse.json({ error: "sym is required" }, { status: 400 });
  }

  // Shape-checked before it reaches the query. It is a bound parameter
  // either way — nothing in this app interpolates — but `2026-13-45` would
  // reach Postgres as a cast error rendered to the reader as a failed
  // chart, and this says which field was wrong.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(before)) {
    return NextResponse.json({ error: "before must be YYYY-MM-DD" }, { status: 400 });
  }

  try {
    const bars = await chartBefore(sym, before, limit ? Number(limit) : undefined);
    // An empty array is the start of the ticker's history and the signal to
    // stop asking. It is a 200, not a 404: "there is nothing before 1998"
    // is an answer.
    return NextResponse.json(
      { bars },
      { headers: { "cache-control": "private, max-age=60" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      // Same redaction the pages use. `pg` puts the connection string in
      // some of its messages.
      { error: message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]") },
      { status: 500 },
    );
  }
}
