"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

import { fmt, vol } from "@/lib/format";
import type { ChartBar } from "@/lib/ticker";

/**
 * DESIGN §11.3's SharpCharts layout, built rather than integrated: price
 * with bands over volume over stochastic, one shared time axis.
 *
 * The only client component in the app. Everything else renders on the
 * server, and this is here because a canvas chart needs a DOM node, a
 * resize observer, and — since ADR 121 — a scroll handler that fetches.
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
 * One marker per event, not per bar.
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

/**
 * Merge an older page in front of what is loaded, dropping any overlap.
 *
 * The query is strict (`ts::date < $2`) so an overlap should not happen.
 * Guarding anyway: `setData` on a series with a duplicate or out-of-order
 * timestamp throws, which would blank the chart on a scroll gesture rather
 * than skip a bar.
 */
export function prepend(older: ChartBar[], loaded: ChartBar[]): ChartBar[] {
  if (older.length === 0) return loaded;
  const known = new Set(loaded.map((b) => b.ts));
  const fresh = older.filter((b) => !known.has(b.ts));
  if (fresh.length === 0) return loaded;
  return [...fresh, ...loaded];
}

/**
 * How far past the oldest loaded bar the viewport must reach before the
 * next page is requested. **Zero: the viewport has to actually extend into
 * the blank.**
 *
 * The obvious threshold is a positive one — "within 40 bars of the left
 * edge" — and it is what the common recipe for this uses. It is wrong here
 * and the network log said so: `fitContent()` puts the whole loaded array
 * on screen, so `from` is 0 at rest and the test is true the moment the
 * chart mounts. `?range=6m` fetched a year before anyone touched it, which
 * makes the range switch decorative.
 *
 * A negative `from` means the viewport extends left of the first bar — the
 * reader is looking at empty space where older data belongs. That is an
 * unambiguous request for more, it needs no "has the user interacted yet"
 * flag, and it cannot fire at rest. The cost is a moment of blank before
 * the page lands, and the query is ~10 ms.
 */
const PREFETCH_BARS = 0;

/**
 * How often the open session's candle is re-read, in milliseconds.
 *
 * The poller writes `bars_live` on a five-minute cycle, so anything under
 * that returns the same row. 45 seconds is deliberately faster than the
 * write: it bounds how long a fresh price sits in the database unseen to
 * well under a minute, at the cost of a bounded SELECT against a primary
 * key. Matching the five minutes exactly would put the worst case at ten.
 */
const LIVE_POLL_MS = 45_000;


/**
 * The top of each pane, in pixels from the top of the chart container.
 *
 * `priceToCoordinate` returns a y **relative to the series' own pane**, and
 * the overlay is positioned against the container, so the two need adding.
 * There is no public API for a pane's offset, and computing it from
 * `paneSize()` means also guessing the separator heights.
 *
 * So it is measured. Each pane paints into full-width canvases; the price
 * axis paints into narrow ones. Taking the distinct `top` of every canvas
 * at least half the container's width gives the pane boundaries directly,
 * with no assumption about the table markup beyond "panes have canvases".
 * Measured on TSM at 826px: [0, 352, 378, 494] for price, volume,
 * stochastic, and the time axis.
 */
function paneTops(holder: HTMLElement): number[] {
  const base = holder.getBoundingClientRect();
  const tops = new Set<number>();
  for (const canvas of holder.querySelectorAll("canvas")) {
    const box = canvas.getBoundingClientRect();
    if (box.width > base.width / 2) tops.add(Math.round(box.top - base.top));
  }
  return [...tops].sort((a, b) => a - b);
}

/** What the crosshair is over. `null` when it leaves the chart. */
interface Hover {
  x: number;
  ohlc: { open: number; high: number; low: number; close: number } | null;
  volume: number | null;
  date: string;
  fast: { value: number; y: number } | null;
  slow: { value: number; y: number } | null;
  /** The band and 200-day levels at the same bar, keyed by their class so
   * each tag takes its own line's colour. Absent entries are bars inside
   * the indicator warm-up, where the level genuinely does not exist. */
  levels: { key: string; value: number; y: number }[];
}

