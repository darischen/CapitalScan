import { describe, expect, it } from "vitest";

import { markers, prepend } from "@/components/TickerChart";
import { fmt, normalizeSymbol, pct, signedPct, vol } from "@/lib/format";
import { sideFor } from "@/lib/screen";
import {
  DEFAULT_EVENT_LIMIT,
  DEFAULT_RANGE,
  MAX_EVENT_LIMIT,
  MAX_PAGE_SESSIONS,
  PAGE_SESSIONS,
  RANGES,
  clampEvents,
  clampPage,
  parseRange,
  toLiveBar,
  toLiveQuote,
  type ChartBar,
  type LiveRow,
} from "@/lib/ticker";

/** A bar with nothing on it, so each test names only what it cares about. */
function bar(over: Partial<ChartBar> = {}): ChartBar {
  return {
    ts: "2026-08-18",
    open: 100,
    high: 101,
    low: 99,
    close: 100.5,
    volume: 1_000_000,
    bbLower: 95,
    bbMid: 100,
    bbUpper: 105,
    kFull: 50,
    dFull: 50,
    kFast: 50,
    sma200: 90,
    eventIds: [],
    signalTypes: [],
    sides: [],
    signalStrength: null,
    ...over,
  };
}

describe("range", () => {
  it("takes the default for anything it does not recognise", () => {
    expect(parseRange(undefined)).toBe(DEFAULT_RANGE);
    expect(parseRange("")).toBe(DEFAULT_RANGE);
    expect(parseRange("10y")).toBe(DEFAULT_RANGE);
    expect(parseRange("__proto__")).toBe(DEFAULT_RANGE);
  });

  it("passes a known range through", () => {
    for (const key of Object.keys(RANGES)) {
      expect(parseRange(key)).toBe(key);
    }
  });

  it("counts sessions, not calendar days", () => {
    // 252 sessions is a year; 365 would be a year and a quarter of bars.
    expect(RANGES["1y"]).toBe(252);
    expect(RANGES["5y"]).toBe(RANGES["1y"] * 5);
  });
});

describe("event limit (ADR 074)", () => {
  it("caps regardless of what is passed", () => {
    expect(clampEvents(10_000)).toBe(MAX_EVENT_LIMIT);
  });

  it("takes the default when absent or nonsense", () => {
    expect(clampEvents(undefined)).toBe(DEFAULT_EVENT_LIMIT);
    expect(clampEvents(Number.NaN)).toBe(DEFAULT_EVENT_LIMIT);
  });

  it("never returns zero, which would render an empty history for a ticker that has one", () => {
    expect(clampEvents(0)).toBe(1);
    expect(clampEvents(-5)).toBe(1);
  });
});

describe("gate 8: chart markers", () => {
  it("puts a long below the bar and a short above it", () => {
    const [long] = markers([bar({ signalTypes: ["bb_lower_touch"], sides: ["long"] })]);
    const [short] = markers([bar({ signalTypes: ["stoch_overbought"], sides: ["short"] })]);

    expect(long.position).toBe("belowBar");
    expect(long.shape).toBe("arrowUp");
    expect(short.position).toBe("aboveBar");
    expect(short.shape).toBe("arrowDown");
  });

  /**
   * ADR 120's reason for making the marker columns arrays. 116 dates carry
   * both a long and a short head, and before the fix a chart keyed on time
   * would have kept one of them.
   */
  it("emits both markers when a bar fired long and short", () => {
    const both = markers([
      bar({
        signalTypes: ["bb_lower_touch", "stoch_overbought"],
        sides: ["long", "short"],
      }),
    ]);

    expect(both).toHaveLength(2);
    expect(both.map((m) => m.position)).toEqual(["belowBar", "aboveBar"]);
    expect(new Set(both.map((m) => m.time))).toEqual(new Set(["2026-08-18"]));
  });

  it("emits nothing for a bar that did not fire", () => {
    expect(markers([bar()])).toEqual([]);
  });

  it("labels confluence and nothing else", () => {
    const [c] = markers([bar({ signalTypes: ["confluence_high"], sides: ["short"] })]);
    const [plain] = markers([bar({ signalTypes: ["bb_upper_touch"], sides: ["short"] })]);
    expect(c.text).toBe("C");
    expect(plain.text).toBe("");
  });

  it("takes its colours from the caller, so the palette has one source", () => {
    const [m] = markers([bar({ signalTypes: ["bb_lower_touch"], sides: ["long"] })], {
      long: "#111111",
      short: "#222222",
    });
    expect(m.color).toBe("#111111");
  });

  /**
   * A defensive default rather than a real state: `v_chart` aggregates both
   * arrays in the same `array_agg ... ORDER BY e.id`, so they are the same
   * length by construction. If that ever stopped being true, a marker
   * pointing the wrong way is worse than one pointing a default way, and
   * this pins which it is.
   */
  it("falls back to long when a side is missing", () => {
    const [m] = markers([bar({ signalTypes: ["bb_lower_touch"], sides: [] })]);
    expect(m.position).toBe("belowBar");
  });

  it("agrees with sideFor on every type it can be handed", () => {
    for (const type of Object.keys({
      bb_lower_touch: 0,
      bb_upper_touch: 0,
      stoch_oversold: 0,
      stoch_overbought: 0,
      confluence_low: 0,
      confluence_high: 0,
      bear_close_above_upper: 0,
    })) {
      const side = sideFor(type);
      const [m] = markers([bar({ signalTypes: [type], sides: [side] })]);
      expect(m.position).toBe(side === "long" ? "belowBar" : "aboveBar");
    }
  });
});

