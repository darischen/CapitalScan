import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * `FEED_DOMAIN` and `v_screen_live`'s WHERE clause must agree.
 *
 * Every *date* question — the newest date, the previous and next dates with
 * rows, the calendar's per-day counts — reads `events` directly rather than
 * the view. The view carries four `LEFT JOIN LATERAL` subqueries and
 * Postgres cannot drop an unused one, so asking it a date question runs all
 * four over the whole history. After ADR 122's rebuild took the live
 * `touch` slice from 157k to 1.31M rows, that was **23.3 s** through the
 * view against **0.17 ms** on the table.
 *
 * The cost is a duplicated predicate. If the two drift, the arrows offer a
 * date the table has no rows for, or the calendar marks a day populated and
 * the click lands on an empty screen — a disagreement that shows up as a
 * confusing page rather than as an error.
 */

const ROOT = join(__dirname, "..");
const SCREEN = readFileSync(join(ROOT, "lib", "screen.ts"), "utf8");
const VIEWS = readFileSync(
  join(ROOT, "..", "capitalscan", "jobs", "views.py"),
  "utf8",
);

/** `v_screen_live`'s WHERE clause, from the DDL that creates it. */
function liveWhere(): string {
  const start = VIEWS.indexOf('V_SCREEN_LIVE_DDL = f"""');
  expect(start, "V_SCREEN_LIVE_DDL not found in views.py").toBeGreaterThan(-1);
  const body = VIEWS.slice(start, VIEWS.indexOf('"""', start + 30));
  const where = body.lastIndexOf("WHERE");
  expect(where, "no WHERE clause in V_SCREEN_LIVE_DDL").toBeGreaterThan(-1);
  return body.slice(where);
}

describe("the feed's domain is stated the same way in both places", () => {
  it.each([
    ["entry_kind", /entry_kind\s*=\s*'touch'/],
    ["in_trade", /\bin_trade\b/],
    ["config_hash", /config_hash\s*=\s*current_setting\(\s*'capitalscan\.default_config_hash'/],
  ])("%s appears in both FEED_DOMAIN and the view", (_name, pattern) => {
    const start = SCREEN.indexOf("const FEED_DOMAIN = `");
    expect(start).toBeGreaterThan(-1);
    const domain = SCREEN.slice(start, SCREEN.indexOf("`;", start));

    expect(domain).toMatch(pattern);
    expect(liveWhere()).toMatch(pattern);
  });

  /**
   * The inverse, and the one that would actually bite. ADR 124 removed the
   * cluster-head filter from the view; if `FEED_DOMAIN` still carried it,
   * the date arrows would skip days whose only rows are cluster repeats —
   * days the table renders perfectly well.
   */
  it("neither carries a cluster-head filter", () => {
    const start = SCREEN.indexOf("const FEED_DOMAIN = `");
    const domain = SCREEN.slice(start, SCREEN.indexOf("`;", start));
    expect(domain).not.toMatch(/is_cluster_head/);
    expect(liveWhere()).not.toMatch(/is_cluster_head/);
  });

  /** A view that gained a predicate `FEED_DOMAIN` does not know about would
   * make the dates a superset of the rows. Counting the conjuncts is crude
   * and catches exactly that. */
  it("the view's WHERE clause has no predicate FEED_DOMAIN lacks", () => {
    const where = liveWhere();
    const conjuncts = (where.match(/\bAND\b/g) ?? []).length;
    expect(
      conjuncts,
      `v_screen_live's WHERE gained a predicate; check FEED_DOMAIN in screen.ts:\n${where}`,
    ).toBe(2);
  });
});
