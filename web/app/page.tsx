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

export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string; stats?: string; limit?: string }>;
}) {
  const params = await searchParams;
  const withStats = params.stats === "1";

  try {
    const result = await screen({
      date: params.date,
      withStats,
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
        />
        <main className="wrap">
          {empty ? (
            <EmptyState last={last} />
          ) : (
            <>
              <ScreenerTable rows={result.rows} withStats={result.withStats} />
              <p className="dim" style={{ marginTop: 12, fontSize: 12 }}>
                {/* The pre-limit count. A page showing 200 of 640 rows and
                    saying only "200" misstates how much fired. */}
                Showing <span className="num">{result.rows.length}</span> of{" "}
                <span className="num">{result.totalMatched}</span>
                {" · "}
                <a href={withStats ? "/" : "/?stats=1"}>
                  {withStats ? "hide statistics" : "show statistics"}
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
