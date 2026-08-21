import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { screenHref } from "@/lib/format";
import { SIGNAL_ORDER, defaultDir, isSortKey, nextDir, type SortKey } from "@/lib/screen";

/**
 * Column sorting, and the ways a sort lies about what it sorted.
 *
 * The interesting properties are not "clicking Vol orders by volume". They
 * are that the ordering runs over the whole day rather than the rendered
 * page, that nothing a request sends reaches the SQL, and that a sort
 * survives the other controls on the page instead of being silently reset
 * by them.
 */

const SOURCE = readFileSync(join(__dirname, "..", "lib", "screen.ts"), "utf8");

const KEYS: SortKey[] = [
  "ticker",
  "signal",
  "strength",
  "bollinger",
  "stochastic",
  "open",
  "high",
  "low",
  "close",
  "volume",
  "live",
  "fired",
];

describe("only a key of the closed map can be sorted by", () => {
  /**
   * **The whole safety argument.** `feedSql` interpolates its `ORDER BY`,
   * which `boundary.test.ts` permits by name. That permission is only sound
   * while the string being interpolated cannot be influenced by a request,
   * and this is what makes that true: the request picks a *key*, never a
   * column.
   */
  it.each(KEYS)("accepts %s", (key) => {
    expect(isSortKey(key)).toBe(true);
  });

  it.each([
    ["a column name", "signal_strength"],
    ["a qualified column", "s.close"],
    ["injection", "close; DROP TABLE events"],
    ["a UNION", "1 UNION SELECT 1"],
    ["a subquery", "(SELECT 1)"],
    ["empty", ""],
    ["whitespace", " ticker"],
    ["different case", "Ticker"],
  ])("rejects %s", (_label, value) => {
    expect(isSortKey(value)).toBe(false);
  });

  it("rejects inherited object properties", () => {
    // `"constructor" in SORT_COLUMNS` is true for a plain object literal, so
    // an `in` check here would accept it and interpolate a function into a
    // query. `hasOwnProperty` is why it does not.
    for (const inherited of ["constructor", "toString", "__proto__", "valueOf"]) {
      expect(isSortKey(inherited)).toBe(false);
    }
  });

  it("rejects an absent parameter rather than defaulting to a column", () => {
    expect(isSortKey(undefined)).toBe(false);
  });
});

describe("the signal order is the one that was asked for", () => {
  /**
   * The user's specification, 2026-08-20: confluence above single
   * conditions, band above stochastic, and the short side above the long
   * side within each pair. Asserted against the source rather than through
   * a database, because the ranking is a `CASE` expression and the thing
   * worth pinning is its order.
   */
  it("ranks the six named types in the specified order", () => {
    const named = SIGNAL_ORDER.filter((t) => t !== "bear_close_above_upper");
    expect(named).toEqual([
      "confluence_high",
      "confluence_low",
      "bb_upper_touch",
      "bb_lower_touch",
      "stoch_overbought",
      "stoch_oversold",
    ]);
  });

  it("puts the close-confirmed reversal at the top", () => {
    // Not in the user's list. ADR 111 makes it the actionable short
    // condition and ADR 057 makes it the most specific type, so "high above
    // low" places it above `confluence_high`.
    expect(SIGNAL_ORDER[0]).toBe("bear_close_above_upper");
  });

  it("ranks every signal type the database produces", () => {
    // A type missing from the map falls into the `ELSE` bucket and sorts
    // last as a group, silently. These are the seven in
    // `SignalParams.enabled_signal_types`.
    expect([...SIGNAL_ORDER].sort()).toEqual(
      [
        "bb_lower_touch",
        "bb_upper_touch",
        "bear_close_above_upper",
        "confluence_high",
        "confluence_low",
        "stoch_overbought",
        "stoch_oversold",
      ].sort(),
    );
  });
});

