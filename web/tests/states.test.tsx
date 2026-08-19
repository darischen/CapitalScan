import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

/**
 * `TickerHeader` renders the search box, which calls `useRouter`. Outside a
 * Next request there is no router mounted and the hook throws, so it is
 * stubbed — this file tests the server-rendered markup, and navigation is
 * the one thing on this page that is not part of it.
 */
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {} }),
}));

import { EmptyState, ScreenerTable, StatusStrip } from "@/components/Screener";
import {
  BandGauge,
  ChartLegend,
  EventHistory,
  NoTicker,
  StateRail,
  TickerHeader,
} from "@/components/Ticker";
import { STALE_AFTER_DAYS, type Meta, type ScreenRow } from "@/lib/screen";
import type { TickerEvent, TickerState } from "@/lib/ticker";

/**
 * The five states, rendered and asserted.
 *
 * These are the paths that only run when something is wrong — an empty day,
 * a stale database, a suppressed cell — which makes them the ones most
 * likely to be broken without anyone noticing. Every one of them was
 * written and none was verified until this file existed: the live data has
 * been populated and fresh every time it was looked at.
 *
 * `renderToStaticMarkup` rather than a DOM library. These are server
 * components with no state and no effects, so the markup *is* the
 * behaviour, and a string comparison is what gate item 10 asks for.
 */

function meta(over: Partial<Meta> = {}): Meta {
  return {
    configHash: "86e91448a65aa40b",
    asOf: "2026-08-18",
    stalenessDays: 0,
    stale: false,
    readOnly: true,
    ...over,
  };
}

function state(over: Partial<TickerState> = {}): TickerState {
  return {
    ticker: "TSM",
    name: "Taiwan Semiconductor",
    sector: "Technology",
    asOf: "2026-08-18",
    close: 413.41,
    volume: 14_000_000,
    bbLower: 385.63,
    bbMid: 413.01,
    bbUpper: 440.4,
    bbPctB: 0.507,
    bbWidthPct: 0.437,
    kFull: 76.7,
    dFull: 85.2,
    kFast: 53.3,
    kCrossUp: false,
    kCrossDown: false,
    sma200: 364.6,
    atr14: 13.5,
    rv20d: 0.393,
    ddPct: -0.134,
    daysToEarnings: null,
    vixClose: 15.8,
    inTrade: true,
    aboveSma200: true,
    mcapUsd: 2.1e12,
    ...over,
  };
}

function event(over: Partial<TickerEvent> = {}): TickerEvent {
  return {
    id: 1,
    signalDate: "2026-07-27",
    signalType: "bb_lower_touch",
    signalTypesAll: ["bb_lower_touch"],
    signalStrength: 1,
    side: "long",
    isClusterHead: true,
    ddBucket: "10-20",
    entryDate: "2026-07-28",
    entryPrice: 390.93,
    exitDate: "2026-07-30",
    exitPrice: 406.57,
    exitReason: "target",
    holdingDays: 3,
    netRet: 0.0394,
    mfe: 0.041,
    mae: -0.047,
    earningsInWindow: false,
    ...over,
  };
}

function row(over: Partial<ScreenRow> = {}): ScreenRow {
  return {
    ticker: "TSM",
    signalDate: "2026-08-18",
    signalType: "confluence_high",
    signalTypesAll: ["confluence_high", "bb_upper_touch", "stoch_overbought"],
    signalStrength: 3,
    side: "short",
    bbLower: 385.63,
    bbMid: 413.01,
    bbUpper: 440.4,
    bandTs: "2026-08-17",
    kFast: 92.1,
    kSlow: 90.4,
    open: 410,
    high: 442,
    low: 409,
    close: 441,
    volume: 14_000_000,
    livePrice: 441.2,
    livePriceTs: "2026-08-18T13:35:00.000Z",
    firedAt: "2026-08-18T13:35:12.000Z",
    cellId: "cell-1",
    isClusterHead: null,
    reversal: null,
    stats: null,
    ...over,
  } as ScreenRow;
}

