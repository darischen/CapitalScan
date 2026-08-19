import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The legend's swatches and the chart's lines use the same tokens.
 *
 * **This is a real defect that shipped.** The slow stochastic was recoloured
 * from `--long` to `--muted` in `TickerChart.tsx` and its legend swatch was
 * not, so the legend showed a blue line the chart never painted. Nobody
 * would catch that reading either file: the colour is in the component, the
 * swatch is in the stylesheet, and each is correct on its own.
 *
 * A swatch is a promise about a pixel somewhere else on the page. Nothing
 * enforced it, so this does.
 *
 * It reads source text rather than rendering. The chart is a canvas — the
 * colour never becomes a DOM property to assert on, and a screenshot test
 * would be comparing anti-aliased pixels to catch a token rename.
 */

const ROOT = join(__dirname, "..");
const CHART = readFileSync(join(ROOT, "components", "TickerChart.tsx"), "utf8");
const CSS = readFileSync(join(ROOT, "app", "globals.css"), "utf8");

/**
 * The token a series is drawn in, read from its `addSeries` options.
 *
 * Anchored on the `const <name> = chart.addSeries(` declaration and takes
 * the first `color: t.<token>` after it, which is that call's own options
 * object.
 */
function seriesToken(name: string): string {
  const start = CHART.indexOf(`const ${name} = chart.addSeries(`);
  expect(start, `no series named ${name} in TickerChart.tsx`).toBeGreaterThan(-1);
  const match = /color:\s*t\.(\w+)/.exec(CHART.slice(start, start + 600));
  expect(match, `no color found for series ${name}`).not.toBeNull();
  return match![1];
}

/** The token a legend swatch is drawn in, read from its CSS rule. */
function swatchToken(cls: string): string {
  const rule = new RegExp(`\\.legend \\.ln\\.${cls}\\s*\\{[^}]*border-top-color:\\s*var\\(--([\\w-]+)\\)`);
  const match = rule.exec(CSS);
  expect(match, `no .legend .ln.${cls} rule with a border-top-color`).not.toBeNull();
  return match![1];
}

describe("the legend matches the chart", () => {
  it.each([
    ["kFast", "kfast"],
    ["kFull", "kfull"],
    ["sma", "sma"],
  ])("the %s series and the .%s swatch use the same token", (series, cls) => {
    expect(swatchToken(cls)).toBe(seriesToken(series));
  });

  /**
   * The two stochastic lines must not resolve to the same token.
   *
   * Gate item 8 is that both `%K` series render *and are distinguishable*,
   * and they sit within `fast_agreement_tol` of each other by construction
   * (ADR 110) — so identical colours would draw one line on top of itself
   * and satisfy every other test in the suite.
   */
  it("the two stochastic lines are different colours", () => {
    expect(seriesToken("kFast")).not.toBe(seriesToken("kFull"));
  });

  /** The bands are drawn in a loop rather than as named series, so they are
   * checked by their own token rather than through `seriesToken`. */
  it("the band swatch is the token the band series uses", () => {
    const start = CHART.indexOf("const bandSeries =");
    expect(start).toBeGreaterThan(-1);
    const match = /color:\s*t\.(\w+)/.exec(CHART.slice(start, start + 600));
    expect(match).not.toBeNull();
    expect(swatchToken("band")).toBe(match![1]);
  });

  /** The crosshair value tags carry the same colours, for the same reason:
   * a number beside a line is matched to it by colour. */
  it("the stochastic value tags match their lines", () => {
    const tag = (cls: string) => {
      const rule = new RegExp(`\\.stoch-tag\\.${cls}\\s*\\{[^}]*color:\\s*var\\(--([\\w-]+)\\)`);
      const match = rule.exec(CSS);
      expect(match, `no .stoch-tag.${cls} rule`).not.toBeNull();
      return match![1];
    };
    expect(tag("fast")).toBe(seriesToken("kFast"));
    expect(tag("slow")).toBe(seriesToken("kFull"));
    expect(tag("sma")).toBe(seriesToken("sma"));
  });

  /** `%D` is gone from the chart, so its swatch must be gone too — a rule
   * for a line nobody draws is the same defect one step later. */
  it("no swatch survives for a line the chart does not draw", () => {
    expect(CSS).not.toContain(".legend .ln.dfull");
    expect(CHART).not.toContain("dFull");
  });
});
