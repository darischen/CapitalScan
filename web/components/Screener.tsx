import DatePicker from "./DatePicker";
import { SIGNAL_LABELS, clock, fmt, pct, screenHref, sessionsBetween, vol } from "@/lib/format";
import type { CellStats, Meta, ScreenRow, Suppressed } from "@/lib/screen";

/**
 * The screener table and the four states around it.
 *
 * DESIGN §11.6 requires every data component to handle five states
 * explicitly. Loading lives in `app/loading.tsx`, which Next renders while
 * the server component awaits; the other four are here.
 */

/**
 * Every signal type on the row, most specific first.
 *
 * Falls back to `signal_type` alone when `signal_types_all` is empty, which
 * the type system allows even though the detector never produces it. Puts
 * `signal_type` first if it is somehow not already there, so the emphasis
 * always lands on the type that names the row rather than on whichever
 * element happened to sort first.
 */
function typeList(row: ScreenRow): string[] {
  const all = row.signalTypesAll;
  if (all.length === 0) return [row.signalType];
  if (all[0] === row.signalType) return all;
  return [row.signalType, ...all.filter((t) => t !== row.signalType)];
}

function dayClass(row: ScreenRow): string {
  if (row.close === null || row.open === null) return "";
  if (row.close > row.open) return "up";
  if (row.close < row.open) return "down";
  return "";
}





