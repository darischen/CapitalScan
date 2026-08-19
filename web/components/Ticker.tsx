import Link from "next/link";

import EventRows from "./EventRows";
import TickerSearch from "./TickerSearch";

import { NONE, SIGNAL_LABELS, fmt, pct, signedPct, vol } from "@/lib/format";
import type { Meta } from "@/lib/screen";
import type { ChartBar, Range, TickerEvent, TickerState } from "@/lib/ticker";
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
  latest,
}: {
  state: TickerState;
  meta: Meta;
  fired: number;
  /**
   * The newest bar on the chart, for the compact OHLC beside the as-of
   * date (user's request, 2026-08-19).
   *
   * **Taken from the chart series, not from `v_ticker_state`.** That view
   * projects `close` and `volume` and no other price — adding three
   * columns to it is a migration, and one is not available right now with
   * a `cscan events` rebuild holding the table. The page already loaded
   * these bars, so this costs nothing and cannot disagree with the candles
   * above it, which a second read of the same data eventually would.
   */
  latest?: ChartBar | null;
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

      {/* Between the identity and the price (user's request, 2026-08-19).
          It sits with the thing it changes — the ticker — rather than in a
          global bar, because this is the only route it navigates within. */}
      <TickerSearch />

      <div className="tk-price">
        {/* Says which price this is. It is the last *close*, not a live
            quote, and on a page that also shows an intraday poller price
            the distinction is worth four characters. */}
        <span className="k">close</span>
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
        {/* The ticker's whole history, from a count query — not the page
            length, which would understate it by the page size. */}
        <span className="tag">{fired} events</span>
        <span className="tag dim">as of {state.asOf}</span>
        {/* The session's own bar, compact. The gauge above says where the
            close sits between the bands; this says what the day did to get
            there, which is the next question and was a click away on the
            chart. */}
        {latest && (
          <span className="tag ohlc num">
            <b>O:</b>
            {fmt(latest.open)} <b>H:</b>
            {fmt(latest.high)} <b>L:</b>
            {fmt(latest.low)} <b>C:</b>
            <span
              className={
                latest.close !== null && latest.open !== null && latest.close >= latest.open
                  ? "up"
                  : "down"
              }
            >
              {fmt(latest.close)}
            </span>
          </span>
        )}
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
      {/* "fast stochastic" / "slow stochastic", not "%K fast" / "%K slow"
          (user's request, 2026-08-19). `%K` is the textbook name and it
          tells a reader nothing they do not already know from the number
          being 0-100. `%D` is gone here for the same reason it is gone from
          the chart: nothing in the system reads it. */}
      <Stat k="fast stochastic" v={fmt(state.kFast, 1)} />
      <Stat
        k="slow stochastic"
        v={fmt(state.kFull, 1)}
        note={gap === null ? undefined : `Δ${gap.toFixed(1)}`}
      />
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
 * are distinguishable*. Two lines within five points of each other by
 * construction (ADR 110's `fast_agreement_tol`) is exactly the case where a
 * reader cannot name a line without being told.
 *
 * **`%D` is not here because it is not on the chart** (user's request,
 * 2026-08-19). It is a moving average of the slow `%K`, and nothing in the
 * system reads it: no signal, no exit, no cell dimension. A third line that
 * decides nothing is a third line to tell apart from the two that do.
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
        <i className="ln kfast" /> fast
        <i className="ln kfull" /> slow
        <span className="grp-name">Stochastic</span>
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

/**
 * Why the history stops where it does.
 *
 * **`run_events` only writes an event for a bar where the ticker was in the
 * *trade* universe that day** (`compute.py`, DESIGN §4.7 step 3). A
 * train-universe name therefore has bars, indicators, and band touches, and
 * no events at all — which on this page looked exactly like a broken job.
 * SMCI: 192 band touches since 2024, zero events, and it has never once
 * been in the trade universe across 66 quarterly snapshots.
 *
 * The second half is stranger and is the one worth printing. The earliest
 * `universe` snapshot is **2010-03-31**, and `core.universe.in_trade` fails
 * open when no evaluation exists on or before the bar — the documented v1
 * simplification. So for every bar before that date the filter is vacuous
 * and *every* ticker passes. That is why a name with no trade-universe
 * membership still has a dense block of 2010 Q1 events and nothing after.
 */
function WhyEmpty({ sym, inTrade }: { sym: string; inTrade: boolean | null }) {
  if (inTrade) {
    return (
      <p className="dim pad">
        No events for {sym} under the current config. The chart above is the full
        series; nothing in it crossed a band or a threshold.
      </p>
    );
  }
  return (
    <div className="empty">
      <span className="rail" />
      <div className="body">
        <p>
          <strong className="num">{sym}</strong> is outside the trade universe, so no
          events are recorded for it.
        </p>
        <p className="last">
          Detection runs only on bars where the ticker was in the trade universe that
          day (DESIGN §4.7). The chart above is the full price series and its bands are
          real — the signals simply are not stored, and re-running{" "}
          <code>cscan events</code> will not add them.
        </p>
      </div>
    </div>
  );
}

export function EventHistory({
  sym,
  events,
  all,
  total,
  inTrade = true,
}: {
  sym: string;
  events: TickerEvent[];
  all: boolean;
  /** Every event the ticker has, not the page length. The reader is told
   * how deep the list goes rather than discovering it by scrolling. */
  total: number;
  /** From `v_ticker_state`. Decides which explanation an empty or stale
   * history gets, because the two have different causes and only one of
   * them is worth acting on. */
  inTrade?: boolean | null;
}) {
  // A history that stops years before the newest bar has the same cause as
  // an empty one, and reads far more like a bug. Both get the note.
  const newest = events[0]?.signalDate ?? null;
  const stopped = !inTrade && newest !== null;

  return (
    <section className="history">
      <div className="history-head">
        <h2>Event history</h2>
        <nav className="toggles">
          <Link href={`/ticker/${sym}`} className={all ? undefined : "on"}>
            confluence
          </Link>
          <Link href={`/ticker/${sym}?all=1`} className={all ? "on" : undefined}>
            every fire
          </Link>
        </nav>
      </div>

      {stopped && (
        <p className="note-gate">
          Detection stopped at <span className="num">{newest}</span>: {sym} is outside
          the trade universe, and events are only recorded for bars where a ticker was
          in it that day (DESIGN §4.7). Rows before 2010-03-31 predate the first
          universe snapshot, where the check has nothing to evaluate and admits every
          ticker.
        </p>
      )}

      {events.length === 0 ? (
        <WhyEmpty sym={sym} inTrade={inTrade} />
      ) : (
        <EventRows sym={sym} initial={events} all={all} total={total} />
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
