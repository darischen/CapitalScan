import Link from "next/link";

import { NONE, SIGNAL_LABELS, fmt, pct, signedPct, vol } from "@/lib/format";
import type { Meta } from "@/lib/screen";
import type { Range, TickerEvent, TickerState } from "@/lib/ticker";
import { RANGES } from "@/lib/ticker";

/**
 * The ticker page's server-rendered parts: identity, state, band gauge,
 * event history, and the not-found state.
 *
 * DESIGN §11.6's five states apply here as they do on the screener.
 * Loading is `app/ticker/[sym]/loading.tsx`; empty, stale, suppressed and
 * error are here or on the page.
 */

/* --- header ------------------------------------------------------------ */

export function TickerHeader({
  state,
  meta,
  fired,
}: {
  state: TickerState;
  meta: Meta;
  fired: number;
}) {
  const distance =
    state.close !== null && state.sma200 !== null ? state.close / state.sma200 - 1 : null;

  return (
    <header className="tk-head">
      <div className="tk-id">
        <Link href="/" className="back">
          ‹ screener
        </Link>
        <h1 className="num">{state.ticker}</h1>
        {/* 210 of 712 tickers have no `name` (measured 2026-08-19). An
            em-dash where a company name goes reads as a missing value the
            reader should worry about; the symbol already identifies the
            row, so the element is simply absent. */}
        {state.name && <span className="tk-name">{state.name}</span>}
        {state.sector && <span className="tk-sector">{state.sector}</span>}
      </div>

      <div className="tk-price">
        <span className="num big">{fmt(state.close)}</span>
        <span className={`num ${distance !== null && distance >= 0 ? "up" : "down"}`}>
          {signedPct(distance)} vs 200d
        </span>
      </div>

      <div className="tk-tags">
        {/* `in_trade` is the ADR 001 trade universe, and a ticker outside it
            still charts. Saying so is the point: the events are real and the
            screener will never show them. */}
        <span className={state.inTrade ? "tag on" : "tag off"}>
          {state.inTrade ? "in trade universe" : "outside trade universe"}
        </span>
        {/* The number *listed*, which the history query caps. Saying
            "events" unqualified would read as the ticker's whole history
            and be wrong by the limit. */}
        <span className="tag">{fired} events listed</span>
        <span className="tag dim">as of {state.asOf}</span>
        {meta.stale && (
          <span className="tag flagged">
            {meta.stalenessDays} sessions behind {meta.asOf ?? "?"}
          </span>
        )}
      </div>
    </header>
  );
}

/* --- band gauge -------------------------------------------------------- */

/**
 * Where the close sits between the bands, drawn rather than described.
 *
 * The page's one signature element, and it earns the space because it is
 * the whole strategy in one glyph: every signal in this system is a
 * statement about this position. `bb_pctb` is the number, and a number
 * between 0 and 1 is a thing you have to decode; a tick on a track is a
 * thing you read.
 *
 * Clamped for *drawing only*. Price outside the band is the normal case for
 * a fired signal — %B above 1 is what `bb_upper_touch` means — so the tick
 * pins to the edge and the printed number stays exact.
 */
export function BandGauge({ state }: { state: TickerState }) {
  const b = state.bbPctB;
  const pos = b === null ? null : Math.max(0, Math.min(1, b));
  const outside = b !== null && (b < 0 || b > 1);

  return (
    <div className="gauge">
      <div className="gauge-head">
        <span className="k">Bollinger position</span>
        <span className="num v">{b === null ? NONE : b.toFixed(3)}</span>
      </div>
      <div className="gauge-track" role="img" aria-label={`Bollinger %B ${fmt(b, 3)}`}>
        <span className="gauge-mid" />
        {pos !== null && (
          <span
            className={`gauge-tick ${outside ? "outside" : ""}`}
            style={{ left: `${pos * 100}%` }}
          />
        )}
      </div>
      <div className="gauge-scale num">
        <span>{fmt(state.bbLower)}</span>
        <span>{fmt(state.bbMid)}</span>
        <span>{fmt(state.bbUpper)}</span>
      </div>
    </div>
  );
}

/* --- state rail -------------------------------------------------------- */