interface Series {
  price: ISeriesApi<"Candlestick">;
  /** Today's partial candle (ADR 128), drawn hollow. A second series
   * rather than a per-bar colour: body colour is per point in
   * `lightweight-charts`, but border and wick styling is a series option,
   * and an outlined body is what reads as "not finished". */
  partial: ISeriesApi<"Candlestick">;
  bands: ISeriesApi<"Line">[];
  sma: ISeriesApi<"Line">;
  volume: ISeriesApi<"Histogram">;
  kFast: ISeriesApi<"Line">;
  kFull: ISeriesApi<"Line">;
  markers: ISeriesMarkersPluginApi<Time>;
  colors: { long: string; short: string };
}

export default function TickerChart({
  ticker,
  bars: initial,
  height = 520,
}: {
  ticker: string;
  bars: ChartBar[];
  height?: number;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Series | null>(null);

  // The authoritative array, in a ref rather than state: the scroll handler
  // reads it on every frame and a stale closure over a state value would
  // page from the wrong cursor. `loadedFrom` mirrors it for the caption,
  // which is the only part React needs to re-render.
  const barsRef = useRef<ChartBar[]>(initial);
  const exhausted = useRef(false);
  const inFlight = useRef(false);
  /**
   * Whether the reader has touched the chart.
   *
   * **A negative `from` is not enough on its own.** With a short range the
   * viewport is wider than the data — 21 bars on a 1300px chart — so
   * `fitContent()` leaves blank space on the left and `from` is negative
   * at rest. That fired a page on mount and quietly turned `?range=1m`
   * into a year: measured, one `/api/chart?before=2026-07-22` call before
   * anyone scrolled.
   *
   * So paging needs both: blank space *and* a reader who went looking for
   * it. Anything else makes the range switch decorative, which is the same
   * defect the `from < 40` version had in a different disguise.
   */
  const interacted = useRef(false);

  const [loadedFrom, setLoadedFrom] = useState(initial[0]?.ts ?? null);
  // Mirrors `exhausted` for the caption. A ref does not re-render, so the
  // "start of history" note would not appear until something else did.
  const [atStart, setAtStart] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  /** When today's candle was last re-read. Null until the first poll. */
  const [liveAt, setLiveAt] = useState<Date | null>(null);

  /** Push the current array into every series. One function, so a series
   * added later cannot be forgotten by the paging path while the mount path
   * still fills it. */
  const paint = useCallback(() => {
    const s = seriesRef.current;
    if (!s) return;
    const bars = barsRef.current;

    const candles = (rows: ChartBar[]) =>
      rows
        .filter((b) => b.open !== null && b.high !== null && b.low !== null && b.close !== null)
        .map((b) => ({
          time: b.ts as Time,
          open: b.open as number,
          high: b.high as number,
          low: b.low as number,
          close: b.close as number,
        }));

    // Today's open session is drawn by its own series, so the closed one
    // must not also draw it -- two candles on one timestamp and the later
    // `setData` silently wins.
    s.price.setData(candles(bars.filter((b) => !b.partial)));
    s.partial.setData(candles(bars.filter((b) => b.partial)));
    const picks: ((b: ChartBar) => number | null)[] = [
      (b) => b.bbUpper,
      (b) => b.bbMid,
      (b) => b.bbLower,
    ];
    s.bands.forEach((series, i) => series.setData(line(bars, picks[i])));
    s.sma.setData(line(bars, (b) => b.sma200));
    s.volume.setData(
      bars
        .filter((b) => b.volume !== null)
        .map((b) => ({
          time: b.ts as Time,
          value: b.volume as number,
          color:
            b.close !== null && b.open !== null && b.close < b.open
              ? `${s.colors.short}55`
              : `${s.colors.long}55`,
        })),
    );
    s.kFast.setData(line(bars, (b) => b.kFast));
    s.kFull.setData(line(bars, (b) => b.kFull));
    s.markers.setMarkers(markers(bars, s.colors));
  }, []);

  /**
   * Fetch the page before what is loaded and prepend it (ADR 121).
   *
   * Three guards, each for a failure seen in this class of handler: a
   * re-entry guard because the scroll event fires per frame and would open
   * a request per frame; an exhausted flag because the start of a ticker's
   * history otherwise requests the same empty page forever; and a caught
   * error that leaves the chart alone, because a failed page is a chart
   * that stops growing and not a chart that breaks.
   */
  const loadOlder = useCallback(async () => {
    if (inFlight.current || exhausted.current) return;
    const oldest = barsRef.current[0]?.ts;
    if (!oldest) return;

    inFlight.current = true;
    setLoading(true);
    try {
      const url = `/api/chart?sym=${encodeURIComponent(ticker)}&before=${oldest}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`chart page ${response.status}`);
      const { bars } = (await response.json()) as { bars: ChartBar[] };

      if (bars.length === 0) {
        exhausted.current = true;
        setAtStart(true);
        return;
      }

      const merged = prepend(bars, barsRef.current);
      if (merged.length === barsRef.current.length) {
        // Every bar was already loaded. Asking again returns the same
        // page, so stop rather than loop.
        exhausted.current = true;
        setAtStart(true);
        return;
      }

      // The visible *logical* range is an index range, so prepending N bars
      // shifts what the reader is looking at by N. Captured before the
      // repaint and restored after, or the chart jumps backwards under the
      // cursor every time a page lands.
      const scale = chartRef.current?.timeScale();
      const before = scale?.getVisibleLogicalRange();
      const shift = merged.length - barsRef.current.length;

      barsRef.current = merged;
      paint();
      setLoadedFrom(merged[0]?.ts ?? null);

      if (scale && before) {
        scale.setVisibleLogicalRange({ from: before.from + shift, to: before.to + shift });
      }
      setFailed(null);
    } catch (error) {
      setFailed(error instanceof Error ? error.message : String(error));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [ticker, paint]);

  // The chart is built once per mount and never rebuilt. An effect keyed on
  // `bars` would tear down the canvas every time a page landed, which loses
  // the reader's scroll position and the pane heights they dragged.
  useEffect(() => {
    const el = holder.current;
    if (!el || barsRef.current.length === 0) return;

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
      timeScale: {
        borderColor: t.rule,
        // **The newest bar is the right edge.** `rightOffset: 0` puts no
        // blank space after it and `fixRightEdge` stops the scroll there,
        // so the chart cannot be dragged into empty future. A price chart
        // that pans past today shows a void that reads like missing data.
        rightOffset: 0,
        fixRightEdge: true,
        // Deliberately *not* `fixLeftEdge`. The left edge is where more
        // history arrives (ADR 121), so pinning it would be pinning the
        // thing that is supposed to move.
        fixLeftEdge: false,
      },
      crosshair: {
        vertLine: { color: t.muted, width: 1, style: 3, labelBackgroundColor: t.panel },
        horzLine: { color: t.muted, width: 1, style: 3, labelBackgroundColor: t.panel },
      },
    });
    chartRef.current = chart;

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

    // Hollow: transparent body, coloured border and wick. Its close is the
    // current price rather than a settled one, and it carries no bands or
    // stochastic because indicators are computed on closed bars only
    // (ADR 128).
    const partialSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: "transparent",
        downColor: "transparent",
        borderUpColor: t.long,
        borderDownColor: t.short,
        wickUpColor: t.long,
        wickDownColor: t.short,
        priceLineVisible: true,
        priceLineStyle: 2,
        lastValueVisible: true,
      },
      0,
    );

    const bandSeries = [false, true, false].map((dashed) =>
      chart.addSeries(
        LineSeries,
        {
          color: t.muted,
          lineWidth: 1,
          lineStyle: dashed ? 2 : 0,
          priceLineVisible: false,
          lastValueVisible: false,
          // The dot under the crosshair (user's request, 2026-08-19). Small:
          // four lines each carrying a marker is four things competing with
          // the candle the reader is actually on.
          crosshairMarkerVisible: true,
          crosshairMarkerRadius: 2,
        },
        0,
      ),
    );

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
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 2,
      },
      0,
    );

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

    // --- pane 2: stochastic ---------------------------------------------
    // Two lines, white and grey (user's request, 2026-08-19). `%D` is
    // gone: it is a moving average of the slow `%K` and nothing in the
    // system reads it — no signal, no exit, no cell dimension. A third
    // line that decides nothing is a third line to tell apart from the two
    // that do.
    const kFast = chart.addSeries(
      LineSeries,
      { color: t.text, lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
      2,
    );
    const kFull = chart.addSeries(
      LineSeries,
      { color: t.muted, lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
      2,
    );

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

    seriesRef.current = {
      price,
      bands: bandSeries,
      sma,
      volume,
      partial: partialSeries,
      kFast,
      kFull,
      markers: createSeriesMarkers(price, []),
      colors: { long: t.long, short: t.short },
    };
    paint();

    // Price gets the room. Volume is a texture read, not a value read, so
    // it takes the least.
    const panes = chart.panes();
    if (panes.length >= 3) {
      panes[0].setHeight(Math.round(height * 0.6));
      panes[1].setHeight(Math.round(height * 0.13));
      panes[2].setHeight(Math.round(height * 0.27));
    }
    chart.timeScale().fitContent();

    const onRange = (range: { from: number; to: number } | null) => {
      if (interacted.current && range && range.from < PREFETCH_BARS) void loadOlder();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);

    // Pointer or wheel on the chart itself. `capture` so a drag that
    // starts on the canvas still registers, and `once` because the flag
    // only ever goes one way.
    const onInteract = () => {
      interacted.current = true;
    };
    el.addEventListener("pointerdown", onInteract, { capture: true });
    el.addEventListener("wheel", onInteract, { capture: true, passive: true });

    // The crosshair readouts (user's request, 2026-08-19). One handler for
    // both, because they describe the same bar and must never disagree
    // about which one the cursor is on.
    const onCrosshair = (param: MouseEventParams<Time>) => {
      if (!param.time || !param.point || !param.seriesData.size) {
        setHover(null);
        return;
      }

      const candle = param.seriesData.get(price) as
        | { open: number; high: number; low: number; close: number }
        | undefined;
      const vol = param.seriesData.get(volume) as { value: number } | undefined;
      const f = param.seriesData.get(kFast) as { value: number } | undefined;
      const sl = param.seriesData.get(kFull) as { value: number } | undefined;

      const tops = paneTops(el);
      // Pane 2 is the stochastic one. Falling back to 0 rather than
      // bailing keeps the values visible if the markup ever changes; they
      // land at the top of the container instead of beside the dots, which
      // is wrong-looking rather than missing.
      const stochTop = tops[2] ?? 0;
      const at = (series: ISeriesApi<"Line">, value: number | undefined) => {
        if (value === undefined) return null;
        const y = series.priceToCoordinate(value);
        return y === null ? null : { value, y: stochTop + y };
      };

      // Pane 0 starts at the top of the container, so the price-pane
      // levels need no offset. Read through the same helper anyway rather
      // than assuming zero: a future layout change moves it.
      const priceTop = tops[0] ?? 0;
      const levels: { key: string; value: number; y: number }[] = [];
      const level = (key: string, series: ISeriesApi<"Line">) => {
        const point = param.seriesData.get(series) as { value: number } | undefined;
        if (point === undefined) return;
        const y = series.priceToCoordinate(point.value);
        if (y !== null) levels.push({ key, value: point.value, y: priceTop + y });
      };
      level("band", bandSeries[0]);
      level("band", bandSeries[1]);
      level("band", bandSeries[2]);
      level("sma", sma);

      setHover({
        x: param.point.x,
        date: String(param.time),
        ohlc: candle
          ? { open: candle.open, high: candle.high, low: candle.low, close: candle.close }
          : null,
        volume: vol?.value ?? null,
        fast: at(kFast, f?.value),
        slow: at(kFull, sl?.value),
        levels,
      });
    };
    chart.subscribeCrosshairMove(onCrosshair);

    return () => {
      el.removeEventListener("pointerdown", onInteract, { capture: true });
      el.removeEventListener("wheel", onInteract, { capture: true });
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      chart.unsubscribeCrosshairMove(onCrosshair);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height, paint, loadOlder]);

  // A new ticker or a new range is a new server render with new props. The
  // chart is not rebuilt for it — the effect above owns the canvas — so the
  // array and the paging flags are reset here instead.
  useEffect(() => {
    barsRef.current = initial;
    exhausted.current = false;
    // A new range or ticker is a fresh intent. Without this, having
    // scrolled the 5y chart would make a later `?range=5d` expand itself
    // on arrival.
    interacted.current = false;
    setAtStart(false);
    setLoadedFrom(initial[0]?.ts ?? null);
    setFailed(null);
    paint();
    chartRef.current?.timeScale().fitContent();
  }, [initial, paint]);

  /**
   * Re-read today's candle on a timer (ADR 128).
   *
   * The page is a server component, so the candle it renders is whatever
   * `bars_live` held at page load. The poller rewrites that row every five
   * minutes. Without this, a tab left open all session showed a price that
   * was right when it was drawn and progressively wronger after — the worst
   * kind of stale, because nothing on screen said so.
   *
   * `update()` on the two series that carry it rather than a full repaint:
   * `setData` on eight series to move one candle would rebuild the price
   * scale on every tick, and the reader is usually mid-drag somewhere else
   * on the chart.
   *
   * **Only while the tab is visible.** A backgrounded tab that keeps
   * polling is a request every 45 seconds forever against a database that
   * `cscan backtest` also wants (ADR 039). `visibilitychange` fires an
   * immediate refresh on return, so coming back to the tab is current
   * rather than up to 45 seconds behind.
   */
  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const response = await fetch(`/api/live?sym=${encodeURIComponent(ticker)}`);
        if (!response.ok) return;
        const { bar } = (await response.json()) as { bar: ChartBar | null };
        if (cancelled || !bar || bar.close === null) return;

        const bars = barsRef.current;
        const index = bars.findIndex((b) => b.ts === bar.ts);
        // A closed bar for the same session already exists — the nightly
        // ingest ran while the tab was open. It is settled and it carries
        // indicators, so it wins and the polling has nothing left to do.
        if (index !== -1 && !bars[index].partial) return;
        if (index === -1) bars.push(bar);
        else bars[index] = bar;

        const s = seriesRef.current;
        if (!s) return;
        s.partial.update({
          time: bar.ts as Time,
          open: bar.open as number,
          high: bar.high as number,
          low: bar.low as number,
          close: bar.close as number,
        });
        if (bar.volume !== null) {
          s.volume.update({
            time: bar.ts as Time,
            value: bar.volume,
            color:
              bar.open !== null && bar.close < bar.open
                ? `${s.colors.short}55`
                : `${s.colors.long}55`,
          });
        }
        setLiveAt(new Date());
      } catch {
        // A failed poll leaves the last good candle on the chart. The
        // timestamp beside it stops advancing, which is the honest signal:
        // this is what we last heard, and when.
      }
    };

    void refresh();
    const timer = window.setInterval(() => void refresh(), LIVE_POLL_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [ticker]);

  if (initial.length === 0) {
    return (
      <div className="chart-empty" style={{ height }}>
        <span className="rail" />
        <p>No bars in this range.</p>
      </div>
    );
  }

  return (
    <>
      <div className="chart-holder">
        <div className="chart" ref={holder} style={{ height }} />

        {/* The crosshair readouts. Rendered as HTML over the canvas rather
            than drawn into it: they are text, they select, and they inherit
            the same tokens as the rest of the page.

            `pointerEvents: none` on the wrapper, so nothing here can steal
            the drag the chart depends on. */}
        {hover && (
          <div className="chart-hover" aria-hidden="true">
            {hover.ohlc && (
              <div
                className="ohlcv"
                // Follows the cursor and flips to the left half past the
                // midpoint, so it never runs off the right edge on the most
                // recent bars — which is where a reader spends most of
                // their time.
                style={
                  hover.x > (holder.current?.clientWidth ?? 0) / 2
                    ? { right: (holder.current?.clientWidth ?? 0) - hover.x + 12 }
                    : { left: hover.x + 12 }
                }
              >
                <div className="d num">{hover.date}</div>
                {/* Spelled out (user's request, 2026-08-19). The single
                    letters were a habit from terminals with 80 columns;
                    there is room here, and "Open" needs no decoding. */}
                <dl>
                  <dt>Open</dt>
                  <dd className="num">{fmt(hover.ohlc.open)}</dd>
                  <dt>High</dt>
                  <dd className="num">{fmt(hover.ohlc.high)}</dd>
                  <dt>Low</dt>
                  <dd className="num">{fmt(hover.ohlc.low)}</dd>
                  <dt>Close</dt>
                  {/* Coloured against the open, the same rule the candle
                      body uses. */}
                  <dd className={`num ${hover.ohlc.close >= hover.ohlc.open ? "up" : "down"}`}>
                    {fmt(hover.ohlc.close)}
                  </dd>
                  <dt>Volume</dt>
                  <dd className="num">{vol(hover.volume)}</dd>
                </dl>
              </div>
            )}

            {/* Beside the dots, in the two colours the lines already use, so
                a value is matched to its line by colour rather than by
                reading a label. */}
            {hover.fast && (
              <span className="stoch-tag fast num" style={{ left: hover.x + 8, top: hover.fast.y }}>
                {hover.fast.value.toFixed(1)}
              </span>
            )}
            {hover.slow && (
              <span className="stoch-tag slow num" style={{ left: hover.x + 8, top: hover.slow.y }}>
                {hover.slow.value.toFixed(1)}
              </span>
            )}

            {/* The three bands and the 200-day, at the same bar. Same
                treatment as the stochastic values and for the same reason:
                the level a signal compared against is a number you want to
                read, not one you want to estimate off an axis. */}
            {hover.levels.map((l, i) => (
              <span
                key={`${l.key}-${i}`}
                className={`stoch-tag ${l.key} num`}
                style={{ left: hover.x + 8, top: l.y }}
              >
                {l.value.toFixed(2)}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* What is loaded, and why it stopped. A chart that quietly stops
          growing when you drag left is indistinguishable from one that has
          reached the beginning of the data. */}
      <p className="chart-foot dim">
        <span className="num">{loadedFrom ?? "—"}</span> to present
        {/* The open session's candle and when it was last re-read. A live
            number with no timestamp beside it cannot be told apart from a
            frozen one, which is exactly the failure this poll fixed. */}
        {liveAt && (
          <span className="live-at">
            {" "}
            · today live, updated{" "}
            <span className="num">{liveAt.toLocaleTimeString([], { hour12: false })}</span>
          </span>
        )}
        {loading && <span className="loading"> · loading earlier sessions</span>}
        {atStart && !loading && <span> · start of history</span>}
        {failed && (
          <span className="failed" role="alert">
            {" "}
            · could not load earlier sessions ({failed})
          </span>
        )}
      </p>
    </>
  );
}
