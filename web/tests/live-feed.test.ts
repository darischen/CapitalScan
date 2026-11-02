import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LIVE_POLL_MS, type LiveSnapshot, subscribeLive } from "@/lib/liveFeed";

/**
 * One `/api/live` poll per ticker (2026-11-01). The header's price and the
 * chart's candle each ran their own until then: two requests on mount and
 * two on every tick, for one row. The feed is browser code, so the two
 * globals it touches, `document` and `fetch`, are stubbed here.
 */

const listeners = new Map<string, () => void>();
const doc = {
  visibilityState: "visible" as "visible" | "hidden",
  addEventListener: (type: string, fn: () => void) => listeners.set(type, fn),
  removeEventListener: (type: string) => listeners.delete(type),
};
const fetchMock = vi.fn();

function respond(open: boolean): void {
  const body: LiveSnapshot = {
    bar: { ts: "2026-10-30", close: 101, marketOpen: open } as LiveSnapshot["bar"],
    quote: { price: 101, ts: "2026-10-30T17:00:00Z", aheadOfBar: true },
  };
  fetchMock.mockResolvedValue({ ok: true, json: async () => body });
}

/** Lets the microtask that sends the first request, and its response, run. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("document", doc);
  vi.stubGlobal("fetch", fetchMock);
  doc.visibilityState = "visible";
  fetchMock.mockReset();
  listeners.clear();
  respond(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("subscribeLive", () => {
  it("sends one request for two subscribers mounting together", async () => {
    const header = vi.fn();
    const chart = vi.fn();
    const offHeader = subscribeLive("TSM", header, "2026-10-29");
    const offChart = subscribeLive("TSM", chart);
    await settle();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/live?sym=TSM&bar=2026-10-29");
    expect(header).toHaveBeenCalledTimes(1);
    expect(chart).toHaveBeenCalledTimes(1);
    // Both read the one response.
    expect(header.mock.calls[0][0]).toBe(chart.mock.calls[0][0]);
    offHeader();
    offChart();
  });

  it("polls once per interval for the page, not once per component", async () => {
    const off = [subscribeLive("TSM", vi.fn()), subscribeLive("TSM", vi.fn())];
    await settle();
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    off.forEach((f) => f());
  });

  it("goes quiet once the session is over", async () => {
    respond(false);
    const seen = vi.fn();
    const off = subscribeLive("TSM", seen);
    await settle();
    expect(seen).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 3);
    listeners.get("visibilitychange")?.();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    off();
  });

  it("does not poll a hidden tab, and refreshes on return", async () => {
    doc.visibilityState = "hidden";
    const off = subscribeLive("TSM", vi.fn());
    await settle();
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 2);
    expect(fetchMock).not.toHaveBeenCalled();

    doc.visibilityState = "visible";
    listeners.get("visibilitychange")?.();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    off();
  });

  it("stops when the last subscriber leaves and starts fresh after", async () => {
    const offA = subscribeLive("TSM", vi.fn());
    const offB = subscribeLive("TSM", vi.fn());
    await settle();
    offA();
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    offB();
    expect(listeners.has("visibilitychange")).toBe(false);
    await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const offC = subscribeLive("TSM", vi.fn());
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    offC();
  });

  it("delivers nothing on a failed request", async () => {
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    const seen = vi.fn();
    const off = subscribeLive("TSM", seen);
    await settle();
    expect(seen).not.toHaveBeenCalled();
    off();
  });
});
