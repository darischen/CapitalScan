"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The screener's date, as a button that opens a month calendar.
 *
 * **It shows counts, not just clickable days.** A calendar that only knew
 * which dates existed would still make you click one to find out whether it
 * was worth it. With counts it becomes a density map of the month — "when
 * did things fire" — which is the question someone opening it actually has.
 *
 * Days with no rows are rendered and disabled rather than blank, so the
 * month keeps its shape and an empty week reads as an empty week rather
 * than as a rendering fault.
 */

interface DayCount {
  date: string;
  n: number;
}

const DOW = ["S", "M", "T", "W", "T", "F", "S"];

/** `YYYY-MM-DD` for a UTC-constructed date.
 *
 * Every date in this component is built with `Date.UTC` and read with the
 * `getUTC*` getters. Local getters would shift the whole grid by a day for
 * anyone west of Greenwich — the same bug that put every chart bar one
 * session early earlier in this project. */
function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function monthStart(date: string): Date {
  const [y, m] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1));
}

function addMonths(d: Date, n: number): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1));
}

const MONTH_NAME = (d: Date) =>
  d.toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

export default function DatePicker({
  date,
  confluenceOnly,
  href,
}: {
  /** The date currently on screen. */
  date: string;
  /** Passed to the API so the calendar's counts match the table's filters —
   * a day it calls populated must be populated under what the reader is
   * actually looking at. */
  confluenceOnly: boolean;
  /** Builds the URL for a chosen date, keeping the other toggles. */
  href: (date: string) => string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => monthStart(date));
  const [days, setDays] = useState<Map<string, number>>(new Map());
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    (async () => {
      try {
        const url = `/api/dates?month=${iso(month)}${confluenceOnly ? "" : "&all=1"}`;
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) return;
        const { days: rows } = (await response.json()) as { days: DayCount[] };
        setDays(new Map(rows.map((r) => [r.date, r.n])));
      } catch {
        // Aborted on close or on a month change. A failed load leaves the
        // previous month's counts up, which is wrong but visibly so — every
        // day disabled — rather than an error state over a calendar.
      } finally {
        setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [open, month, confluenceOnly]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // A fixed six-row grid. Sizing it to the month makes the popover change
  // height as you page through, which moves the arrows out from under the
  // cursor you are clicking them with.
  const first = month;
  const lead = first.getUTCDay();
  const cells: (Date | null)[] = Array.from({ length: 42 }, (_, i) => {
    const day = i - lead + 1;
    const d = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), day));
    return d.getUTCMonth() === month.getUTCMonth() ? d : null;
  });

  return (
    <span className="picker" ref={box}>
      <button
        type="button"
        className="picker-btn num"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setMonth(monthStart(date));
          setOpen((v) => !v);
        }}
        title="pick a date"
      >
        signals {date}
      </button>

      {open && (
        <div className="cal" role="dialog" aria-label="Pick a date">
          <div className="cal-head">
            <button type="button" onClick={() => setMonth(addMonths(month, -1))} aria-label="Previous month">
              ←
            </button>
            <strong>{MONTH_NAME(month)}</strong>
            <button type="button" onClick={() => setMonth(addMonths(month, 1))} aria-label="Next month">
              →
            </button>
          </div>

          <div className="cal-grid">
            {DOW.map((d, i) => (
              <span className="cal-dow" key={i}>
                {d}
              </span>
            ))}
            {cells.map((d, i) => {
              if (!d) return <span className="cal-pad" key={i} />;
              const key = iso(d);
              const n = days.get(key) ?? 0;
              const cls = [
                "cal-day",
                n > 0 ? "has" : "none",
                key === date ? "on" : "",
              ]
                .filter(Boolean)
                .join(" ");

              if (n === 0) {
                return (
                  <span className={cls} key={i} aria-disabled="true">
                    {d.getUTCDate()}
                  </span>
                );
              }
              return (
                <a className={cls} key={i} href={href(key)} title={`${n} row${n === 1 ? "" : "s"}`}>
                  {d.getUTCDate()}
                  {/* The count as a bar rather than a number: at this size a
                      two-digit figure competes with the date it belongs to,
                      and the shape of the month is what the reader is
                      scanning for. Capped so one busy day does not flatten
                      the rest. */}
                  <i style={{ width: `${Math.min(100, n * 8)}%` }} />
                </a>
              );
            })}
          </div>

          <div className="cal-foot dim">
            {loading ? "loading…" : `${confluenceOnly ? "confluence" : "all signals"} · days with rows are clickable`}
          </div>
        </div>
      )}
    </span>
  );
}
