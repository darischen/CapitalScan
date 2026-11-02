import type { Metadata } from "next";
import { Suspense } from "react";

import HistorySkeleton from "@/components/HistorySkeleton";
import { ErrorState } from "@/components/Screener";
import {
  BandGauge,
  ChartLegend,
  EventHistory,
  NoTicker,
  RangeSwitch,
  StateRail,
  TickerHeader,
} from "@/components/Ticker";
import TickerChart from "@/components/TickerChart";
import TouchNotes from "@/components/TouchNotes";
import { normalizeSymbol } from "@/lib/format";
import { readMeta } from "@/lib/screen";
import type { Prediction } from "@/lib/screen";
import {
  chart,
  countEvents,
  events,
  latestPrediction,
  liveQuote,
  parseRange,
  state,
} from "@/lib/ticker";
import type { TickerEvent } from "@/lib/ticker";

/**
 * `/ticker/[sym]` — chart, current state, event history.
 *
 * A server component holding one client component. The queries run on
 * the server and the browser receives HTML plus the bar array; `pg` never
 * reaches a client bundle, and the chart canvas is the only thing that
 * needs a browser.
 *
 * **Two flushes, not one (2026-11-01).** The header, rail, gauge and chart
 * wait for `v_chart` and nothing else. The event history, its count and
 * the Predict button stream in behind them under their own Suspense
 * boundaries. Measured cold on the Pi, the history read took 0.4-1.8 s and
 * the chart read 0.1-0.4 s: one ticker's events sit on ~2,000 scattered
 * pages of a 7 GB table on an SD card. With one `Promise.all` the chart
 * waited for every one of them.
 *
 * ADR 118: SELECTs against `v_chart`, `v_ticker_state` and `v_events`. No
 * handler and no MCP in this path.
 *
 * **No split parameter, anywhere.** Session gate item 9. `v_events` carries
 * `split_key` and this route never reads it, never filters on it, and
 * exposes no query parameter that could reach it. The Python handlers
 * enforce this with `HoldoutRequested`; here the guarantee is that there is
 * nothing to ask.
 */

export const dynamic = "force-dynamic";

