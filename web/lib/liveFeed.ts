import type { ChartBar, LiveQuote } from "./ticker";

/**
 * One `/api/live` poll per ticker, shared by every component showing it.
 *
 * **Why (2026-11-01).** The header's `LivePrice` and the chart's candle
 * each ran their own poll of the same endpoint for the same row: two
 * requests on mount, two every 45 seconds, two on every return to the tab.
 * They now subscribe to one feed, so each tick is one request, and the two
 * numbers on screen come from one response rather than from two fetches
 * that agree because they ran close together.
 *
 * Client-side only: every function here touches `document` or `fetch`, and
 * nothing here imports a value from a module that reaches `pg`.
 */

export interface LiveSnapshot {
  bar: ChartBar | null;
  quote: LiveQuote | null;
}

type Listener = (snapshot: LiveSnapshot) => void;

/**
 * How often the open session's row is re-read, in milliseconds.
 *
 * The poller writes `bars_live` on a five-minute cycle, so anything under
 * that returns the same row. 45 seconds is deliberately faster than the
 * write: it bounds how long a fresh price sits in the database unseen to
 * well under a minute, at the cost of a bounded SELECT against a primary
 * key. Matching the five minutes exactly would put the worst case at ten.
 */
export const LIVE_POLL_MS = 45_000;

interface Feed {
  listeners: Set<Listener>;
  /** The page's last closed bar date, sent so the quote can say whether it
   * is ahead of it. Any subscriber may supply it. */
  barDate: string;
  timer: ReturnType<typeof setInterval> | null;
  /** Set once the database says the session is over. */
  closed: boolean;
  onVisible: () => void;
}

const feeds = new Map<string, Feed>();

/**
 * Subscribe to the ticker's live row. Returns the unsubscribe function, so
 * a component's effect can return it directly.
 *
 * The first subscriber starts the feed and the last one out stops it. The
 * first request goes out on a microtask: React runs every passive effect of
 * a commit in one synchronous pass, so the header and the chart, mounting
 * together, both subscribe before it leaves and one response serves both.
 */
export function subscribeLive(ticker: string, listener: Listener, barDate = ""): () => void {
  let feed = feeds.get(ticker);
  if (!feed) {
    const created: Feed = {
      listeners: new Set(),
      barDate,
      timer: null,
      closed: false,
      onVisible: () => void refresh(ticker, created),
    };
    created.timer = setInterval(() => void refresh(ticker, created), LIVE_POLL_MS);
    // Coming back to the tab refreshes at once rather than up to 45 s late.
    document.addEventListener("visibilitychange", created.onVisible);
    feeds.set(ticker, created);
    queueMicrotask(() => void refresh(ticker, created));
    feed = created;
  }
  if (barDate) feed.barDate = barDate;
  feed.listeners.add(listener);

  const owned = feed;
  return () => {
    owned.listeners.delete(listener);
    if (owned.listeners.size === 0 && feeds.get(ticker) === owned) stop(ticker, owned);
  };
}

function stop(ticker: string, feed: Feed): void {
  if (feed.timer !== null) clearInterval(feed.timer);
  feed.timer = null;
  document.removeEventListener("visibilitychange", feed.onVisible);
  feeds.delete(ticker);
}

/**
 * One request, delivered to every subscriber.
 *
 * **Only while the tab is visible.** A backgrounded tab that keeps polling
 * is a request every 45 seconds forever against a database the jobs also
 * want (ADR 039).
 *
 * **Stops once the session ends.** A row with `marketOpen === false` is
 * delivered, so the candle and the header take their last values, and then
 * the feed goes quiet: nothing rewrites `bars_live` until the next session,
 * so neither the timer nor a return to the tab asks again.
 *
 * A failed request delivers nothing. The last good values stay on screen
 * and the chart's poll timestamp stops advancing, which is the honest
 * report of a failed poll.
 */
async function refresh(ticker: string, feed: Feed): Promise<void> {
  if (feeds.get(ticker) !== feed || feed.closed) return;
  if (document.visibilityState !== "visible") return;
  try {
    const bar = feed.barDate ? `&bar=${encodeURIComponent(feed.barDate)}` : "";
    const response = await fetch(`/api/live?sym=${encodeURIComponent(ticker)}${bar}`);
    if (!response.ok) return;
    const snapshot = (await response.json()) as LiveSnapshot;
    // Every subscriber left while the request was out.
    if (feeds.get(ticker) !== feed) return;
    if (snapshot.bar?.marketOpen === false) {
      feed.closed = true;
      if (feed.timer !== null) clearInterval(feed.timer);
      feed.timer = null;
    }
    for (const listener of feed.listeners) listener(snapshot);
  } catch {
    // See above: nothing is delivered and the last good values stand.
  }
}
