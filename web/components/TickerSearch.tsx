"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { mcap } from "@/lib/format";
import type { TickerHit } from "@/lib/ticker";

/**
 * Type-ahead in the ticker page's header, and in the home page's status
 * strip (2026-08-27).
 *
 * Rendered in two places and owned by neither: it takes no props and routes
 * on its own, so a second mount is a second instance, not shared state.
 *
 * **Every name, tradeable or not.** The menu tags the ones outside the
 * trade universe rather than hiding them: those are the tickers whose page
 * is most worth reaching, since they never appear on the screener.
 *
 * A combobox rather than a plain input with a list under it — the pattern
 * has a keyboard contract people already know, and getting `aria-expanded`,
 * `aria-activedescendant` and arrow keys right is the difference between a
 * search box and a mouse-only decoration.
 */

/** Enough that a fast typist does not fire a request per keystroke, short
 * enough that the menu feels attached to the keyboard. */
const DEBOUNCE_MS = 130;

export default function TickerSearch() {
  const router = useRouter();
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<TickerHit[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const q = term.trim();
    if (q === "") {
      setHits([]);
      return;
    }

    // Both an abort and a timer. The timer stops the request being made;
    // the abort stops a slow earlier one from resolving after a fast later
    // one and overwriting the menu with results for a shorter prefix.
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(
          `/api/tickers?q=${encodeURIComponent(q)}`,
          {
            signal: controller.signal,
          },
        );
        if (!response.ok) return;
        const { tickers } = (await response.json()) as { tickers: TickerHit[] };
        setHits(tickers);
        setActive(0);
      } catch {
        // An aborted fetch is the normal path on every keystroke, and a
        // failed one leaves the previous menu up. Neither is worth an error
        // state on a search box.
      }
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [term]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const go = (sym: string) => {
    setOpen(false);
    setTerm("");
    router.push(`/ticker/${sym}`);
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // The highlighted row, or — when nothing matched — whatever was
      // typed. `/ticker/ZZZZ` renders the not-found state, which is a
      // better answer than a key that does nothing.
      const pick = hits[active]?.ticker ?? term.trim().toUpperCase();
      if (pick) go(pick);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const show = open && hits.length > 0;

  return (
    <div className="search" ref={box}>
      <input
        type="text"
        value={term}
        placeholder="search ticker"
        aria-label="Search ticker"
        autoComplete="off"
        spellCheck={false}
        role="combobox"
        aria-expanded={show}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={show ? `${listId}-${active}` : undefined}
        onChange={(e) => {
          setTerm(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
      />

      {show && (
        <ul className="search-menu" id={listId} role="listbox">
          {hits.map((h, i) => (
            <li
              key={h.ticker}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "on" : undefined}
              // `onMouseDown`, not `onClick`: the outside-click handler
              // closes the menu on mousedown, which would unmount the row
              // before a click ever landed on it.
              onMouseDown={(e) => {
                e.preventDefault();
                go(h.ticker);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="sym num">{h.ticker}</span>
              <span className="co">{h.name ?? "—"}</span>
              {/* Outside the trade universe is the tag that matters here:
                  it is why a name has no screener rows and a short event
                  history, and this is where someone finds it. */}
              {h.inTrade === false && <span className="out">not traded</span>}
              <span className="cap num">{mcap(h.mcapUsd)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
