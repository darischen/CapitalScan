import { describe, expect, it } from "vitest";

import { markers } from "@/components/TickerChart";
import { fmt, normalizeSymbol, pct, signedPct, vol } from "@/lib/format";
import { sideFor } from "@/lib/screen";
import {
  DEFAULT_EVENT_LIMIT,
  DEFAULT_RANGE,
  MAX_EVENT_LIMIT,
  RANGES,
  clampEvents,
  parseRange,
  type ChartBar,
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
