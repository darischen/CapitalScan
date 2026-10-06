"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * A scroll container for a table too wide for a phone (DESIGN §11.9).
 *
 * The research tables carry meaning by column position, so they scroll
 * sideways inside their own box rather than stacking into cards. A box that
 * scrolls has to be reachable without a pointer, so it becomes a labelled,
 * focusable region (arrow keys then scroll it).
 *
 * **Focusable only while it actually overflows.** On a desktop the tables fit,
 * and a tab stop on a box with nothing to scroll would change how the page
 * is tabbed through. The first render, on the server and on hydration, has
 * neither `tabindex` nor `role`, so the desktop markup is the markup that
 * existed before this file; the effect adds them only on a measured overflow.
 */
/**
 * Whether the box has something to scroll. Wider content alone is not enough:
 * above 900px `.table-scroll` is `overflow: visible`, so a table can be wider
 * than its column with nothing to scroll, and a tab stop there would be empty.
 * The 1px slack absorbs sub-pixel rounding.
 */
export function overflows(scrollWidth: number, clientWidth: number, overflowX: string): boolean {
  return overflowX !== "visible" && scrollWidth > clientWidth + 1;
}

export default function ScrollRegion({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setScrolls(overflows(el.scrollWidth, el.clientWidth, getComputedStyle(el).overflowX));
    measure();
    // The box changes size on rotation, the table when fonts load.
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={box}
      className="table-scroll"
      {...(scrolls ? { role: "region", "aria-label": label, tabIndex: 0 } : {})}
    >
      {children}
    </div>
  );
}
