import { describe, expect, it } from "vitest";

import { clampLimit, isoDate, MAX_LIMIT, sideFor } from "../lib/screen";
import { databaseUrl, num, readOnlyRole } from "../lib/db";

/**
 * The TypeScript half of the contract.
 *
 * ADR 118 puts these routes on the views with no Python validator behind
 * them, so the rules the handler layer enforces by raising have to be
 * enforced here by test. Each block below names the rule it stands in for.
 */

describe("side derivation (ADR 118, ADR 102)", () => {
  // `v_screen` carries no `side` column, and `core.signals.detect` emits one
  // hit per side and never mixes long and short types on one row, so the
  // type determines the side exactly.
  it("maps every short-side type to short", () => {
    for (const t of [
      "bb_upper_touch",
      "stoch_overbought",
      "confluence_high",
      "bear_close_above_upper",
    ]) {
      expect(sideFor(t)).toBe("short");
    }
  });

  it("maps every long-side type to long", () => {
    for (const t of ["bb_lower_touch", "stoch_oversold", "confluence_low"]) {
      expect(sideFor(t)).toBe("long");
    }
  });

  it("covers all seven types core.types.SignalType declares", () => {
    // If this count changes, `SHORT_SIGNALS` in lib/screen.ts needs the new
    // member or it renders with a long rail by default -- silently wrong in
    // the one place the design encodes information with colour.
    const all = [
      "bb_lower_touch",
      "bb_upper_touch",
      "stoch_oversold",
      "stoch_overbought",
      "confluence_low",
      "confluence_high",
      "bear_close_above_upper",
    ];
    expect(all).toHaveLength(7);
    for (const t of all) {
      expect(["long", "short"]).toContain(sideFor(t));
    }
  });
});

describe("limit (ADR 074)", () => {
  it("caps at 200 regardless of what is passed", () => {
    expect(clampLimit(10_000)).toBe(MAX_LIMIT);
    expect(MAX_LIMIT).toBe(200);
  });

  it("passes a reasonable value through", () => {
    expect(clampLimit(25)).toBe(25);
  });

  it("means no limit when absent, so the page shows the whole day", () => {
    // The 50-row default this replaced truncated roughly one trading day in
    // twenty: measured over 4,108 days, the largest is 134 rows and the
    // 95th percentile is 79. `null` becomes `LIMIT ALL` in Postgres.
    expect(clampLimit(undefined)).toBeNull();
  });

  it("clamps a nonsense value rather than throwing", () => {
    // A `limit=0` from a loop counter should return a row and be noticed,
    // not raise somewhere unrelated to limits.
    expect(clampLimit(0)).toBe(1);
    expect(clampLimit(-5)).toBe(1);
    expect(clampLimit(Number.NaN)).toBeNull();
  });
});

describe("numeric conversion", () => {
  // `pg` returns `numeric` as a string so it never silently narrows a value
  // the database stores with more precision than a double holds.
  it("converts the strings pg returns", () => {
    expect(num("0.849213")).toBe(0.849213);
    expect(num("28.0894")).toBe(28.0894);
  });

  it("keeps null as null rather than turning it into NaN", () => {
    // The failure this prevents: `parseFloat(null)` is NaN, and NaN renders
    // as an empty cell that looks exactly like a legitimately absent value
    // sitting next to a real number.
    expect(num(null)).toBeNull();
    expect(num(undefined)).toBeNull();
    expect(num("not a number")).toBeNull();
  });

  it("does not round", () => {
    // 0.849 and 0.8492 are different statements about a q-value.
    expect(num("0.8492")).toBe(0.8492);
  });
});

describe("database url (ADR 070, ADR 118)", () => {
  it("prefers the read-only role", () => {
    const env = {
      DATABASE_URL_MCP: "postgresql://ro@h/db",
      DATABASE_URL_RESEARCH: "postgresql://rw@h/db",
    };
    expect(databaseUrl(env)).toBe("postgresql://ro@h/db");
    expect(readOnlyRole(env)).toBe(true);
  });

  it("falls back to the research role and says so", () => {
    const env = { DATABASE_URL_RESEARCH: "postgresql://rw@h/db" };
    expect(databaseUrl(env)).toBe("postgresql://rw@h/db");
    expect(readOnlyRole(env)).toBe(false);
  });

  it("refuses to guess when neither is set", () => {
    expect(() => databaseUrl({})).toThrow(/DATABASE_URL/);
  });
});

describe("dates", () => {
  it("takes the calendar day from a pg Date", () => {
    // `pg` hands back a JS Date at local midnight. Using toISOString() here
    // would shift the day backwards for anyone west of UTC, which is
    // everyone running this.
    expect(isoDate(new Date(2026, 7, 18))).toBe("2026-08-18");
  });

  it("passes a string through", () => {
    expect(isoDate("2026-08-18")).toBe("2026-08-18");
  });
});
