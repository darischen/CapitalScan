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
      events(ticker, { limit: 200 }),
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

  it("the event history is one row per signal, not one per entry kind", async () => {
    const { events } = await import("@/lib/ticker");
    const history = await events("TSM", { limit: 200 });
    const keys = history.map((e) => `${e.signalDate}|${e.signalType}`);
    // `v_events` carries four entry kinds. Unfiltered, every signal would
    // appear four times.
    expect(new Set(keys).size).toBe(keys.length);
  });
});
