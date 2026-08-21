import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { CHART_ID, chartUrl, stockchartsSymbol } from "@/lib/stockcharts";

/**
 * Links out to StockCharts, and the two ways they go wrong silently.
 *
 * Neither failure mode here produces an error. A mistranslated symbol
 * returns a "not found" image, and a drifted config returns a real chart
 * drawn with the wrong numbers. Both look like a working link.
 */

const CONFIG = readFileSync(
  join(__dirname, "..", "..", "capitalscan", "core", "config.py"),
  "utf8",
);

/**
 * Reads a default off a frozen dataclass field in `core/config.py`.
 *
 * Line-oriented rather than a regex: every field in that file is written
 * `name: type = value`, one per line, and a parser that says so is easier
 * to read than the pattern that matches it. Trailing comments are stripped
 * because several of these fields carry one.
 */
function configValue(field: string): string {
  for (const raw of CONFIG.split("\n")) {
    const line = raw.trim();
    if (!line.startsWith(`${field}:`)) continue;
    const equals = line.indexOf("=");
    if (equals === -1) continue;
    return line
      .slice(equals + 1)
      .split("#")[0]
      .trim();
  }
  throw new Error(`${field} not found in core/config.py`);
}

describe("the chart the id points at still matches the config", () => {
  /**
   * **The failure this whole file exists for.**
   *
   * `CHART_ID` references a chart stored on StockCharts' servers. Nothing
   * in this repo can read what it draws, and nothing in it changes when
   * `core/config.py` does. Sweep `bb_std` to 2.5 and every link on the
   * screener keeps working, keeps rendering, and starts showing a reader
   * bands that are not the bands the signal fired against.
   *
   * So this pins the config values the stored chart was built from
   * (2026-08-20, verified against the rendered PNG). A config change fails
   * here, and the fix is to rebuild the chart on StockCharts and move the
   * id -- not to edit these numbers.
   *
   * Layout is deliberately not pinned here. The chart draws candlesticks
   * with the stochastic panel below price, and neither is a measurement, so
   * neither can disagree with `core/config.py`.
   */
  it.each([
    ["bb_window", "20", "BB(20,2.0)"],
    ["bb_std", "2.0", "BB(20,2.0)"],
    ["stoch_window", "14", "Full STO %K(14,3)"],
    ["stoch_smooth_k", "3", "Full STO %K(14,3)"],
    ["stoch_smooth_d", "3", "Full STO %D(3)"],
    ["stoch_oversold", "20.0", "the stochastic panel's lower gridline"],
    ["stoch_overbought", "80.0", "the stochastic panel's upper gridline"],
  ])("%s is still %s, which is what the chart draws as %s", (field, expected) => {
    expect(configValue(field)).toBe(expected);
  });

  /**
   * **What is deliberately not pinned.**
   *
   * The chart also draws `EMA(10)`, `EMA(50)` and `RSI(14)` (user's
   * specification, 2026-08-21). This project computes none of them, so
   * there is no config value they could drift away from and nothing here
   * could usefully assert. `sma_long` was pinned until that rebuild dropped
   * `MA(200)` for a second EMA -- the free tier allows three overlays and
   * the three on price use them.
   *
   * The rule this file follows: pin a setting when the chart and
   * `core/config.py` could silently disagree about what a signal was
   * measured against. A reading preference is not that.
   */
  it("does not claim the chart draws anything this project computes but it does not", () => {
    const source = readFileSync(join(__dirname, "..", "lib", "stockcharts.ts"), "utf8");
    // `sma_long` is still a real config field; the chart just stopped
    // drawing it. Naming it here would be a claim about someone else's
    // database that nothing checks.
    expect(source).not.toMatch(/MA\(200\)`? -- `sma_long/);
  });

  it("names a permanent chart, not a session-scoped draft", () => {
    // The workbench hands out `t...c` ids while you are editing and a `p`
    // number only after "Reload with Link". A `t` id here would work for
    // whoever generated it and 404 for everyone else.
    expect(CHART_ID).toMatch(/^p\d+$/);
  });
});

describe("symbols are spelled the way StockCharts spells them", () => {
  it("translates class shares, which are the only ones that differ", () => {
    // Verified 2026-08-20: `BRK-B` returns a 305x176 "not found" image,
    // `BRK/B` returns the 990x664 chart. Both are HTTP 200.
    expect(stockchartsSymbol("BRK-B")).toBe("BRK/B");
    expect(stockchartsSymbol("BF-B")).toBe("BF/B");
  });

  it("leaves ordinary symbols alone", () => {
    for (const plain of ["ROST", "PSX", "DAL", "PCAR", "AAPL"]) {
      expect(stockchartsSymbol(plain)).toBe(plain);
    }
  });

  it("normalises case and stray whitespace", () => {
    expect(stockchartsSymbol(" rost ")).toBe("ROST");
  });

  it("percent-encodes the slash rather than emitting a path separator", () => {
    // `s=BRK/B` unencoded would be read as a path, and the symbol would
    // arrive truncated.
    const url = chartUrl("BRK-B");
    expect(url).toContain("s=BRK%2FB");
    expect(url).not.toContain("s=BRK/B");
  });
});

describe("the url", () => {
  it("carries the symbol and the stored chart", () => {
    const url = new URL(chartUrl("ROST"));
    expect(url.searchParams.get("s")).toBe("ROST");
    expect(url.searchParams.get("id")).toBe(CHART_ID);
  });

  it("pins the period rather than inheriting it", () => {
    // The stored chart's own period lives in an account this project does
    // not control. A silent switch to weekly would draw 20-week bands
    // beside a signal computed on 20 days.
    expect(new URL(chartUrl("ROST")).searchParams.get("p")).toBe("D");
  });

  it("points at the workbench over https", () => {
    const url = new URL(chartUrl("ROST"));
    expect(url.protocol).toBe("https:");
    expect(url.host).toBe("stockcharts.com");
    // `c-sc/sc` is the rendered PNG. A reader following a link wants the
    // interactive page.
    expect(url.pathname).toBe("/sc3/ui");
  });
});