describe("clicking a header twice reverses it", () => {
  it.each(KEYS)("%s flips when it is already active", (key) => {
    const first = defaultDir(key);
    const flipped = first === "asc" ? "desc" : "asc";
    expect(nextDir(key, key, first)).toBe(flipped);
    expect(nextDir(key, key, flipped)).toBe(first);
  });

  it.each(KEYS.filter((k) => k !== "close"))(
    "%s opens on its own natural direction, not the previous column's",
    (key) => {
      // Sorting by Close descending and then clicking Ticker should give
      // A-Z, not Z-A. Carrying the direction across columns makes the first
      // click on a new column answer the wrong question about half the time.
      //
      // `close` is excluded because it is the active column here, and an
      // active column is the one case that is supposed to flip.
      expect(nextDir(key, "close", "desc")).toBe(defaultDir(key));
      expect(nextDir(key, null, "desc")).toBe(defaultDir(key));
    },
  );

  it("reads names A-Z and quantities largest-first on the first click", () => {
    expect(defaultDir("ticker")).toBe("asc");
    // The signal rank and the stochastic count up from their most
    // interesting end, so ascending is the useful first answer.
    expect(defaultDir("signal")).toBe("asc");
    expect(defaultDir("stochastic")).toBe("asc");
    for (const quantity of ["strength", "volume", "close", "fired"] as const) {
      expect(defaultDir(quantity)).toBe("desc");
    }
  });
});

describe("a sort survives the rest of the page", () => {
  /**
   * Each of these controls rebuilds the URL from scratch. Any one of them
   * forgetting the sort would silently reorder the table as a side effect
   * of a date step or a statistics toggle, and the reader would read the
   * new order as new data.
   */
  it("is carried by the date and toggle links", () => {
    const href = screenHref("2026-08-19", {
      withStats: true,
      confluenceOnly: false,
      sort: "volume",
      dir: "desc",
    });
    expect(href).toContain("sort=volume");
    expect(href).toContain("dir=desc");
    expect(href).toContain("date=2026-08-19");
    expect(href).toContain("stats=1");
    expect(href).toContain("all=1");
  });

  it("emits no sort parameters when there is no sort", () => {
    const href = screenHref(null, { withStats: false, confluenceOnly: true });
    expect(href).toBe("/");
  });

  it("never emits a direction without a column", () => {
    // `?dir=asc` alone names a direction for the default order, which is not
    // something the query can honour — it would read as sorted and not be.
    const href = screenHref(null, {
      withStats: false,
      confluenceOnly: true,
      sort: null,
      dir: "asc",
    });
    expect(href).not.toContain("dir=");
  });
});

describe("the ordering runs in SQL, over the whole day", () => {
  it("orders before it limits", () => {
    // The reason this is a link and not an onClick. Sorting the rendered
    // rows would sort whatever the previous ordering happened to select,
    // and on 2026-08-20 that was 50 of 85 rows.
    const order = SOURCE.indexOf("ORDER BY ${order}");
    const limit = SOURCE.indexOf("LIMIT $2");
    expect(order).toBeGreaterThan(-1);
    expect(order).toBeLessThan(limit);
  });

  it("puts nulls last in both directions", () => {
    // Postgres defaults to nulls *first* on DESC. A missing close is not a
    // large close, and a descending sort that opened with blank rows would
    // bury the answer.
    const clause = /NULLS LAST, s\.ticker/;
    expect(SOURCE).toMatch(clause);
    expect(SOURCE).not.toMatch(/\$\{SORT_COLUMNS\[key\]\} \$\{[^}]*\}(?! NULLS LAST)/);
  });

  it("breaks ties on ticker, so equal values do not reorder between renders", () => {
    expect(SOURCE).toMatch(/NULLS LAST, s\.ticker/);
  });

  it("leaves the default order untouched", () => {
    // A page with no `?sort=` must render exactly what it rendered before
    // sorting existed: confluence rank, newest first, then alphabetical.
    expect(SOURCE).toMatch(
      /DEFAULT_ORDER = `\$\{CONFLUENCE_RANK\}, s\.fired_at DESC NULLS LAST, s\.ticker`/,
    );
  });
});