describe("formatting", () => {
  it("renders a missing number as the placeholder, never as blank or zero", () => {
    expect(fmt(null)).toBe("—");
    expect(pct(null)).toBe("—");
    expect(signedPct(null)).toBe("—");
    expect(vol(null)).toBe("—");
  });

  it("keeps a real zero", () => {
    expect(fmt(0)).toBe("0.00");
    expect(signedPct(0)).toBe("0.0%");
  });

  it("signs a return so direction survives a screenshot", () => {
    expect(signedPct(0.039)).toBe("+3.9%");
    expect(signedPct(-0.052)).toBe("-5.2%");
  });

  it("abbreviates volume at the boundaries", () => {
    expect(vol(999)).toBe("999");
    expect(vol(1_000)).toBe("1K");
    expect(vol(1_400_000)).toBe("1.4M");
    expect(vol(2_000_000_000)).toBe("2.00B");
  });
});

describe("symbol normalisation", () => {
  it("upper-cases so /ticker/tsm and /ticker/TSM are one page", () => {
    expect(normalizeSymbol("tsm")).toBe("TSM");
  });

  it("keeps the punctuation a US symbol can hold", () => {
    expect(normalizeSymbol("brk.b")).toBe("BRK.B");
    expect(normalizeSymbol("bf-b")).toBe("BF-B");
  });

  /**
   * Not SQL escaping — every query here is parameterised and the boundary
   * test asserts it. This is so a pasted URL with a stray character
   * resolves to the ticker rather than to the not-found state.
   */
  it("drops everything else and bounds the length", () => {
    expect(normalizeSymbol("TSM'; DROP TABLE bars--")).toBe("TSMDROPTABLE");
    expect(normalizeSymbol("A".repeat(50))).toHaveLength(12);
  });
});

describe("history paging (ADR 121)", () => {
  it("caps a page and takes the default for nonsense", () => {
    expect(clampPage(undefined)).toBe(PAGE_SESSIONS);
    expect(clampPage(Number.NaN)).toBe(PAGE_SESSIONS);
    expect(clampPage(10_000)).toBe(MAX_PAGE_SESSIONS);
    expect(clampPage(0)).toBe(1);
  });

  it("puts an older page in front, oldest first", () => {
    const older = [bar({ ts: "2026-08-14" }), bar({ ts: "2026-08-17" })];
    const loaded = [bar({ ts: "2026-08-18" })];
    expect(prepend(older, loaded).map((b) => b.ts)).toEqual([
      "2026-08-14",
      "2026-08-17",
      "2026-08-18",
    ]);
  });

  /**
   * The query is strict, so an overlap should not happen. `setData` throws
   * on a duplicate timestamp, which would blank the chart on a scroll
   * gesture rather than skip one bar, so it is guarded anyway.
   */
  it("drops a bar it already holds rather than duplicating a timestamp", () => {
    const loaded = [bar({ ts: "2026-08-17" }), bar({ ts: "2026-08-18" })];
    const older = [bar({ ts: "2026-08-14" }), bar({ ts: "2026-08-17" })];
    const merged = prepend(older, loaded);
    expect(merged.map((b) => b.ts)).toEqual(["2026-08-14", "2026-08-17", "2026-08-18"]);
    expect(new Set(merged.map((b) => b.ts)).size).toBe(merged.length);
  });

  /**
   * How the caller learns to stop asking. An identity return means nothing
   * new arrived, and the component flips its exhausted flag on it — without
   * that check, the start of a ticker's history requests the same page on
   * every frame forever.
   */
  it("returns the same array when the page adds nothing", () => {
    const loaded = [bar({ ts: "2026-08-18" })];
    expect(prepend([], loaded)).toBe(loaded);
    expect(prepend([bar({ ts: "2026-08-18" })], loaded)).toBe(loaded);
  });
});