function Stat({
  k,
  v,
  cls,
  note,
}: {
  k: string;
  v: string;
  cls?: string;
  note?: string;
}) {
  return (
    <div className="stat">
      <span className="k">{k}</span>
      <span className={`num v ${cls ?? ""}`}>{v}</span>
      {note && <span className="note">{note}</span>}
    </div>
  );
}

/**
 * `v_ticker_state`, one row, as a strip of readouts.
 *
 * Both `%K` series appear here as well as on the chart, with the gap
 * between them printed: ADR 110 gates the signal on the two agreeing within
 * `fast_agreement_tol`, so the gap is the part that decides whether a
 * threshold crossing is a signal at all.
 */
export function StateRail({ state }: { state: TickerState }) {
  const gap =
    state.kFast !== null && state.kFull !== null ? Math.abs(state.kFast - state.kFull) : null;

  return (
    <section className="rail-strip">
      <Stat k="%K fast" v={fmt(state.kFast, 1)} />
      <Stat k="%K slow" v={fmt(state.kFull, 1)} note={gap === null ? undefined : `Δ${gap.toFixed(1)}`} />
      <Stat k="%D" v={fmt(state.dFull, 1)} />
      <Stat
        k="cross"
        v={state.kCrossUp ? "up" : state.kCrossDown ? "down" : "—"}
        cls={state.kCrossUp ? "up" : state.kCrossDown ? "down" : ""}
      />
      <Stat k="drawdown" v={pct(state.ddPct, 1)} />
      <Stat k="band width" v={pct(state.bbWidthPct, 1)} />
      <Stat k="ATR 14" v={fmt(state.atr14)} />
      <Stat k="RV 20d" v={pct(state.rv20d, 1)} />
      <Stat
        k="earnings"
        v={state.daysToEarnings === null ? NONE : `${state.daysToEarnings}d`}
        // ADR 049 excludes an event whose window straddles an announcement.
        // Five sessions is the horizon the grid measures, so inside that the
        // number is a caveat rather than a fact.
        cls={
          state.daysToEarnings !== null && state.daysToEarnings >= 0 && state.daysToEarnings <= 5
            ? "flagged"
            : ""
        }
      />
      <Stat k="VIX" v={fmt(state.vixClose, 1)} />
      <Stat k="volume" v={vol(state.volume)} />
    </section>
  );
}

/* --- range switch ------------------------------------------------------ */

/**
 * What each line on the chart is.
 *
 * Gate item 8 is not "both `%K` series render", it is that they render *and
 * are distinguishable*. Three lines in one panel, two of them within five
 * points of each other by construction (ADR 110's `fast_agreement_tol`), is
 * exactly the case where a reader cannot name a line without being told.
 *
 * HTML rather than a canvas overlay: it is text, it selects, it reads to a
 * screen reader, and it stays in the same stylesheet as everything else.
 */
export function ChartLegend() {
  return (
    <div className="legend">
      <span className="grp">
        <i className="ln band" /> Bollinger 20/2
        <i className="ln band dash" /> 20-day mean
        <i className="ln sma" /> 200-day
      </span>
      <span className="grp">
        <i className="ln kfast" /> %K fast <em>trigger</em>
        <i className="ln kfull dash" /> %K slow
        <i className="ln dfull" /> %D
      </span>
    </div>
  );
}

export function RangeSwitch({ sym, range }: { sym: string; range: Range }) {
  return (
    <nav className="ranges">
      {(Object.keys(RANGES) as Range[]).map((r) => (
        <Link
          key={r}
          href={`/ticker/${sym}?range=${r}`}
          className={r === range ? "on" : undefined}
          aria-current={r === range ? "true" : undefined}
        >
          {r}
        </Link>
      ))}
    </nav>
  );
}

/* --- event history ----------------------------------------------------- */

function typeList(e: TickerEvent): string[] {
  const all = e.signalTypesAll;
  if (all.length === 0) return [e.signalType];
  if (all[0] === e.signalType) return all;
  return [e.signalType, ...all.filter((t) => t !== e.signalType)];
}

/**
 * The outcome of a closed event, or why there is not one.
 *
 * Three distinguishable states, and telling them apart is the reason this
 * is a function rather than a `??`. An event still open is not the same as
 * one that closed flat, and neither is the same as one the backtest has not
 * reached — the poller writes an event the moment it fires and
 * `cscan backtest` fills the outcome hours later.
 */