/* --- gate 7: staleness ------------------------------------------------- */

describe("gate 7: the staleness banner triggers above 2 days", () => {
  const strip = (days: number) => {
    const m = meta({ stalenessDays: days, stale: days > STALE_AFTER_DAYS });
    return renderToStaticMarkup(<StatusStrip meta={m} fired={4} signalDate="2026-08-18" />);
  };

  it("does not fire at 1 day", () => {
    expect(strip(1)).not.toContain("stale ·");
    expect(strip(1)).toContain("fresh");
  });

  /** The boundary itself. `MonitoringThresholds.stale_after_days` is 2 and
   * the rule is *above* 2, so exactly 2 is fresh. Off by one here means the
   * banner fires every third day forever, which reads as noise and gets
   * ignored on the day it is real. */
  it("does not fire at exactly 2 days", () => {
    expect(strip(STALE_AFTER_DAYS)).toContain("fresh");
    expect(strip(STALE_AFTER_DAYS)).not.toContain("stale ·");
  });

  it("fires at 3 days and names the count", () => {
    const html = strip(3);
    expect(html).toContain("stale ·");
    expect(html).toContain("3 sessions since last bar");
    expect(html).toContain("strip stale");
  });

  it("links to the neighbouring dates that have rows", () => {
    const html = renderToStaticMarkup(
      <StatusStrip
        meta={meta()}
        fired={4}
        signalDate="2026-08-18"
        prevDate="2026-08-13"
        nextDate={null}
      />,
    );
    expect(html).toContain("date=2026-08-13");
    // No next date means the newest screen, so no "latest" shortcut to the
    // page you are already reading.
    expect(html).not.toContain(">latest<");
  });

  /**
   * The arrows must carry the filters. A link that dropped `?all=1` would
   * change the filter as well as the date, and the reader would read the
   * difference in row count as a change in the market.
   */
  it("keeps the toggles when stepping to another date", () => {
    const html = renderToStaticMarkup(
      <StatusStrip
        meta={meta()}
        fired={4}
        signalDate="2026-08-18"
        prevDate="2026-08-13"
        nextDate="2026-08-19"
        withStats
        confluenceOnly={false}
      />,
    );
    expect(html).toContain("stats=1");
    expect(html).toContain("all=1");
    // And "latest" is the absence of a date, never an empty one.
    expect(html).not.toContain("date=&");
    expect(html).not.toMatch(/date=(?=["&])/);
  });

  it("renders a disabled arrow at the end of the history rather than removing it", () => {
    const html = renderToStaticMarkup(
      <StatusStrip meta={meta()} fired={0} signalDate="2010-01-05" prevDate={null} nextDate="2010-01-06" />,
    );
    expect(html).toContain('class="off"');
  });

  it("reports the read-write role when the read-only one is unset", () => {
    const html = renderToStaticMarkup(
      <StatusStrip meta={meta({ readOnly: false })} fired={0} signalDate={null} />,
    );
    expect(html).toContain("read-write role");
  });
});

/* --- gate 6: empty state ----------------------------------------------- */

describe("gate 6: the empty state carries a last-fire reference", () => {
  it("names the ticker, the date, and links to it", () => {
    const html = renderToStaticMarkup(
      <EmptyState last={{ ticker: "TSM", signalDate: "2026-08-13" }} />,
    );
    expect(html).toContain("No signals today.");
    expect(html).toContain("TSM");
    expect(html).toContain("2026-08-13");
    expect(html).toContain('href="/ticker/TSM"');
  });

  /** A database with no events at all. Distinguished from "nothing today",
   * because the two mean different things and a blank line under "No
   * signals today" reads as a failed query. */
  it("says so when there is no fire to point at", () => {
    const html = renderToStaticMarkup(<EmptyState last={null} />);
    expect(html).toContain("No events recorded for this config.");
  });
});

/* --- reversals (ADR 117, ADR 123) --------------------------------------- */

describe("the two reversals are told apart", () => {
  const render = (over: Partial<ScreenRow>) =>
    renderToStaticMarkup(<ScreenerTable rows={[row(over)]} withStats={false} />);

  it("shows the close-confirmed reversal as a solid badge", () => {
    const html = render({
      signalTypesAll: ["bear_close_above_upper", "confluence_high", "bb_upper_touch"],
    });
    expect(html).toContain("↓ reversal");
    expect(html).not.toContain("live reversal");
  });

  it("shows the poller's live reversal, marked as live", () => {
    const html = render({
      reversal: {
        confirmed: true,
        aboveBand: true,
        openGapAtr: -0.31,
        ts: "2026-08-19T14:05:00.000Z",
      },
    });
    expect(html).toContain("↓ live reversal");
    expect(html).toContain("reversal live");
  });

  /**
   * Close-confirmed wins. It is a settled fact about a finished session;
   * the poller's is a statement about a moment, and once the close has
   * spoken the moment no longer matters.
   */
  it("prefers the close-confirmed one when both exist", () => {
    const html = render({
      signalTypesAll: ["bear_close_above_upper", "confluence_high"],
      reversal: {
        confirmed: true,
        aboveBand: true,
        openGapAtr: -0.31,
        ts: "2026-08-19T14:05:00.000Z",
      },
    });
    expect(html).toContain("↓ reversal");
    expect(html).not.toContain("live reversal");
  });

  /**
   * The near-miss is the reason ADR 117 chose option B: show every
   * confluence and say how far each is from confirming. A badge that
   * appeared only on confirmation would put that decision back.
   */
  it("renders a near miss with its distance rather than nothing", () => {
    const html = render({
      reversal: {
        confirmed: false,
        aboveBand: true,
        openGapAtr: 0.42,
        ts: "2026-08-19T14:05:00.000Z",
      },
    });
    expect(html).toContain("0.42 ATR vs open");
    expect(html).toContain("reversal near");
  });

  it("shows nothing when the poller has not evaluated the row", () => {
    const html = render({ reversal: null });
    expect(html).not.toContain("reversal");
  });
});

/* --- gate 4/5: suppressed and companions -------------------------------- */

describe("gates 4 and 5: statistics render whole or not at all", () => {
  it("a suppressed cell renders its stored reason and no number", () => {
    const html = renderToStaticMarkup(
      <ScreenerTable
        rows={[
          row({
            stats: { kind: "suppressed", cellId: "c", reason: "n_eff 12 < min_n_eff 30", nEff: 12 },
          }),
        ]}
        withStats
      />,
    );
    expect(html).toContain("n_eff 12 &lt; min_n_eff 30");
    expect(html).not.toMatch(/\d+\.\d%/);
  });

  /** Invariant 8. A hit rate without its interval is the number this system
   * exists not to produce. */
  it("a hit rate renders with n_eff, the interval, and the q-value", () => {
    const html = renderToStaticMarkup(
      <ScreenerTable
        rows={[
          row({
            stats: {
              kind: "cell",
              cellId: "c",
              pHit: 0.51,
              baseline: 0.39,
              edge: 0.12,
              ciLow: 0.46,
              ciHigh: 0.56,
              qValue: 0.849,
              nEff: 340,
              survivesFdr: false,
            },
          }),
        ]}
        withStats
      />,
    );
    expect(html).toContain("51%");
    expect(html).toContain("340");
    expect(html).toContain("46");
    expect(html).toContain("56");
    // Full precision. Session 17's plan lists "rounding a q-value of 0.849
    // to 'not significant' and hiding it" as a thing not to do — the number
    // is more informative than the label.
    expect(html).toContain("q=0.849");
    expect(html).toContain("did not survive FDR correction");
  });
});

/* --- ticker page states ------------------------------------------------- */

describe("the ticker page's states", () => {
  it("renders the not-found state without claiming the symbol is invalid", () => {
    const html = renderToStaticMarkup(<NoTicker sym="ABMD" />);
    expect(html).toContain("No indicator history for");
    expect(html).toContain("ABMD");
    // It cannot tell an unknown symbol from a known one with no indicators,
    // so it must not assert either.
    expect(html).not.toMatch(/not a (valid|real) ticker|does not exist/i);
  });

  it("renders a ticker with no events without an empty table", () => {
    const html = renderToStaticMarkup(<EventHistory sym="TSM" events={[]} all={false} total={0} />);
    expect(html).toContain("No events for TSM");
    expect(html).not.toContain("<tbody>");
  });

  /**
   * `run_events` only writes an event for a bar where the ticker was in the
   * *trade* universe that day (DESIGN §4.7 step 3). SMCI has 192 band
   * touches since 2024, zero events, and has never been in the trade
   * universe across 66 quarterly snapshots — so the page must say why
   * rather than let an absence read as a broken job.
   */
  it("explains an empty history on a ticker outside the trade universe", () => {
    const html = renderToStaticMarkup(
      <EventHistory sym="SMCI" events={[]} all={false} total={0} inTrade={false} />,
    );
    expect(html).toContain("outside the trade universe");
    expect(html).toContain("will not add them");
    // Not the other explanation. "Nothing crossed a band" would be false:
    // plenty did.
    expect(html).not.toContain("nothing in it crossed a band");
  });

  /** A history that stops years before the newest bar has the same cause as
   * an empty one and reads far more like a bug, so it gets the note too. */
  it("explains a history that stops, naming the date it stopped", () => {
    const html = renderToStaticMarkup(
      <EventHistory
        sym="SMCI"
        events={[event({ signalDate: "2010-03-24" })]}
        all={false}
        total={1}
        inTrade={false}
      />,
    );
    expect(html).toContain("Detection stopped at");
    expect(html).toContain("2010-03-24");
    expect(html).toContain("2010-03-31");
  });

  it("says nothing about the universe for a ticker that is in it", () => {
    const html = renderToStaticMarkup(
      <EventHistory sym="TSM" events={[event()]} all={false} total={1} inTrade={true} />,
    );
    expect(html).not.toContain("Detection stopped at");
  });

  it("tells an open event apart from one that closed flat", () => {
    const open = renderToStaticMarkup(
      <EventHistory
        sym="TSM"
        events={[event({ netRet: null, exitDate: null, exitPrice: null, exitReason: null })]}
        all={false}
        total={1}
      />,
    );
    const closed = renderToStaticMarkup(
      <EventHistory sym="TSM" events={[event({ netRet: 0 })]} all={false} total={1} />,
    );
    expect(open).toContain(">open<");
    expect(closed).toContain("0.0%");
    expect(closed).not.toContain(">open<");
  });

  it("marks a row the poller wrote before clustering ran", () => {
    const html = renderToStaticMarkup(
      <EventHistory sym="TSM" events={[event({ isClusterHead: null })]} all={false} total={1} />,
    );
    expect(html).toContain("pending cluster");
  });

  it("says the outcomes are measured on a different entry from the screener's", () => {
    const html = renderToStaticMarkup(<EventHistory sym="TSM" events={[event()]} all={false} total={1} />);
    expect(html).toContain("touch");
    expect(html).toContain("next_open");
  });

  it("labels the headline price as a close, and shows the session's OHLC", () => {
    const bar = {
      ts: "2026-08-18",
      open: 410,
      high: 416.2,
      low: 409.1,
      close: 413.41,
      volume: 14_000_000,
      bbLower: null,
      bbMid: null,
      bbUpper: null,
      kFull: null,
      dFull: null,
      kFast: null,
      sma200: null,
      eventIds: [],
      signalTypes: [],
      sides: [],
      signalStrength: null,
    };
    const html = renderToStaticMarkup(
      <TickerHeader state={state()} meta={meta()} fired={3} latest={bar} />,
    );
    expect(html).toContain(">close<");
    expect(html).toContain("410.00");
    expect(html).toContain("416.20");
    expect(html).toContain("409.10");
  });

  /** The chart can be empty — a symbol with state but no bars in range —
   * and the header must render without it rather than print four dashes. */
  it("omits the OHLC when there is no bar", () => {
    const html = renderToStaticMarkup(
      <TickerHeader state={state()} meta={meta()} fired={3} latest={null} />,
    );
    expect(html).not.toContain("tag ohlc");
    expect(html).toContain(">close<");
  });

  /** The screener already showed a live price and the ticker page did not,
   * so during a session the same ticker read as two different numbers on
   * two pages. */
  it("leads with the poller's price when there is one, and still shows the close", () => {
    const html = renderToStaticMarkup(
      <TickerHeader
        state={state()}
        meta={meta()}
        fired={3}
        live={{ price: 415.22, ts: "2026-08-18T17:35:00.000Z", aheadOfBar: false }}
      />,
    );
    expect(html).toContain(">live<");
    expect(html).toContain("415.22");
    // The close never disappears: every band, indicator and signal on the
    // page was computed from it and not from the quote.
    expect(html).toContain(">close<");
    expect(html).toContain("413.41");
  });

  it("shows only the close when the poller has nothing for this session", () => {
    const html = renderToStaticMarkup(
      <TickerHeader state={state()} meta={meta()} fired={3} live={null} />,
    );
    expect(html).not.toContain(">live<");
    expect(html).toContain("num big");
  });

  it("says when a ticker is outside the trade universe", () => {
    const inside = renderToStaticMarkup(
      <TickerHeader state={state({ inTrade: true })} meta={meta()} fired={3} />,
    );
    const outside = renderToStaticMarkup(
      <TickerHeader state={state({ inTrade: false })} meta={meta()} fired={3} />,
    );
    expect(inside).toContain("in trade universe");
    expect(outside).toContain("outside trade universe");
  });

  /** 210 of 712 tickers carry no name. An em-dash where a company name goes
   * reads as a value that failed to load. */
  it("omits the company name rather than printing a placeholder", () => {
    const html = renderToStaticMarkup(
      <TickerHeader state={state({ name: null })} meta={meta()} fired={0} />,
    );
    expect(html).not.toContain("tk-name");
  });

  it("carries the staleness banner into the ticker header", () => {
    const html = renderToStaticMarkup(
      <TickerHeader
        state={state()}
        meta={meta({ stale: true, stalenessDays: 4 })}
        fired={0}
      />,
    );
    expect(html).toContain("4 sessions behind");
  });
});

/* --- the band gauge ----------------------------------------------------- */

describe("the band gauge", () => {
  it("places the tick at the percentage %B names", () => {
    const html = renderToStaticMarkup(<BandGauge state={state({ bbPctB: 0.25 })} />);
    expect(html).toContain("left:25%");
    expect(html).not.toContain("outside");
  });

  /** Price outside the band is the normal case for a fired signal — %B above
   * 1 is what `bb_upper_touch` means — so the tick pins to the edge and the
   * printed number stays exact. */
  it("pins the tick at the edge but prints the true value", () => {
    const html = renderToStaticMarkup(<BandGauge state={state({ bbPctB: 1.37 })} />);
    expect(html).toContain("left:100%");
    expect(html).toContain("outside");
    expect(html).toContain("1.370");
  });

  it("pins the low side too", () => {
    const html = renderToStaticMarkup(<BandGauge state={state({ bbPctB: -0.2 })} />);
    expect(html).toContain("left:0%");
    expect(html).toContain("outside");
  });

  it("draws no tick at all when %B is unknown", () => {
    const html = renderToStaticMarkup(<BandGauge state={state({ bbPctB: null })} />);
    expect(html).not.toContain("gauge-tick");
    expect(html).toContain("—");
  });
});

/* --- gate 8: both %K series --------------------------------------------- */

describe("gate 8: both %K series are named and distinguishable", () => {
  it("the legend names both stochastic lines", () => {
    const html = renderToStaticMarkup(<ChartLegend />);
    expect(html).toContain("fast");
    expect(html).toContain("slow");
    expect(html).toContain("Stochastic");
    // Different classes means different colours, which is what
    // "distinguishable" has to mean for two lines that sit within
    // `fast_agreement_tol` of each other by construction.
    expect(html).toContain("ln kfast");
    expect(html).toContain("ln kfull");
    // %D is deliberately gone: nothing in the system reads it.
    expect(html).not.toContain("%D");
    expect(html).not.toContain("dfull");
  });

  /** ADR 110 gates the signal on the two agreeing within
   * `fast_agreement_tol`, so the gap is the quantity that decides whether a
   * threshold crossing is a signal at all. */
  it("the state rail prints both and the gap between them", () => {
    const html = renderToStaticMarkup(<StateRail state={state({ kFast: 53.3, kFull: 76.7 })} />);
    expect(html).toContain("53.3");
    expect(html).toContain("76.7");
    expect(html).toContain("23.4");
  });

  it("shows market cap at the end of the rail", () => {
    const html = renderToStaticMarkup(<StateRail state={state({ mcapUsd: 2.476e12 })} />);
    expect(html).toContain("market cap");
    expect(html).toContain("2.48T");
  });

  /** A name with no `universe` snapshot has no cap. The placeholder is the
   * same em-dash every other missing value uses, never a zero. */
  it("renders the placeholder when there is no market cap", () => {
    const html = renderToStaticMarkup(<StateRail state={state({ mcapUsd: null })} />);
    // Scoped to the cell. A looser `not.toContain("0M")` fails on the
    // volume beside it, which legitimately renders `14.0M`.
    const cell = html.slice(html.indexOf("market cap"));
    expect(cell.slice(0, 80)).toContain("—");
    expect(cell.slice(0, 80)).not.toMatch(/\d/);
  });

  it("prints no gap when one of them is missing, rather than a wrong one", () => {
    const html = renderToStaticMarkup(<StateRail state={state({ kFull: null })} />);
    expect(html).not.toContain("Δ");
  });
});

/* --- gate 10: determinism ----------------------------------------------- */

describe("gate 10: identical input renders identically", () => {
  const cases: [string, () => string][] = [
    ["StatusStrip", () => renderToStaticMarkup(<StatusStrip meta={meta()} fired={4} signalDate="2026-08-18" />)],
    ["ScreenerTable", () => renderToStaticMarkup(<ScreenerTable rows={[row()]} withStats={false} />)],
    ["StateRail", () => renderToStaticMarkup(<StateRail state={state()} />)],
    ["BandGauge", () => renderToStaticMarkup(<BandGauge state={state()} />)],
    ["EventHistory", () => renderToStaticMarkup(<EventHistory sym="TSM" events={[event()]} all={false} total={1} />)],
    ["TickerHeader", () => renderToStaticMarkup(<TickerHeader state={state()} meta={meta()} fired={1} />)],
  ];

  it.each(cases)("%s is a pure function of its props", (_name, render) => {
    expect(render()).toBe(render());
  });

  /**
   * The sharper version. A component reading `Date.now()` or `Math.random()`
   * renders identically twice in the same millisecond and differs across a
   * day boundary, which is the failure that shows up in a screenshot
   * comparison months later and never in a test.
   */
  it.each(cases)("%s renders nothing that depends on the current time", (_name, render) => {
    const html = render();
    const today = new Date().toISOString().slice(0, 10);
    expect(html).not.toContain(today);
  });
});