/**
 * The header's live price and the chart's candle come from one row.
 *
 * **They came from two tables until 2026-08-19.** `poll.py` writes
 * `quotes_live` inside its breach loop, so a ticker gets a row only on a
 * tick where it fired; ROST fired at 09:36 ET and never again, and hours
 * after the close the header still read 238.72 from that morning while the
 * candle beside it showed the true 234.63. Two numbers describing the same
 * quantity, on one screen, disagreeing by four dollars.
 *
 * `bars_live.close` is the current price for every polled ticker. These
 * assert the two shapes are built from the same row, which is what makes
 * the disagreement unrepresentable rather than merely unlikely.
 */
describe("the live row is one source for two shapes", () => {
  const row = (over: Partial<LiveRow> = {}): LiveRow => ({
    // **Local midnight, not `...T00:00:00Z`.** `pg` parses a `date` column
    // into a `Date` at local midnight and `isoDate` reads it back with
    // local getters, so a UTC-midnight fixture is the *previous* day on
    // this machine and every date assertion below silently shifts. The
    // same one-session offset ADR 120's chart markers hit.
    session_date: new Date(2026, 7, 19),
    ts: new Date("2026-08-19T19:55:46Z"),
    market_is_open: true,
    open: "233.5900",
    high: "239.6700",
    low: "232.5600",
    close: "234.6300",
    volume: "2070108",
    ...over,
  });

  it("the quote's price is the candle's close", () => {
    const source = row();
    expect(toLiveQuote(source, "2026-08-18")?.price).toBe(toLiveBar(source)?.close);
  });

  it("reads the close, not any other price on the row", () => {
    // The regression in one line: 238.715 was a *different* number from a
    // different table, and no arrangement of this row can produce it.
    const quote = toLiveQuote(row(), "2026-08-18");
    expect(quote?.price).toBe(234.63);
    expect(quote?.ts).toBe("2026-08-19T19:55:46.000Z");
  });

  it("is ahead of the bar during a session and level with it after ingest", () => {
    expect(toLiveQuote(row(), "2026-08-18")?.aheadOfBar).toBe(true);
    // Once the nightly writes today's closed bar, the live price is no
    // longer ahead of anything.
    expect(toLiveQuote(row(), "2026-08-19")?.aheadOfBar).toBe(false);
  });

  it("both are null when the poller does not cover the ticker", () => {
    expect(toLiveBar(null)).toBeNull();
    expect(toLiveQuote(null, "2026-08-18")).toBeNull();
  });

  /**
   * The reported defect: TSLA showed 349.58 as "live" at 15:25 PT against
   * an official close of 351.12. The poller's last tick landed at 15:55:46
   * ET, five minutes before the bell, and the stock moved $1.54 after it.
   *
   * The row is still real — it is the session's OHLCV and the candle keeps
   * drawing it. What must not survive the close is the *price*, because
   * "live" is a claim about now.
   */
  it("the price goes away after the close, and the candle does not", () => {
    const closed = row({ market_is_open: false });
    expect(toLiveQuote(closed, "2026-08-18")).toBeNull();

    const bar = toLiveBar(closed);
    expect(bar?.close).toBe(234.63);
    expect(bar?.marketOpen).toBe(false);
  });

  it("the candle carries the session flag so the client can stop polling", () => {
    expect(toLiveBar(row())?.marketOpen).toBe(true);
  });

  /** The candle stays partial: it has no indicators and must never grow
   * any, or invariant 3 is broken one layer up. */
  it("the candle carries prices and no indicators", () => {
    const bar = toLiveBar(row())!;
    expect(bar.partial).toBe(true);
    for (const field of ["bbLower", "bbMid", "bbUpper", "kFast", "kFull", "sma200"] as const) {
      expect(bar[field], `${field} must be null on a partial bar`).toBeNull();
    }
  });
});
