import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterAll, describe, expect, it } from "vitest";

/**
 * The checks that need the real database.
 *
 * **Skipped when no connection string is present**, which is how CI runs:
 * its container has a migrated schema and no data, and every assertion here
 * is about data. Locally they run and they are the only place gate item 1
 * ("render against the live database") and 17.3's marker-alignment
 * acceptance are actually tested rather than eyeballed.
 *
 * The skip is loud on purpose — `it` reports as skipped rather than passing
 * — because a suite that silently passes with nothing connected is the
 * shape of test that reports green forever.
 */

// `next.config.ts` does this for the app. Vitest does not load it.
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

/** Three tickers, as 17.3's acceptance criterion asks for. */
const TICKERS = ["TSM", "AAPL", "NVDA"];

describe.skipIf(!connected)("against the live database", () => {
  afterAll(async () => {
    const { getPool } = await import("@/lib/db");
    await getPool().end();
  });

  it.each(TICKERS)("%s: the chart returns one row per session", async (ticker) => {
    const { chart } = await import("@/lib/ticker");
    const bars = await chart(ticker, "1y");

    expect(bars.length).toBeGreaterThan(200);
    // ADR 120. Before the fix this was 963 rows for 275 sessions, because
    // the events join matched all 22 config hashes.
    expect(new Set(bars.map((b) => b.ts)).size).toBe(bars.length);
  });

  it.each(TICKERS)("%s: every marker lands on a real signal date", async (ticker) => {
    const { chart, events } = await import("@/lib/ticker");
    const [bars, history] = await Promise.all([
      chart(ticker, "1y"),
      // `all: true`. The default is confluence-only (user's request,
      // 2026-08-19) while the chart marks every event, so the default set
      // is a strict subset of the markers and this comparison would fail on
      // correct data.
      events(ticker, { limit: 200, all: true }),
    ]);

    const marked = bars.filter((b) => b.eventIds.length > 0).map((b) => b.ts);
    const fired = new Set(history.map((e) => e.signalDate));

    expect(marked.length).toBeGreaterThan(0);
    // The check the date bug would have failed. `v_chart.ts` is a
    // `timestamptz` and `v_events.signal_date` is a `date`; reading the
    // first through local getters put every marker one session left of its
    // own history row, with no error anywhere. Both are cast `::date` now.
    for (const ts of marked) {
      expect(fired, `${ticker} marker on ${ts} has no event`).toContain(ts);
    }
  });

  it.each(TICKERS)("%s: the state's as-of matches the newest bar drawn", async (ticker) => {
    const { chart, state } = await import("@/lib/ticker");
    const [bars, current] = await Promise.all([chart(ticker, "6m"), state(ticker)]);

    expect(current).not.toBeNull();
    expect(current?.asOf).toBe(bars[bars.length - 1]?.ts);
  });

  it("returns null for a symbol with no indicator history", async () => {
    const { state } = await import("@/lib/ticker");
    expect(await state("ZZZZNOTATICKER")).toBeNull();
  });

  it("both %K series carry values, not just one", async () => {
    const { chart } = await import("@/lib/ticker");
    const bars = await chart("TSM", "1y");
    expect(bars.filter((b) => b.kFast !== null).length).toBeGreaterThan(200);
    expect(bars.filter((b) => b.kFull !== null).length).toBeGreaterThan(200);
    // They must not be the same column read twice, which is the way a chart
    // "showing both" ends up showing one line drawn on top of itself.
    expect(bars.some((b) => b.kFast !== b.kFull)).toBe(true);
  });

  it.each(TICKERS)("%s: a page before the oldest bar returns older bars", async (ticker) => {
    const { chart, chartBefore } = await import("@/lib/ticker");
    const bars = await chart(ticker, "1y");
    const oldest = bars[0].ts;

    const page = await chartBefore(ticker, oldest, 60);
    expect(page.length).toBe(60);
    // Strictly before, so the cursor bar is not sent twice — a duplicate
    // timestamp makes `setData` throw and blanks the chart.
    expect(page.every((b) => b.ts < oldest)).toBe(true);
    // Ascending, which is the order the chart takes.
    expect([...page].sort((a, b) => a.ts.localeCompare(b.ts))).toEqual(page);
  });

  it("the default history is confluence only, with no reversal requirement", async () => {
    const { countEvents, events } = await import("@/lib/ticker");
    // **Counts, not list lengths.** Both lists are capped at
    // `MAX_EVENT_LIMIT`, and after ADR 122's rebuild TSM has 377
    // confluences against 2,292 total — so comparing two 200-element lists
    // asserted `200 < 200` and failed. The cap is the thing under test
    // everywhere else; here it hides the property.
    const [nConfluence, nAll, confluence] = await Promise.all([
      countEvents("TSM", false),
      countEvents("TSM", true),
      events("TSM", { limit: 200 }),
    ]);

    expect(nConfluence).toBeGreaterThan(0);
    expect(nConfluence).toBeLessThan(nAll);
    expect(confluence.length).toBeGreaterThan(0);
    // Membership of `signal_types_all`, not of `signal_type` — ADR 057
    // stores only the most specific type there, so a bar that fired both
    // `bear_close_above_upper` and `confluence_high` keeps the reversal and
    // a filter on the single column would drop it.
    for (const e of confluence) {
      expect(e.signalTypesAll.some((t) => t.startsWith("confluence"))).toBe(true);
    }
    // ADR 111 makes a short actionable only with a confirming reversal.
    // This filter deliberately does not, so a confluence with no
    // `bear_close_above_upper` must still be here.
    expect(
      confluence.some((e) => !e.signalTypesAll.includes("bear_close_above_upper")),
    ).toBe(true);
  });

  /**
   * **The `?stats=1` path, which shipped broken.** `screen.ts` filters
   * `arm = 'signal'` on `v_stats`, and `v_stats` did not project `arm`
   * until ADR 126 — so every request returned `column "arm" does not
   * exist` and rendered the error state.
   *
   * `states.test.tsx` covers the *component* with a `CellStats` fixture and
   * never runs the query, which is exactly why a fixture test is not a
   * substitute for one round trip against the real view.
   */
  it("attaches cell statistics without throwing", async () => {
    const { screen } = await import("@/lib/screen");
    const result = await screen({ withStats: true, confluenceOnly: false, limit: 20 });

    expect(result.rows.length).toBeGreaterThan(0);
    const withCell = result.rows.filter((r) => r.cellId !== null);
    expect(withCell.length).toBeGreaterThan(0);

    // ADR 112: zero cells survive correction, so a real cell here is
    // either suppressed or carries a q-value above `fdr_alpha`. Both are
    // valid; a bare probability is not.
    for (const row of withCell) {
      if (row.stats === null) continue;
      if (row.stats.kind === "suppressed") {
        expect(row.stats.reason).toBeTruthy();
      } else {
        // Invariant 8: never a rate without its companions.
        expect(row.stats.nEff).not.toBeNull();
        expect(row.stats.ciLow).not.toBeNull();
        expect(row.stats.ciHigh).not.toBeNull();
        expect(row.stats.qValue).not.toBeNull();
      }
    }
  });

  it("returns an empty page at the start of history, which is how paging stops", async () => {
    const { chartBefore } = await import("@/lib/ticker");
    expect(await chartBefore("TSM", "1900-01-01", 10)).toEqual([]);
  });

  it("the event history is one row per signal, not one per entry kind", async () => {
    const { events } = await import("@/lib/ticker");
    const history = await events("TSM", { limit: 200, all: true });
    const keys = history.map((e) => `${e.signalDate}|${e.signalType}`);
    // `v_events` carries four entry kinds. Unfiltered, every signal would
    // appear four times.
    expect(new Set(keys).size).toBe(keys.length);
  });
});
