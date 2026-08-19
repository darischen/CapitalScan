"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

import type { ChartBar } from "@/lib/ticker";

/**
 * DESIGN §11.3's SharpCharts layout, built rather than integrated: price
 * with bands over volume over stochastic, one shared time axis.
 *
 * The only client component in the app. Everything else renders on the
 * server, and this is here because a canvas chart needs a DOM node and a
 * resize observer. The data still comes from the server — the page passes
 * `bars` in as a prop, so no fetch happens in the browser and the RSC
 * payload is the whole contract.
 *
 * **Both `%K` series render.** ADR 110 moved the trigger to `k_fast` with
 * `k_full` required to agree within `fast_agreement_tol`, so the agreement
 * between the two *is* the rule. A stochastic panel showing one line shows
 * half of it. `k_fast` is drawn solid and `k_full` dashed: they sit within
 * five points of each other most of the time and would otherwise read as a
 * single thick line.
 */

/**
 * Chart colours come from the CSS custom properties, read at mount.
 *
 * The chart is a canvas, so it cannot inherit a token the way an element
 * can, and hardcoding the palette here would give the page two sources of
 * truth that drift the first time one is edited. Falls back to the token's
 * own value if the property is missing, which happens in a test renderer
 * with no stylesheet attached.
 */
function tokens(el: HTMLElement) {
  const style = getComputedStyle(el);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    ground: read("--ground", "#14171a"),
    panel: read("--panel", "#1b1f24"),
    rule: read("--rule", "#2a3038"),
    text: read("--text", "#d6dbe1"),
    muted: read("--muted", "#7a848f"),
    long: read("--long", "#4fa8d8"),
    short: read("--short", "#d8734f"),
    flag: read("--flag", "#c9a227"),
    mono: read("--mono", "monospace"),
  };
}

/** Bars with no close cannot be drawn. Dropping is invariant 4's rule for
 * data, and it is the right rendering too: a gap in the line is honest and
 * an interpolated point is not. */
function line(bars: ChartBar[], pick: (b: ChartBar) => number | null) {
  const out: { time: Time; value: number }[] = [];
  for (const b of bars) {
    const v = pick(b);
    if (v !== null) out.push({ time: b.ts as Time, value: v });
  }
  return out;
}

/**
 * One marker per bar that fired, carrying every signal on it.
 *
 * ADR 120 made the marker columns arrays for exactly this: 116 dates hold
 * both a long and a short head, and the two get one marker each rather than
 * one of them being silently dropped by a time-keyed series.
 */
export function markers(
  bars: ChartBar[],
  colors: { long: string; short: string } = { long: "#4fa8d8", short: "#d8734f" },
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const b of bars) {
    b.signalTypes.forEach((type, i) => {
      const side = b.sides[i] ?? "long";
      out.push({
        time: b.ts as Time,
        // A long fires at a low and a short at a high, so the marker sits on
        // the side of the bar the signal came from rather than always above.
        position: side === "long" ? "belowBar" : "aboveBar",
        shape: side === "long" ? "arrowUp" : "arrowDown",
        color: side === "long" ? colors.long : colors.short,
        // Confluence is the only type that earns a glyph. Labelling all
        // seven turns the price pane into a wall of text, and confluence is
        // the one ADR 111 makes actionable.
        text: type.startsWith("confluence") ? "C" : "",
      });
    });
  }
  return out;
}

