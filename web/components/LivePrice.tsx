"use client";

import { useEffect, useState } from "react";

import { DISPLAY_TZ_LABEL, clock, fmt } from "@/lib/format";
import { subscribeLive } from "@/lib/liveFeed";
import type { LiveQuote } from "@/lib/ticker";

/**
 * The live quote in the ticker header, kept current.
 *
 * The page around it is a server component, which is right for everything
 * else on it: the close, the bands, the event history, and the indicator
 * state are all settled numbers that do not change until the nightly job
 * runs. The quote is the one thing that moves, so it is the one thing that
 * is a client component.
 *
 * It reads the same poll as the chart's candle, deliberately, and since
 * 2026-11-01 the same response: both subscribe to `lib/liveFeed.ts`. Two
 * timers would let the two numbers on this screen disagree by up to a poll
 * interval, and "the chart says 467.70 and the header says 465.10" reads as
 * a bug in the data rather than as a race between two fetches.
 */

export default function LivePrice({
  ticker,
  barDate,
  close,
  initial,
}: {
  ticker: string;
  barDate: string;
  /** The last closed bar, for the up/down comparison. */
  close: number | null;
  initial: LiveQuote | null;
}) {
  const [quote, setQuote] = useState<LiveQuote | null>(initial);

  // **Null means the session is over, and the price goes away.** After the
  // close the poller's last tick ages in place — TSLA read 349.58 at 15:25
  // PT against a 351.12 close — so keeping it on screen would show a stale
  // number under a "live" label. The close beside it is the answer once
  // trading ends, and it takes over the large size on its own.
  //
  // The feed owns the timer, the visibility rule, the end-of-session stop
  // and the failure handling, for this and the chart alike.
  useEffect(
    () => subscribeLive(ticker, (snapshot) => setQuote(snapshot.quote), barDate),
    [ticker, barDate],
  );

  if (!quote) return null;

  return (
    <>
      <span className="k">live</span>
      <span
        className={`num big ${close !== null && quote.price >= close ? "up" : "down"}`}
        title={`polled ${clock(quote.ts)} ${DISPLAY_TZ_LABEL}`}
      >
        {fmt(quote.price)}
      </span>
    </>
  );
}
