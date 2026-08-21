import OpenSelected from "@/components/OpenSelected";
import { EmptyState, ErrorState, ScreenerTable, StatusStrip } from "@/components/Screener";
import { lastFire, screen } from "@/lib/screen";

/**
 * `/` — today's screener, the default landing route.
 *
 * A server component. The query runs on the server, the browser receives
 * HTML, and `pg` never reaches a client bundle. ADR 070: every endpoint is
 * an indexed SELECT, no computation in the request path.
 *
 * **The default is the event feed** (ADR 114). `?stats=1` adds the cell
 * columns. ADR 112 measured zero cells surviving FDR correction and 100 of
 * 224 train cells suppressed, so four statistical columns would be blank or
 * near-blank on nearly every row, every day, and four always-empty columns
 * teach a reader to skip the row.
 */

// Never cached. `runs` writes through the session and the staleness banner
// is a claim about *now*; a cached page would report a freshness it no
// longer has, which is the one thing the banner exists to prevent.
export const dynamic = "force-dynamic";

/** The two toggles as one URL, so neither clears the other. */
function href({
  withStats,
  confluenceOnly,
}: {
  withStats: boolean;
  confluenceOnly: boolean;
}): string {
  const q = new URLSearchParams();
  if (withStats) q.set("stats", "1");
  if (!confluenceOnly) q.set("all", "1");
  const s = q.toString();
  return s ? `/?${s}` : "/";
}

export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string; stats?: string; limit?: string; all?: string }>;
}) {
  const params = await searchParams;
  const withStats = params.stats === "1";
  // ADR 111's `--confluence-only`, on by default (user's request). `?all=1`
  // clears it, so every signal stays one click away rather than unreachable.
  const confluenceOnly = params.all !== "1";

  try {
    const result = await screen({
      // An empty `?date=` is not a date. `??` would keep `""` and hand it
      // to the query, which is neither a valid date nor a missing one.
      date: params.date || undefined,
      withStats,
      confluenceOnly,
      limit: params.limit ? Number(params.limit) : undefined,
    });

    const empty = result.rows.length === 0;
    const last = empty ? await lastFire() : null;

    return (
      <>
        <StatusStrip
          meta={result.meta}
          fired={result.totalMatched}
          signalDate={result.signalDate}
          prevDate={result.prevDate}
          nextDate={result.nextDate}
          withStats={withStats}
          confluenceOnly={confluenceOnly}
        />
        <main className="wrap">
          {empty ? (
            <EmptyState last={last} />
          ) : (
            <>
              {/* The table stays a server component and is passed through as
                  children; `OpenSelected` only wraps it in a form and reads
                  the checkboxes it rendered (ADR 139). */}
              <OpenSelected>
                <ScreenerTable rows={result.rows} withStats={result.withStats} />
              </OpenSelected>
              <p className="dim" style={{ marginTop: 12, fontSize: 12 }}>
                {/* The pre-limit count. A page showing 200 of 640 rows and
                    saying only "200" misstates how much fired. */}
                Showing <span className="num">{result.rows.length}</span> of{" "}
                <span className="num">{result.totalMatched}</span>
                {result.confluenceOnly && " confluence"}
                {" · "}
                <a href={href({ withStats: !withStats, confluenceOnly })}>
                  {withStats ? "hide statistics" : "show statistics"}
                </a>
                {" · "}
                <a href={href({ withStats, confluenceOnly: !confluenceOnly })}>
                  {confluenceOnly ? "show every signal" : "confluence only"}
                </a>
              </p>
            </>
          )}
        </main>
      </>
    );
  } catch (error) {
    // The exception's own message, never a stack trace, and never the
    // connection string that `pg` puts in some of them.
    const message = error instanceof Error ? error.message : String(error);
    const safe = message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]");
    return (
      <>
        <main className="wrap">
          <ErrorState code="SCREEN_QUERY_FAILED" message={safe} />
        </main>
      </>
    );
  }
}
