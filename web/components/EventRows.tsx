"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { NONE, SIGNAL_LABELS, fmt, signedPct } from "@/lib/format";
import { EVENT_PAGE, type TickerEvent } from "@/lib/ticker";

/**
 * The event table, paged as the reader scrolls.
 *
 * TSLA has 272 `touch` events and the page rendered 40, so five of six were
 * unreachable and the list looked truncated with no way to say by how much.
 * Fifty more arrive whenever the bottom of the table comes into view.
 *
 * A client component only for that reason. The first page still renders on
 * the server, so the table is complete HTML before any script runs — the
 * scroll handler adds to it rather than fetching it.
 */

function typeList(e: TickerEvent): string[] {
  const all = e.signalTypesAll;
  if (all.length === 0) return [e.signalType];
  if (all[0] === e.signalType) return all;
  return [e.signalType, ...all.filter((t) => t !== e.signalType)];
}

/**
 * The outcome of a closed event, or why there is not one.
 *
 * Three distinguishable states, and telling them apart is the reason this
 * is a function rather than a `??`. An event still open is not the same as
 * one that closed flat, and neither is the same as one the backtest has not
 * reached — the poller writes an event the moment it fires and
 * `cscan backtest` fills the outcome hours later.
 */
function Outcome({ e }: { e: TickerEvent }) {
  if (e.netRet !== null) {
    return <span className={`num ${e.netRet >= 0 ? "up" : "down"}`}>{signedPct(e.netRet)}</span>;
  }
  if (e.exitDate !== null) return <span className="dim">closed, no return</span>;
  return <span className="dim">open</span>;
}

export default function EventRows({
  sym,
  initial,
  all,
  total,
}: {
  sym: string;
  initial: TickerEvent[];
  all: boolean;
  total: number;
}) {
  const [rows, setRows] = useState<TickerEvent[]>(initial);
  const [done, setDone] = useState(initial.length >= total);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const sentinel = useRef<HTMLTableRowElement>(null);
  const inFlight = useRef(false);
  // The scroll observer fires on a stale closure otherwise, and paging from
  // `rows.length` at the time the observer was created re-fetches page one
  // forever.
  const countRef = useRef(initial.length);

  const loadMore = useCallback(async () => {
    if (inFlight.current || done) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const url = `/api/events?sym=${encodeURIComponent(sym)}&offset=${countRef.current}${
        all ? "&all=1" : ""
      }`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`events page ${response.status}`);
      const body = (await response.json()) as { events: TickerEvent[]; done: boolean };

      setRows((current) => {
        // Ids, not indices. A concurrent write between two pages shifts
        // every offset by one and would otherwise duplicate a row, and a
        // duplicate React key is a silent rendering fault.
        const known = new Set(current.map((e) => e.id));
        const fresh = body.events.filter((e) => !known.has(e.id));
        const merged = [...current, ...fresh];
        countRef.current = merged.length;
        return merged;
      });
      // The server's answer, not a length comparison the client has to keep
      // in step with the page size.
      if (body.done) setDone(true);
      setFailed(null);
    } catch (error) {
      setFailed(error instanceof Error ? error.message : String(error));
      // Stop rather than retry on every scroll frame. The button below is
      // how a reader asks again.
      setDone(true);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [sym, all, done]);

  // A new ticker or a new toggle is a new server render with new props.
  useEffect(() => {
    setRows(initial);
    countRef.current = initial.length;
    setDone(initial.length >= total);
    setFailed(null);
  }, [initial, total]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node || done) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadMore();
      },
      // Fire before the row is actually on screen, so the next page is
      // usually there by the time the reader reaches it.
      { rootMargin: "400px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [loadMore, done]);

  return (
    <>
      <table className="screen">
        <thead>
          <tr>
            <th className="rail" />
            <th>Date</th>
            <th>Signal</th>
            <th>Side</th>
            <th className="r">Entry</th>
            <th className="r">Exit</th>
            <th>Reason</th>
            <th className="r">Hold</th>
            <th className="r">Net</th>
            <th className="r">MFE</th>
            <th className="r">MAE</th>
            <th>Bucket</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="rail">
                <span data-side={e.side} />
              </td>
              <td className="num" data-label="Date">
                {e.signalDate}
              </td>
              <td className="sig" data-label="Signal">
                {typeList(e)
                  .filter((t) => t !== "bear_close_above_upper")
                  .map((t, i) => (
                    <span key={t}>
                      {i > 0 && <span className="sep-dot"> · </span>}
                      <span className={i === 0 ? undefined : "dim"}>{SIGNAL_LABELS[t] ?? t}</span>
                    </span>
                  ))}
                {/* NULL means the poller wrote it and clustering has not
                    run yet (ADR 054, ADR 119), which is not the same as
                    "not a head". */}
                {/* ADR 111's confirming reversal, badged rather than left
                    as a word in the list — same treatment as the screener,
                    because it is the one label that changes what a reader
                    does rather than describing what fired. */}
                {e.signalTypesAll.includes("bear_close_above_upper") && (
                  <span className="reversal" title="closed above the band and below its open">
                    ↓ reversal
                  </span>
                )}
                {/* NULL means the poller wrote it and clustering has not
                    run yet (ADR 054, ADR 119); `false` means it ran and
                    this is a repeat within a cluster. Different facts, so
                    different labels — collapsing them would make an
                    un-clustered row look like a duplicate. */}
                {e.isClusterHead === null && <span className="flag">pending cluster</span>}
                {e.isClusterHead === false && <span className="flag dim">repeat</span>}
                {e.earningsInWindow && <span className="flag">earnings</span>}
              </td>
              {/* After the signal, not before it (user's request,
                  2026-08-19). The signal type is what identifies the row;
                  the side follows from it and reads as its consequence.

                  `long`/`short` is the position direction and nothing more.
                  It says which way the signal points, not what instrument
                  expresses it. */}
              <td className={`side ${e.side}`} data-label="Side">
                {e.side}
              </td>
              <td className="num r" data-label="Entry">
                {fmt(e.entryPrice)}
              </td>
              <td className="num r" data-label="Exit">
                {fmt(e.exitPrice)}
              </td>
              <td data-label="Reason">{e.exitReason ?? <span className="dim">{NONE}</span>}</td>
              <td className="num r" data-label="Hold">
                {e.holdingDays === null ? NONE : `${e.holdingDays}d`}
              </td>
              <td className="num r" data-label="Net">
                <Outcome e={e} />
              </td>
              <td className="num r" data-label="MFE">
                {signedPct(e.mfe)}
              </td>
              <td className="num r" data-label="MAE">
                {signedPct(e.mae)}
              </td>
              <td className="dim" data-label="Bucket">
                {e.ddBucket ?? NONE}
              </td>
            </tr>
          ))}
          {/* Inside the table so it scrolls with the rows. Zero height, so
              it adds nothing to the layout. */}
          <tr ref={sentinel} className="sentinel" aria-hidden="true">
            <td colSpan={12} />
          </tr>
        </tbody>
      </table>

      <p className="rows-foot dim">
        <span className="num">{rows.length}</span> of <span className="num">{total}</span>
        {loading && <span className="loading"> · loading</span>}
        {failed && (
          <>
            <span className="failed" role="alert">
              {" "}
              · could not load more ({failed})
            </span>{" "}
            <button
              type="button"
              className="retry"
              onClick={() => {
                setDone(false);
                setFailed(null);
              }}
            >
              retry
            </button>
          </>
        )}
      </p>
    </>
  );
}