/**
 * Tab title: the ticker, which the root layout's template renders as
 * `TSLA | CapitalScan`.
 *
 * Normalised through `normalizeSymbol` exactly as the page body does, so a
 * lowercase or padded URL cannot put an unnormalised string in the tab
 * while the page shows the clean one. It also bounds what reaches the
 * title: the same regex strips anything outside `[A-Z0-9.-]` and caps the
 * length at 12, so a crafted URL cannot inject markup here.
 *
 * A blank symbol falls back to the bare site name rather than rendering a
 * dangling separator — `/ticker/` with no symbol shows `NoTicker`, and the
 * tab should match.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ sym: string }>;
}): Promise<Metadata> {
  const { sym: raw } = await params;
  const sym = normalizeSymbol(decodeURIComponent(raw));
  return sym ? { title: sym } : { title: { absolute: "CapitalScan" } };
}

export default async function TickerPage({
  params,
  searchParams,
}: {
  params: Promise<{ sym: string }>;
  searchParams: Promise<{ range?: string; all?: string; limit?: string }>;
}) {
  const { sym: raw } = await params;
  const query = await searchParams;
  const sym = normalizeSymbol(decodeURIComponent(raw));
  const range = parseRange(query.range);
  const all = query.all === "1";

  try {
    // `state` decides whether the rest is worth running, so it goes first
    // rather than in the parallel batch. A symbol with no indicator history
    // has no chart and no events either, and three queries against a
    // typo is three round trips for one answer.
    const current = await state(sym);
    if (!current) {
      return (
        <main className="wrap">
          <NoTicker sym={sym} />
        </main>
      );
    }

    // **Call order is pool order.** `query()` asks the pool for its
    // connection before its first `await`, and the pool (four connections)
    // serves requests first come, first served. The chart's four reads go
    // first and take all four, so the history's reads queue behind them
    // instead of the chart's queueing behind a second of SD-card reads.
    const top = Promise.all([chart(sym, range), liveQuote(sym, current.asOf), readMeta()]);

    // Started now and awaited inside the boundaries below, so they run
    // alongside the chart rather than after it.
    const history = events(sym, {
      all,
      limit: query.limit ? Number(query.limit) : undefined,
    });
    const total = countEvents(sym, all);
    // Only the history's Predict button reads this, and a cold
    // `recentRealised` behind it costs 240-360 ms, so it waits with the
    // history and not with the chart. A failed read hides the button, as
    // `recentRealisedOrNone` already does for the comparison inside it,
    // rather than taking the history down with it.
    const prediction = latestPrediction(sym).catch(() => null);
    // Each promise is awaited later, inside a boundary. One that rejects
    // before then would count as unhandled, so each gets a handler now; the
    // boundary still sees the rejection when it awaits.
    history.catch(() => undefined);
    total.catch(() => undefined);

    const [bars, live, meta] = await top;

    return (
      <main className="wrap tk">
        <TickerHeader
          state={current}
          meta={meta}
          fired={
            <Suspense fallback="…">
              <EventCount total={total} />
            </Suspense>
          }
          live={live}
          latest={bars[bars.length - 1] ?? null}
        />
        <div className="tk-top">
          <StateRail state={current} />
          <BandGauge state={current} />
        </div>
        <div className="tk-chart">
          <div className="chart-bar">
            <ChartLegend />
            <RangeSwitch sym={sym} range={range} />
          </div>
          <TickerChart ticker={sym} bars={bars} />
        </div>
        <Suspense fallback={<HistorySkeleton />}>
          <History
            sym={sym}
            all={all}
            inTrade={current.inTrade}
            events={history}
            total={total}
            prediction={prediction}
          />
        </Suspense>
        {/* Reads a `title` out on tap, under 720px only, and renders
            nothing on a wide viewport: the same mount as the screener's.
            Here the notes are the reversal badge's, the watch tag's, the
            live price's poll time, and everything in the model dialog.
            Inside `main` so its hint shares the page gutter. */}
        <TouchNotes />
      </main>
    );
  } catch (error) {
    return (
      <main className="wrap">
        <ErrorState code="TICKER_QUERY_FAILED" message={redacted(error)} what={sym} />
      </main>
    );
  }
}

/** The driver's message can carry the connection string. */
function redacted(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]");
}

/**
 * The header's "N events" figure, streamed into the tag the header has
 * already drawn.
 *
 * A failed count reads `?`. The history boundary awaits the same promise
 * and reports the error in full, so one failure is not announced twice.
 */
async function EventCount({ total }: { total: Promise<number> }) {
  try {
    return <>{await total}</>;
  } catch {
    return <>?</>;
  }
}

/**
 * The event history, once its reads land.
 *
 * Catches its own failure. An error thrown inside a Suspense boundary goes
 * to the nearest error boundary, and this route has none of its own, so an
 * uncaught one would replace the chart that has already rendered with the
 * framework's error page.
 */
async function History({
  sym,
  all,
  inTrade,
  events: rows,
  total,
  prediction,
}: {
  sym: string;
  all: boolean;
  inTrade: boolean | null;
  events: Promise<TickerEvent[]>;
  total: Promise<number>;
  prediction: Promise<Prediction | null>;
}) {
  try {
    const [list, n, latest] = await Promise.all([rows, total, prediction]);
    return (
      <EventHistory
        sym={sym}
        events={list}
        all={all}
        total={n}
        inTrade={inTrade}
        prediction={latest}
      />
    );
  } catch (error) {
    return (
      <ErrorState
        code="TICKER_QUERY_FAILED"
        message={redacted(error)}
        what={`the ${sym} event history`}
      />
    );
  }
}