export function StatusStrip({
  meta,
  fired,
  signalDate,
  prevDate,
  nextDate,
  withStats = false,
  confluenceOnly = true,
}: {
  meta: Meta;
  fired: number;
  signalDate: string | null;
  /** The nearest dates each way that actually have rows. `null` at the ends
   * of the history, where the arrow renders disabled rather than absent —
   * a control that disappears makes the strip reflow. */
  prevDate?: string | null;
  nextDate?: string | null;
  withStats?: boolean;
  confluenceOnly?: boolean;
}) {
  // Two dates, deliberately. `signalDate` is what is on screen; `asOf` is how
  // current the database is. They diverge whenever the last backtest is
  // older than the last ingest, and a single date would report the fresher
  // one beside the older rows.
  const trailing = signalDate !== null && meta.asOf !== null && signalDate < meta.asOf;
  return (
    <div className={meta.stale ? "strip stale" : "strip"}>
      {/* Date navigation (user's request, 2026-08-19). Steps to the next
          date that *has rows under the current filters*, not to the next
          trading day: most sessions fire nothing, and stepping by calendar
          would walk a reader through a run of empty screens.

          `←` is older and `→` is newer, matching the chart's time axis
          rather than a browser's history. */}
      <span className="datenav">
        {prevDate ? (
          <a
            href={screenHref(prevDate, { withStats, confluenceOnly })}
            title={`previous date with rows: ${prevDate}`}
            aria-label={`Previous date with signals, ${prevDate}`}
          >
            ←
          </a>
        ) : (
          <span className="off" aria-hidden="true">
            ←
          </span>
        )}
        {signalDate ? (
          <DatePicker
            date={signalDate}
            withStats={withStats}
            confluenceOnly={confluenceOnly}
          />
        ) : (
          <strong className="num">signals —</strong>
        )}
        {nextDate ? (
          <a
            href={screenHref(nextDate, { withStats, confluenceOnly })}
            title={`next date with rows: ${nextDate}`}
            aria-label={`Next date with signals, ${nextDate}`}
          >
            →
          </a>
        ) : (
          <span className="off" aria-hidden="true">
            →
          </span>
        )}
        {/* Only when you are not on it. On the newest date it would be a
            link to the page you are already reading. */}
        {nextDate && (
          <a className="latest" href={screenHref(null, { withStats, confluenceOnly })}>
            latest
          </a>
        )}
      </span>
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
      {/* The other two surfaces. Pushed right rather than given a nav bar of
          its own: there are three routes, the strip is already the page's
          one persistent element, and a second horizontal band above a dense
          table costs a row of signals to save nothing. */}
      <span className="strip-nav">
        <a href="/research">research</a>
        <a href="/chat">chat</a>
      </span>
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


/**
 * The reversal state, in whichever of its two forms this row has.
 *
 * **Close-confirmed wins when both exist.** `bear_close_above_upper` is a
 * settled fact about a finished session (ADR 108/109); the poller's
 * judgement is a statement about a moment, and once the close has spoken
 * the moment no longer matters.
 *
 * The live form is styled apart from it — dashed, prefixed `live` — because
 * the two carry different certainty and a reader deciding on a short needs
 * to know which one they are looking at. ADR 111 makes the *confirmed* one
 * the actionable condition.
 *
 * The near-miss renders too, with its distance. ADR 117 chose to show every
 * confluence and say how far each is from confirming rather than hide the
 * ones that had not; a badge that appeared only on confirmation would put
 * that decision back.
 */
function ReversalBadge({ row }: { row: ScreenRow }) {
  if (row.signalTypesAll.includes("bear_close_above_upper")) {
    return (
      <span className="reversal" title="closed above the band and below its open: ADR 111's confirming reversal">
        ↓ reversal
      </span>
    );
  }

  const rev = row.reversal;
  if (!rev) return null;

  // **Only where a bear reversal is a thing that could happen.** The poller
  // attaches a `bear_reversal` block to every report regardless of side
  // (`poll.py::_state_json`), so a `confluence_low` carries one too — and
  // `open_gap_atr` is computed from the open and the ATR, which exist on
  // every row. Rendering it there states a short-side near-miss about a
  // long-side signal.
  //
  // It read as *confirming* rather than as noise, which is what makes it
  // worth a guard: DAL fired `confluence_low` on 2026-08-20 and showed
  // `-1.99 ATR vs open`. On a row above its band that large negative is the
  // reversal; on this one the measurement does not apply. 68 of that day's
  // 80 rows were in this state.
  //
  // `above_band` is the field that says so, it has been in the view as
  // `rev_above_band` since ADR 117, and `ReversalState.label` already
  // returns "n/a" for it. The judgement existed at every layer and was
  // dropped at the last one.
  if (!rev.aboveBand) return null;

  // Negative is below the open and therefore reversing.
  const gap = rev.openGapAtr === null ? null : `${rev.openGapAtr.toFixed(2)} ATR vs open`;

  if (rev.confirmed) {
    return (
      <span
        className="reversal live"
        title={`poller at ${clock(rev.ts)} ET: above the band and below today's open${gap ? ` (${gap})` : ""}`}
      >
        ↓ live reversal
      </span>
    );
  }

  // Evaluated and not reversing. Muted, and it carries the distance — this
  // is the row a reader checks to see how close it came.
  return (
    <span
      className="reversal near"
      title={`poller at ${clock(rev.ts)} ET: not reversing${gap ? ` (${gap})` : ""}`}
    >
      {gap ?? "no reversal"}
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
          {/* Its own column rather than a share of the rail: the rail is 3px
              and carries side, which is the table's signature element.
              Select-all lives in the bar below, so this header stays empty. */}
          <th className="pick" aria-label="select" />
          <th className="rail" aria-label="side" />
          <th>Ticker</th>
          <th>Signal</th>
          <th className="r">Str</th>
          <th className="r" title="Bollinger bands at t-1, the row the signal compared against. Mid is the 20-day SMA.">
            Bollinger Lower / Mid / Upper
          </th>
          <th className="r" title="Raw %K is the trigger (ADR 110); smoothed must agree within tolerance">
            fast / slow Stochastic
          </th>
          <th className="r" title="The signal date's own bar. Empty until that night's ingest.">
            Open
          </th>
          <th className="r">High</th>
          <th className="r">Low</th>
          <th className="r" title="Coloured against the open: cool closed up, warm closed down">
            Close
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
            {/* An uncontrolled checkbox, read by `OpenSelected` on demand
                (ADR 139). Keeping it uncontrolled is what lets this table
                stay a server component -- a `checked` prop would need state,
                and state would pull the whole screener into the bundle.

                The label is the ticker alone: a row-selection checkbox is
                announced with its row context already, so "Select ROST"
                would read as "Select ROST checkbox" in a row that just said
                ROST. */}
            <td className="pick">
              <input type="checkbox" name="ticker" value={row.ticker} aria-label={row.ticker} />
            </td>
            <td className="rail">
              {/* The signature element. `title` rather than visible text so
                  the rail stays 3px, with the side still reachable by
                  assistive tech and on hover. */}
              <span data-side={row.side} title={row.side} />
            </td>
            <td className="ticker" data-label="Ticker">
              <a href={`/ticker/${row.ticker}`}>{row.ticker}</a>
            </td>
            {/* Every type, spelled out (user's request, 2026-08-19). The
                previous `+2` made the reader hover to learn what a
                confluence was made of, and left the column narrow enough
                that a gap opened before Str.

                `signal_types_all` is already in ADR 057's specificity
                order, with `signal_type` first -- verified against the live
                data rather than assumed -- so the leading label is the one
                that names the row and the rest are dimmed as context. */}
            <td className="sig" data-label="Signal">
              {typeList(row)
                .filter((t) => t !== "bear_close_above_upper")
                .map((t, i) => (
                  <span key={t}>
                    {i > 0 && <span className="sep-dot"> · </span>}
                    <span className={i === 0 ? undefined : "dim"}>{SIGNAL_LABELS[t] ?? t}</span>
                  </span>
                ))}
              {/* Pulled out of the list and badged (user's request,
                  2026-08-19). ADR 111 makes a short actionable only with a
                  confirming reversal, so this is the one label that changes
                  what a reader does rather than describing what fired — and
                  as the fourth word in a list of four it read like the
                  least important of them.

                  Rare enough to earn the treatment: 116 of 4,306
                  confluences. */}
              <ReversalBadge row={row} />
            </td>
            <td className="r num" data-label="Str">
              {row.signalStrength}
            </td>
            {/* The t-1 bands, which is the row the signal compared against
                (invariant 3). `bb_mid` is the 20-day SMA as
                `core/indicators.py` computed it; nothing is recomputed
                here. `title` carries the session so a reader can confirm
                which one it is without a column for it. */}
            <td className="r num" data-label="Bollinger" title={row.bandTs ? `bands at ${row.bandTs}` : undefined}>
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
            <td className="r num" data-label="Stochastic">
              {fmt(row.kFast, 1)}
              <span className="dim"> / {fmt(row.kSlow, 1)}</span>
            </td>
            {/* Empty intraday, and that is the honest rendering: bars are
                ingested nightly, so a signal that fired at 09:35 has no bar
                until that evening. Back-filling from quotes would put a
                first-tick price in a column labelled "open". */}
            {/* Four columns rather than one slash-separated cell (user's
                request, 2026-08-19). Separate columns let the eye scan one
                series down the page, which a joined cell does not.

                Empty intraday and that is the honest rendering: bars are
                ingested nightly, so a signal that fired at 09:35 has no bar
                until that evening. The dash carries the reason on hover. */}
            <td className="r num" data-label="Open">
              {row.open === null ? (
                <span className="dim" title="no bar yet; ingested tonight">
                  —
                </span>
              ) : (
                fmt(row.open)
              )}
            </td>
            <td className="r num dim" data-label="High">
              {fmt(row.high)}
            </td>
            <td className="r num dim" data-label="Low">
              {fmt(row.low)}
            </td>
            {/* The one coloured number in the bar. Cool closed above its
                open, warm closed below -- the same cool/warm the side rail
                uses, so one palette carries direction everywhere rather
                than two conventions competing. Green/red is avoided for the
                reason §11.7 gives: it fails for a red-green deficiency and
                separates on hue alone. */}
            <td className={`r num ${dayClass(row)}`} data-label="Close">
              {fmt(row.close)}
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
export function ErrorState({
  code,
  message,
  what = "the screener",
}: {
  code: string;
  message: string;
  /** What failed. The ticker page reuses this component, and a chart error
   * announcing "could not load the screener" sends the reader to look at
   * the wrong page. */
  what?: string;
}) {
  return (
    <div className="error" role="alert">
      <h2>Could not load {what}</h2>
      <p>{message}</p>
      <p>
        <code>{code}</code>
      </p>
    </div>
  );
}
