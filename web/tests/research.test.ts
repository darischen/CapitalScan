import { readFileSync } from "node:fs";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * `/research`'s gate items, asserted against the source and against the
 * live database.
 *
 * The page exists to make a negative result legible (ADR 033), so most of
 * these check that nothing softens it: suppressed cells present, q-values
 * unrounded, intervals that span zero drawn as spanning zero.
 */

const ROOT = join(__dirname, "..");
const LIB = readFileSync(join(ROOT, "lib", "research.ts"), "utf8");
const PAGE = readFileSync(join(ROOT, "app", "research", "page.tsx"), "utf8");
const COMPONENT = readFileSync(join(ROOT, "components", "Research.tsx"), "utf8");

/* Load the root `.env.local`, as `next.config.ts` does for the app. */
const envPath = resolve(__dirname, "..", "..", ".env.local");
if (existsSync(envPath)) {
  for (const raw of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (process.env[key] === undefined) process.env[key] = line.slice(eq + 1).trim();
  }
}
const connected = Boolean(process.env.DATABASE_URL_MCP || process.env.DATABASE_URL_RESEARCH);

describe("gate 9: holdout is unreachable from /research", () => {
  /**
   * Stronger than "does not currently read holdout". The route takes no
   * `searchParams` at all, so there is no parameter a caller could set, and
   * the split list is a constant rather than an argument.
   */
  it("the route accepts no query parameters", () => {
    expect(PAGE).not.toMatch(/searchParams/);
  });

  it("neither the lib nor the page names holdout as a value", () => {
    // The word appears in prose explaining why it is absent; what must not
    // appear is holdout as a *string literal* reaching a query.
    expect(LIB).not.toMatch(/["']holdout["']/);
    expect(PAGE).not.toMatch(/["']holdout["']/);
  });

  it("the split list is a literal, not a parameter", () => {
    expect(LIB).toMatch(/\["train",\s*"validate"\]/);
  });
});

describe("gate 2: the research modules import no Python data layer", () => {
  it.each([
    ["lib/research.ts", LIB],
    ["app/research/page.tsx", PAGE],
    ["components/Research.tsx", COMPONENT],
  ])("%s", (_name, text) => {
    expect(text).not.toMatch(/\bsqlalchemy\b/i);
    expect(text).not.toMatch(/\bdb_io\b/);
  });
});

describe("the page does not soften the result", () => {
  /** Session 18's plan lists "rounding a q-value of 0.849 to 'not
   * significant' and hiding it" as a thing not to do. */
  it("renders q-values to three decimals rather than a label", () => {
    expect(COMPONENT).toMatch(/qValue.*toFixed\(3\)/s);
    // The phrase appears twice in comments explaining why it is *not* the
    // rendering. Checked against the JSX only.
    const jsx = COMPONENT.replace(/\/\*[\s\S]*?\*\//g, " ");
    expect(jsx).not.toMatch(/not significant/i);
  });

  it("renders a suppressed cell's stored reason and never a rate", () => {
    expect(COMPONENT).toMatch(/suppressReason/);
    // The suppressed branch must come before the rate columns, so a
    // suppressed cell cannot fall through to them.
    const suppressed = COMPONENT.indexOf("c.suppressed ? (");
    const rate = COMPONENT.indexOf("pct(c.pHit");
    expect(suppressed).toBeGreaterThan(-1);
    expect(suppressed).toBeLessThan(rate);
  });

  /** ADR 013: the `replication` column exists so the null renders as a
   * distribution. A mean answers a different question. */
  it("renders the null as a distribution, not a summary", () => {
    expect(LIB).toMatch(/replication IS NOT NULL/);
    expect(COMPONENT).toMatch(/counts\[/);
    expect(LIB).not.toMatch(/avg\(total_ret\)/);
  });
});

describe.skipIf(!connected)("against the live database", () => {
  it("returns every cell including the suppressed ones", async () => {
    const { cells } = await import("@/lib/research");
    const rows = await cells();

    expect(rows.length).toBeGreaterThan(0);
    expect(rows.some((c) => c.suppressed)).toBe(true);
    expect(rows.some((c) => !c.suppressed)).toBe(true);

    // ADR 026 is enforced in SQL: `v_stats` nulls the rate on a suppressed
    // cell, so this cannot be a rendering convention that someone forgets.
    for (const c of rows.filter((c) => c.suppressed)) {
      expect(c.pHit).toBeNull();
      expect(c.suppressReason).toBeTruthy();
    }
  });

  it("returns only train and validate, never holdout", async () => {
    const { cells } = await import("@/lib/research");
    const splits = new Set((await cells()).map((c) => c.splitKey));
    expect([...splits].sort()).toEqual(["train", "validate"]);
  });

  /** Gate item 2: era 2024+ is the holdout window and must be absent
   * everywhere on this page. */
  it("the era breakdown contains no 2024+ era", async () => {
    const { eraCells } = await import("@/lib/research");
    const eras = new Set((await eraCells()).map((c) => c.era));
    expect(eras.size).toBeGreaterThan(0);
    for (const era of eras) {
      expect(era).not.toMatch(/2024|2025|2026/);
    }
  });

  /** ADR 103: era is a reporting split, so a q-value there would claim a
   * test that was never run. */
  it("era cells carry no q-value", async () => {
    const { eraCells } = await import("@/lib/research");
    for (const c of await eraCells()) {
      expect(c.qValue).toBeNull();
    }
  });

  it("ADR 112 still holds: nothing survives correction", async () => {
    const { cells, GRID } = await import("@/lib/research");
    const scored = (await cells()).filter((c) => c.qValue !== null);

    expect(scored.length).toBeGreaterThan(0);
    // If this ever fails, the headline on the page is wrong and the ADR
    // needs revisiting — which is exactly when someone should be told.
    expect(scored.filter((c) => (c.qValue as number) <= GRID.fdrAlpha)).toHaveLength(0);
  });

  it("the null distribution is 200 replications per breadth split", async () => {
    const { nullDistribution } = await import("@/lib/research");
    const [pooled, breadth] = await Promise.all([
      nullDistribution("pooled"),
      nullDistribution("breadth_high"),
    ]);
    expect(pooled).toHaveLength(200);
    expect(breadth).toHaveLength(200);
  });

  /**
   * ADR 099's breadth split disagrees in sign on the signal arm, and the
   * page shows both rows. A regression that collapsed them would look like
   * a cleanup.
   */
  it("the signal arm has a row per breadth split, and they differ", async () => {
    const { arms } = await import("@/lib/research");
    const signal = (await arms()).filter((a) => a.arm === "signal");

    expect(signal).toHaveLength(2);
    expect(new Set(signal.map((a) => a.era))).toEqual(new Set(["pooled", "breadth_high"]));
    expect(signal[0].totalRet).not.toBe(signal[1].totalRet);
  });

  it("percentileOf places a value inside its own distribution", async () => {
    const { percentileOf } = await import("@/lib/research");
    const sorted = [-0.2, -0.1, 0, 0.1, 0.2];
    expect(percentileOf(-0.5, sorted)).toBe(0);
    expect(percentileOf(0.05, sorted)).toBe(0.6);
    expect(percentileOf(1, sorted)).toBe(1);
  });
});
