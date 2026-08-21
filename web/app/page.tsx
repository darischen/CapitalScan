import OpenSelected from "@/components/OpenSelected";
import { EmptyState, ErrorState, ScreenerTable, StatusStrip } from "@/components/Screener";
import { isSortKey, lastFire, screen } from "@/lib/screen";

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

/** The toggles as one URL, so none of them clears the others. */
function href({
  withStats,
  confluenceOnly,
  date,
  sort,
  dir,
}: {
  withStats: boolean;
  confluenceOnly: boolean;
  date: string | null;
  sort: string | null;
  dir: string;
}): string {
  const q = new URLSearchParams();
  if (date) q.set("date", date);
  if (withStats) q.set("stats", "1");
  if (!confluenceOnly) q.set("all", "1");
  // Toggling statistics must not silently reset the column a reader sorted
  // by, nor drop them back to the newest date.
  if (sort) {
    q.set("sort", sort);
    q.set("dir", dir);
  }
  const s = q.toString();
  return s ? `/?${s}` : "/";
}

export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<{
    date?: string;
    stats?: string;
    limit?: string;
    all?: string;
    sort?: string;
    dir?: string;
  }>;
}) {
  const params = await searchParams;
  const withStats = params.stats === "1";
  // ADR 111's `--confluence-only`, on by default (user's request). `?all=1`
  // clears it, so every signal stays one click away rather than unreachable.
  const confluenceOnly = params.all !== "1";
  // An unknown `?sort=` falls back to the default order rather than
  // erroring: a stale bookmark should render the page, not a stack trace.
  const sort = isSortKey(params.sort) ? params.sort : null;
  const dir = params.dir === "asc" ? "asc" : params.dir === "desc" ? "desc" : undefined;

  try {
    const result = await screen({
      // An empty `?date=` is not a date. `??` would keep `""` and hand it
      // to the query, which is neither a valid date nor a missing one.
      date: params.date || undefined,
      withStats,
      confluenceOnly,
      limit: params.limit ? Number(params.limit) : undefined,
      sort,
      dir,
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
                <ScreenerTable
                  rows={result.rows}
                  withStats={result.withStats}
                  sortCtx={{
                    sort: result.sort,
                    dir: result.dir,
                    date: params.date || null,
                    withStats,
                    confluenceOnly,
                  }}
                />
              </OpenSelected>
              <p className="dim" style={{ marginTop: 12, fontSize: 12 }}>
                {/* Every row for the date renders, so this is a count rather
                    than a ratio. It still says "N of M" when an explicit
                    `?limit=` truncated the page, because then the two
                    genuinely differ and a bare count would misstate how much
                    fired. */}
                {result.rows.length === result.totalMatched ? (
                  <>
                    <span className="num">{result.totalMatched}</span>
                    {result.confluenceOnly ? " confluence signals" : " signals"}
                  </>
                ) : (
                  <>
                    Showing <span className="num">{result.rows.length}</span> of{" "}
                    <span className="num">{result.totalMatched}</span>
                    {result.confluenceOnly && " confluence"}
                  </>
                )}
                {" · "}
                <a
                  href={href({
                    withStats: !withStats,
                    confluenceOnly,
                    date: params.date || null,
                    sort: result.sort,
                    dir: result.dir,
                  })}
                >
                  {withStats ? "hide statistics" : "show statistics"}
                </a>
                {" · "}
                <a
                  href={href({
                    withStats,
                    confluenceOnly: !confluenceOnly,
                    date: params.date || null,
                    sort: result.sort,
                    dir: result.dir,
                  })}
                >
                  {confluenceOnly ? "show every signal" : "confluence only"}
                </a>
                {result.sort !== null && (
                  <>
                    {" · "}
                    {/* The way back to the default order. Without it a sort
                        can only be changed, never cleared. */}
                    <a
                      href={href({
                        withStats,
                        confluenceOnly,
                        date: params.date || null,
                        sort: null,
                        dir: result.dir,
                      })}
                    >
                      clear sort
                    </a>
                  </>
                )}
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