function Outcome({ e }: { e: TickerEvent }) {
  if (e.netRet !== null) {
    return <span className={`num ${e.netRet >= 0 ? "up" : "down"}`}>{signedPct(e.netRet)}</span>;
  }
  if (e.exitDate !== null) return <span className="dim">closed, no return</span>;
  return <span className="dim">open</span>;
}

export function EventHistory({
  sym,
  events,
  all,
}: {
  sym: string;
  events: TickerEvent[];
  all: boolean;
}) {
  return (
    <section className="history">
      <div className="history-head">
        <h2>Event history</h2>
        <nav className="toggles">
          <Link href={`/ticker/${sym}`} className={all ? undefined : "on"}>
            cluster heads
          </Link>
          <Link href={`/ticker/${sym}?all=1`} className={all ? "on" : undefined}>
            every fire
          </Link>
        </nav>
      </div>

      {events.length === 0 ? (
        <p className="dim pad">
          No events for {sym} under the current config. The chart above is the full
          series; nothing in it crossed a band or a threshold.
        </p>
      ) : (
        <table className="screen">
          <thead>
            <tr>
              <th className="rail" />
              <th>Date</th>
              <th>Signal</th>
              <th className="r">Entry</th>
              <th className="r">Exit</th>
              <th>Reason</th>
              <th className="r">Hold</th>
              <th className="r">Net</th>
              <th className="r">MFE</th>
              <th className="r">MAE</th>
              <th>Bucket</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td className="rail">
                  <span data-side={e.side} />
                </td>
                <td className="num" data-label="Date">
                  {e.signalDate}
                </td>
                <td className="sig" data-label="Signal">
                  {typeList(e).map((t, i) => (
                    <span key={t}>
                      {i > 0 && <span className="sep-dot"> · </span>}
                      <span className={i === 0 ? undefined : "dim"}>{SIGNAL_LABELS[t] ?? t}</span>
                    </span>
                  ))}
                  {/* NULL means the poller wrote it and clustering has not
                      run yet (ADR 054, ADR 119), which is not the same as
                      "not a head". */}
                  {e.isClusterHead === null && <span className="flag">pending cluster</span>}
                  {e.earningsInWindow && <span className="flag">earnings</span>}
                </td>
                <td className="num r" data-label="Entry">
                  {fmt(e.entryPrice)}
                </td>
                <td className="num r" data-label="Exit">
                  {fmt(e.exitPrice)}
                </td>
                <td data-label="Reason">{e.exitReason ?? <span className="dim">{NONE}</span>}</td>
                <td className="num r" data-label="Hold">
                  {e.holdingDays === null ? NONE : `${e.holdingDays}d`}
                </td>
                <td className="num r" data-label="Net">
                  <Outcome e={e} />
                </td>
                <td className="num r" data-label="MFE">
                  {signedPct(e.mfe)}
                </td>
                <td className="num r" data-label="MAE">
                  {signedPct(e.mae)}
                </td>
                <td className="dim" data-label="Bucket">
                  {e.ddBucket ?? NONE}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="foot dim">
        Outcomes are measured on the <strong>touch</strong> entry, matching the chart
        markers. The screener&rsquo;s hit rates are measured on <strong>next_open</strong>,
        which is the entry the cell grid simulated. Same events, two entries.
      </p>
    </section>
  );
}

/* --- not found --------------------------------------------------------- */

/**
 * No row in `v_ticker_state`.
 *
 * It says what it knows and no more. An unknown symbol and a known one with
 * no indicator history are indistinguishable from this view, and claiming
 * "not a valid ticker" for the second would be wrong — measured 2026-08-19,
 * nine inactive symbols carry a handful of bars with no indicators.
 */
export function NoTicker({ sym }: { sym: string }) {
  return (
    <div className="empty">
      <span className="rail" />
      <div className="body">
        <p>
          No indicator history for <strong className="num">{sym}</strong>.
        </p>
        <p className="last">
          Either the symbol is not in this database, or it has bars but no computed
          indicators. <Link href="/">Back to the screener</Link>.
        </p>
      </div>
    </div>
  );
}
