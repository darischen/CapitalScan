import { NextResponse } from "next/server";

import { monthCounts } from "@/lib/screen";

/**
 * `GET /api/dates?month=2026-08-01&all=1` — the dates in a month that have
 * rows, with counts.
 *
 * Feeds the screener's date picker. The counts matter as much as the dates:
 * a calendar that only knew which days were clickable would still make you
 * click one to find out whether it was worth it.
 *
 * ADR 118 holds — a SELECT against `v_screen_live`, no handler, no MCP.
 */

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const month = params.get("month") ?? "";
  const confluenceOnly = params.get("all") !== "1";

  // Shape-checked before it reaches the query. It is a bound parameter
  // either way, but `2026-13-01` would surface as a Postgres cast error
  // rendered to the reader as a broken calendar.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(month)) {
    return NextResponse.json({ error: "month must be YYYY-MM-DD" }, { status: 400 });
  }

  try {
    return NextResponse.json(
      { days: await monthCounts(month, confluenceOnly) },
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
