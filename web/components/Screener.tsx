import type { CellStats, Meta, ScreenRow, Suppressed } from "@/lib/screen";

/**
 * The screener table and the four states around it.
 *
 * DESIGN §11.6 requires every data component to handle five states
 * explicitly. Loading lives in `app/loading.tsx`, which Next renders while
 * the server component awaits; the other four are here.
 */

const SIGNAL_LABELS: Record<string, string> = {
  bb_lower_touch: "bb lower",
  bb_upper_touch: "bb upper",
  stoch_oversold: "oversold",
  stoch_overbought: "overbought",
  confluence_low: "confluence low",
  confluence_high: "confluence high",
  bear_close_above_upper: "bear close",
};

function fmt(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

function pct(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`;
}

/**
 * Calendar days between two ISO dates, as a rough "how far behind".
 *
 * Deliberately not trading days: this is a gap between two *artifacts*, not
 * a staleness measure, and the exactness would imply a precision the number
 * does not have. `meta.stalenessDays` is the one counted in sessions.
 */
function sessionsBetween(from: string | null, to: string | null): string {
  if (!from || !to) return "?";
  const days = Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000,
  );
  return `${days}d`;
}

export function StatusStrip({
  meta,
  fired,
  signalDate,
}: {
  meta: Meta;
  fired: number;
  signalDate: string | null;
}) {
  // Two dates, deliberately. `signalDate` is what is on screen; `asOf` is how
  // current the database is. They diverge whenever the last backtest is
  // older than the last ingest, and a single date would report the fresher
  // one beside the older rows.
  const trailing = signalDate !== null && meta.asOf !== null && signalDate < meta.asOf;
  return (
    <div className={meta.stale ? "strip stale" : "strip"}>
      <strong className="num" title="signal date shown">
        signals {signalDate ?? "—"}
      </strong>
      {trailing && (
        <span className="stale-badge num" title="v_screen shows entry_kind='next_open', which only cscan backtest writes">
          trails bars by {sessionsBetween(signalDate, meta.asOf)}
        </span>
      )}
      <span className="sep">·</span>
      <span className="num" title="last ingested daily bar">
        bars {meta.asOf ?? "none"}
      </span>
      <span className="sep">·</span>
      <span className="num" title="config_hash">
        {meta.configHash ?? "unconfigured"}
      </span>
      <span className="sep">·</span>
      {/* Staleness in *trading* days. A Monday query against Friday's close
          is zero sessions stale, and counting calendar days would raise this
          every Monday and over every holiday. A banner that is always on is
          a banner that is off. */}
      {meta.stale ? (
        <span className="stale-badge num">
          stale · {meta.stalenessDays} sessions since last bar
        </span>
      ) : (
        <span>
          fresh · <span className="num">{meta.stalenessDays ?? 0}</span> sessions
        </span>
      )}
      <span className="sep">·</span>
      <span>
        <span className="num">{fired}</span> fired
      </span>
      {!meta.readOnly && (
        <>
          <span className="sep">·</span>
          <span title="DATABASE_URL_MCP is unset; reading through the read-write role">
            read-write role
          </span>
        </>
      )}
    </div>
  );
}

/**
 * ADR 026 and DESIGN §11.6: never a number and never a blank. The stored
 * reason, greyed.
 *
 * "Suppressed is the one people get wrong. It is not an error and not
 * empty — it is a valid answer meaning the sample is too thin."
 */
function StatsCell({ stats }: { stats: CellStats | Suppressed | null }) {
  if (stats === null) return <span className="dim">—</span>;
  if (stats.kind === "suppressed") {
    return <span className="suppressed">{stats.reason}</span>;
  }
  return (
    <span className="num">
      {pct(stats.pHit)} vs {pct(stats.baseline)}
      <span className="dim">
        {" "}
        [{fmt(stats.ciLow)}–{fmt(stats.ciHigh)}] n={stats.nEff ?? "—"}
      </span>
      {/* The q-value renders alongside the hit rate, never instead of it and
          never hidden. On the live config no cell survives correction
          (ADR 112), so the reader learns that from the row rather than from
          remembering to check. */}
      {!stats.survivesFdr && stats.qValue !== null && (
        <span className="flag" title="did not survive FDR correction">
          q={stats.qValue.toFixed(3)}
        </span>
      )}
    </span>
  );
}

export function ScreenerTable({
  rows,
  withStats,
}: {
  rows: ScreenRow[];
  withStats: boolean;
}) {
  return (
    <table className="screen">
      <thead>
        <tr>
          <th className="rail" aria-label="side" />
          <th>Ticker</th>
          <th>Signal</th>
          <th className="r">Str</th>
          <th className="r">%B</th>
          <th className="r">%K</th>
          <th>DD</th>
          <th className="r">Cofire</th>
          {withStats && <th>Cell</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.ticker}-${row.signalDate}-${row.signalType}`}>
            <td className="rail">
              {/* The signature element. `title` rather than visible text so
                  the rail stays 3px, with the side still reachable by
                  assistive tech and on hover. */}
              <span data-side={row.side} title={row.side} />
            </td>
            <td className="ticker" data-label="Ticker">
              <a href={`/ticker/${row.ticker}`}>{row.ticker}</a>
            </td>
            <td className="sig" data-label="Signal">
              {SIGNAL_LABELS[row.signalType] ?? row.signalType}
              {row.signalTypesAll.length > 1 && (
                <span className="dim" title={row.signalTypesAll.join(", ")}>
                  {" "}
                  +{row.signalTypesAll.length - 1}
                </span>
              )}
            </td>
            <td className="r num" data-label="Str">
              {row.signalStrength}
            </td>
            <td className="r num" data-label="%B">
              {fmt(row.bbPctb)}
            </td>
            {/* Both %K series. ADR 110 made the agreement between them part
                of the signal definition: `k_fast` is the trigger and
                `k_full` must agree within `fast_agreement_tol`, so showing
                one is showing half the rule. */}
            <td className="r num" data-label="%K">
              {fmt(row.kFast, 1)}
              <span className="dim"> / {fmt(row.kFull, 1)}</span>
            </td>
            <td data-label="DD">
              <span className="num">{row.ddBucket ?? "—"}</span>
            </td>
            <td className="r num" data-label="Cofire">
              {row.cofireCount ?? "—"}
            </td>
            {withStats && (
              <td data-label="Cell">
                <StatsCell stats={row.stats} />
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * DESIGN §11.2: "Empty state matters more than usual, because most days
 * nothing fires."
 *
 * So this is the common view, and it gets the same rail a row does, in
 * `--muted`. The page's structure holds whether or not there is anything in
 * it, which is the difference between "quiet" and "broken".
 */
export function EmptyState({
  last,
}: {
  last: { ticker: string; signalDate: string } | null;
}) {
  return (
    <div className="empty">
      <div className="rail" />
      <div className="body">
        <p>No signals today.</p>
        {last ? (
          <p className="last">
            Last fire: <a href={`/ticker/${last.ticker}`}>{last.ticker}</a>,{" "}
            <span className="num">{last.signalDate}</span>
          </p>
        ) : (
          <p className="last">No events recorded for this config.</p>
        )}
      </div>
    </div>
  );
}

/**
 * DESIGN §11.6: message plus error code, with retry.
 *
 * The message is the exception's own text and never a stack trace. A page
 * that renders a traceback is a page that shows a connection string to
 * whoever is standing behind you.
 */
export function ErrorState({ code, message }: { code: string; message: string }) {
  return (
    <div className="error" role="alert">
      <h2>Could not load the screener</h2>
      <p>{message}</p>
      <p>
        <code>{code}</code>
      </p>
    </div>
  );
}
