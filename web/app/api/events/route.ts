import { NextResponse } from "next/server";

import { normalizeSymbol } from "@/lib/format";
import { EVENT_PAGE, events } from "@/lib/ticker";

/**
 * `GET /api/events?sym=TSLA&offset=40&all=1` — the next page of a ticker's
 * event history, newest first.
 *
 * The list is deep enough to need this: TSLA has 272 `touch` events and the
 * page renders 40, so five of six were unreachable. ADR 121's shape, one
 * axis over.
 *
 * ADR 118 holds: this selects from `v_events`. No handler, no MCP, and no
 * `split` parameter — the route cannot ask for holdout because there is
 * nothing to ask with.
 */

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const sym = normalizeSymbol(params.get("sym") ?? "");
  const offset = Number(params.get("offset") ?? "0");
  const all = params.get("all") === "1";

  if (!sym) {
    return NextResponse.json({ error: "sym is required" }, { status: 400 });
  }
  if (!Number.isFinite(offset) || offset < 0) {
    return NextResponse.json({ error: "offset must be a non-negative number" }, { status: 400 });
  }

  try {
    const rows = await events(sym, { all, limit: EVENT_PAGE, offset });
    // Short page means the end. Returned rather than inferred, so the
    // client stops on the server's answer instead of on a length
    // comparison it has to keep in step with `EVENT_PAGE`.
    return NextResponse.json({ events: rows, done: rows.length < EVENT_PAGE });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      { error: message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]") },
      { status: 500 },
    );
  }
}
