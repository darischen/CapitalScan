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

/** Volume, abbreviated. A ten-digit share count in a dense row is noise. */
function vol(value: number | null): string {
  if (value === null) return "—";
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
  return String(value);
}

/**
 * A timestamp as market-clock time.
 *
 * ET, not the viewer's zone: the reader is looking at a trading session and
 * "09:35" has to mean the open regardless of where they are sitting.
 */
function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
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
          <th className="r" title="Bollinger bands at t-1, the row the signal compared against. Mid is the 20-day SMA.">
            Lower / Mid / Upper
          </th>
          <th className="r" title="Raw %K is the trigger (ADR 110); smoothed must agree within tolerance">
            %K fast / slow
          </th>
          <th className="r" title="The signal date's own bar. Empty until that night's ingest.">
            O / H / L / C
          </th>
          <th className="r">Vol</th>
          <th className="r" title="Newest price the poller saw">Live</th>
          <th className="r">Fired</th>
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
            {/* The t-1 bands, which is the row the signal compared against
                (invariant 3). `bb_mid` is the 20-day SMA as
                `core/indicators.py` computed it; nothing is recomputed
                here. `title` carries the session so a reader can confirm
                which one it is without a column for it. */}
            <td className="r num" data-label="Bands" title={row.bandTs ? `bands at ${row.bandTs}` : undefined}>
              {fmt(row.bbLower)}
              <span className="dim"> / </span>
              {fmt(row.bbMid)}
              <span className="dim"> / </span>
              {fmt(row.bbUpper)}
            </td>
            {/* Both %K series. ADR 110 made the agreement between them part
                of the signal definition: the raw one is the trigger and the
                smoothed one must agree within `fast_agreement_tol`, so
                showing one is showing half the rule. */}
            <td className="r num" data-label="%K">
              {fmt(row.kFast, 1)}
              <span className="dim"> / {fmt(row.kSlow, 1)}</span>
            </td>
            {/* Empty intraday, and that is the honest rendering: bars are
                ingested nightly, so a signal that fired at 09:35 has no bar
                until that evening. Back-filling from quotes would put a
                first-tick price in a column labelled "open". */}
            <td className="r num" data-label="OHLC">
              {row.open === null ? (
                <span className="dim" title="no bar yet; ingested tonight">—</span>
              ) : (
                <>
                  {fmt(row.open)}
                  <span className="dim"> / </span>
                  {fmt(row.high)}
                  <span className="dim"> / </span>
                  {fmt(row.low)}
                  <span className="dim"> / </span>
                  {fmt(row.close)}
                </>
              )}
            </td>
            <td className="r num" data-label="Vol">
              {vol(row.volume)}
            </td>
            <td className="r num" data-label="Live" title={row.livePriceTs ? `as of ${clock(row.livePriceTs)}` : undefined}>
              {fmt(row.livePrice)}
            </td>
            <td className="r num" data-label="Fired">
              {row.firedAt ? clock(row.firedAt) : <span className="dim">—</span>}
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