export default function TickerChart({ bars, height = 520 }: { bars: ChartBar[]; height?: number }) {
  const holder = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = holder.current;
    if (!el || bars.length === 0) return;

    const t = tokens(el);
    const chart: IChartApi = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: t.ground },
        textColor: t.muted,
        fontFamily: t.mono,
        fontSize: 10,
        panes: { separatorColor: t.rule, separatorHoverColor: t.panel },
      },
      grid: {
        vertLines: { color: t.panel },
        horzLines: { color: t.panel },
      },
      rightPriceScale: { borderColor: t.rule, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: t.rule, rightOffset: 4 },
      crosshair: {
        vertLine: { color: t.muted, width: 1, style: 3, labelBackgroundColor: t.panel },
        horzLine: { color: t.muted, width: 1, style: 3, labelBackgroundColor: t.panel },
      },
    });

    // --- pane 0: price, bands, 200-day ---------------------------------
    //
    // Candles in the two direction colours the rest of the app uses, not
    // green and red. DESIGN §11.7: that pairing separates on hue alone and
    // fails for the ~8% of men with a red-green deficiency.
    const price = chart.addSeries(
      CandlestickSeries,
      {
        upColor: t.long,
        downColor: t.short,
        borderUpColor: t.long,
        borderDownColor: t.short,
        wickUpColor: t.long,
        wickDownColor: t.short,
        priceLineVisible: false,
      },
      0,
    );
    price.setData(
      bars
        .filter((b) => b.open !== null && b.high !== null && b.low !== null && b.close !== null)
        .map((b) => ({
          time: b.ts as Time,
          open: b.open as number,
          high: b.high as number,
          low: b.low as number,
          close: b.close as number,
        })),
    );

    const band = (pick: (b: ChartBar) => number | null, dashed: boolean) =>
      chart
        .addSeries(
          LineSeries,
          {
            color: t.muted,
            lineWidth: 1,
            lineStyle: dashed ? 2 : 0,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          },
          0,
        )
        .setData(line(bars, pick));

    band((b) => b.bbUpper, false);
    band((b) => b.bbMid, true);
    band((b) => b.bbLower, false);

    // The 200-day is a regime marker, not a band, so it gets the one
    // remaining accent rather than a fourth grey line nobody can tell from
    // the bands.
    const sma = chart.addSeries(
      LineSeries,
      {
        color: t.flag,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      },
      0,
    );
    sma.setData(line(bars, (b) => b.sma200));

    createSeriesMarkers(price, markers(bars, { long: t.long, short: t.short }));

    // --- pane 1: volume -------------------------------------------------
    //
    // Its own price scale, hidden. Volume is read as texture — is today
    // heavier than the last twenty days — and the axis would print `0.00`
    // beside a bar chart whose units nobody converts in their head.
    const volume = chart.addSeries(
      HistogramSeries,
      {
        color: t.panel,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId: "volume",
      },
      1,
    );
    chart.priceScale("volume", 1).applyOptions({
      visible: false,
      scaleMargins: { top: 0.1, bottom: 0 },
    });
    volume.setData(
      bars
        .filter((b) => b.volume !== null)
        .map((b) => ({
          time: b.ts as Time,
          value: b.volume as number,
          color:
            b.close !== null && b.open !== null && b.close < b.open ? `${t.short}55` : `${t.long}55`,
        })),
    );

    // --- pane 2: stochastic ---------------------------------------------
    const kFast = chart.addSeries(
      LineSeries,
      { color: t.text, lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
      2,
    );
    kFast.setData(line(bars, (b) => b.kFast));

    const kFull = chart.addSeries(
      LineSeries,
      { color: t.long, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false },
      2,
    );
    kFull.setData(line(bars, (b) => b.kFull));

    const dFull = chart.addSeries(
      LineSeries,
      { color: t.muted, lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
      2,
    );
    dFull.setData(line(bars, (b) => b.dFull));

    // The two thresholds, drawn rather than described. `StochParams`
    // oversold/overbought are 20 and 80; they are sweepable, so these lines
    // are a reading aid and the signal itself is never computed here.
    for (const level of [20, 80]) {
      kFast.createPriceLine({
        price: level,
        color: t.rule,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "",
      });
    }

    // Price gets the room. Volume is a texture read, not a value read, so
    // it takes the least.
    const panes = chart.panes();
    if (panes.length >= 3) {
      panes[0].setHeight(Math.round(height * 0.6));
      panes[1].setHeight(Math.round(height * 0.13));
      panes[2].setHeight(Math.round(height * 0.27));
    }
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [bars, height]);

  if (bars.length === 0) {
    return (
      <div className="chart-empty" style={{ height }}>
        <span className="rail" />
        <p>No bars in this range.</p>
      </div>
    );
  }

  return <div className="chart" ref={holder} style={{ height }} />;
}
