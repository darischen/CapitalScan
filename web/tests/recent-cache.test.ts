import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { recentRealisedOrNone, resetRecentCache } from "@/lib/reliability";

/**
 * `recentRealisedOrNone` serves its cache stale while one refresh runs
 * (2026-11-01). Before, an expired cache made the next page load wait for
 * the whole query, ~1.2 s on the Pi. The query itself is mocked: what is
 * pinned here is when a caller waits and when it does not.
 */

const { query } = vi.hoisted(() => ({ query: vi.fn() }));
vi.mock("@/lib/db", () => ({ query }));

const TEN_MINUTES = 10 * 60 * 1000;

/** One resolved long-side row, so the summary is non-null and comparable. */
function rowsStated(stated: number) {
  return [{ as_of: "2026-10-30", side: "long", stated, realised: 1 }];
}

/** A query that stays pending until the test resolves it. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-01T12:00:00Z"));
  query.mockReset();
  resetRecentCache();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("recentRealisedOrNone", () => {
  it("waits for the first query in a process", async () => {
    query.mockResolvedValueOnce(rowsStated(0.6));
    const got = await recentRealisedOrNone();
    expect(got.long?.stated).toBe(0.6);
    expect(query).toHaveBeenCalledTimes(1);
  });

  it("answers from the cache while it is fresh", async () => {
    query.mockResolvedValueOnce(rowsStated(0.6));
    await recentRealisedOrNone();
    vi.advanceTimersByTime(TEN_MINUTES - 1);
    const got = await recentRealisedOrNone();
    expect(got.long?.stated).toBe(0.6);
    expect(query).toHaveBeenCalledTimes(1);
  });

  it("answers stale at once after expiry and refreshes behind it", async () => {
    query.mockResolvedValueOnce(rowsStated(0.6));
    await recentRealisedOrNone();
    vi.advanceTimersByTime(TEN_MINUTES);

    const pending = deferred<unknown[]>();
    query.mockReturnValueOnce(pending.promise);
    // Neither call waits for the refresh, and the two share one query.
    expect((await recentRealisedOrNone()).long?.stated).toBe(0.6);
    expect((await recentRealisedOrNone()).long?.stated).toBe(0.6);
    expect(query).toHaveBeenCalledTimes(2);

    pending.resolve(rowsStated(0.7));
    await vi.waitFor(async () => {
      expect((await recentRealisedOrNone()).long?.stated).toBe(0.7);
    });
    expect(query).toHaveBeenCalledTimes(2);
  });

  it("keeps the old figures when a refresh fails, and retries on the next call", async () => {
    query.mockResolvedValueOnce(rowsStated(0.6));
    await recentRealisedOrNone();
    vi.advanceTimersByTime(TEN_MINUTES);

    query.mockRejectedValueOnce(new Error("connection reset"));
    expect((await recentRealisedOrNone()).long?.stated).toBe(0.6);
    await vi.waitFor(() => expect(query).toHaveBeenCalledTimes(2));

    query.mockResolvedValueOnce(rowsStated(0.8));
    expect((await recentRealisedOrNone()).long?.stated).toBe(0.6);
    await vi.waitFor(async () => {
      expect((await recentRealisedOrNone()).long?.stated).toBe(0.8);
    });
    expect(query).toHaveBeenCalledTimes(3);
  });

  it("returns no figures when the first query fails, and caches nothing", async () => {
    query.mockRejectedValueOnce(new Error("down"));
    expect(await recentRealisedOrNone()).toEqual({ long: null, short: null });
    query.mockResolvedValueOnce(rowsStated(0.6));
    expect((await recentRealisedOrNone()).long?.stated).toBe(0.6);
  });
});
