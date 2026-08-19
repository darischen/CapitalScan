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
import { normalizeSymbol } from "@/lib/format";
import { readMeta } from "@/lib/screen";
import { chart, events, parseRange, state } from "@/lib/ticker";

/**
 * `/ticker/[sym]` — chart, current state, event history.
 *
 * A server component holding one client component. The three queries run on
 * the server and the browser receives HTML plus the bar array; `pg` never
 * reaches a client bundle, and the chart canvas is the only thing that
 * needs a browser.
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

    const [bars, history, meta] = await Promise.all([
      chart(sym, range),
      events(sym, { all, limit: query.limit ? Number(query.limit) : undefined }),
      readMeta(),
    ]);

    return (
      <main className="wrap tk">
        <TickerHeader state={current} meta={meta} fired={history.length} />
        <div className="tk-top">
          <StateRail state={current} />
          <BandGauge state={current} />
        </div>
        <div className="tk-chart">
          <div className="chart-bar">
            <ChartLegend />
            <RangeSwitch sym={sym} range={range} />
          </div>
          <TickerChart bars={bars} />
        </div>
        <EventHistory sym={sym} events={history} all={all} />
      </main>
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const safe = message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]");
    return (
      <main className="wrap">
        <ErrorState code="TICKER_QUERY_FAILED" message={safe} what={sym} />
      </main>
    );
  }
}
