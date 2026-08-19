"use client";

import { useEffect, useState } from "react";

import { clock, fmt } from "@/lib/format";
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
 * It polls the same endpoint and on the same schedule as the chart's
 * candle, deliberately. Two timers would let the two numbers on this screen
 * disagree by up to a poll interval, and "the chart says 467.70 and the
 * header says 465.10" reads as a bug in the data rather than as a race
 * between two fetches.
 */

/** Matches `TickerChart`'s `LIVE_POLL_MS`. See the note above. */
const POLL_MS = 45_000;

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

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const url = `/api/live?sym=${encodeURIComponent(ticker)}&bar=${encodeURIComponent(barDate)}`;
        const response = await fetch(url);
        if (!response.ok) return;
        const { quote: next } = (await response.json()) as { quote: LiveQuote | null };
        // A null after a session ends leaves the last quote on screen with
        // its own timestamp beside it. Blanking it would remove the record
        // of what the last price was, which is the thing a reader arriving
        // after the close wants.
        if (!cancelled && next) setQuote(next);
      } catch {
        // Same reasoning as the chart: the visible timestamp stops
        // advancing, and that is the honest report of a failed poll.
      }
    };

    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [ticker, barDate]);

  if (!quote) return null;

  return (
    <>
      <span className="k">live</span>
      <span
        className={`num big ${close !== null && quote.price >= close ? "up" : "down"}`}
        title={`polled ${clock(quote.ts)} ET`}
      >
        {fmt(quote.price)}
      </span>
    </>
  );
}
