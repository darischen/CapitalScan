"use client";

import { useCallback, useRef, useState } from "react";

import { chartUrl } from "@/lib/stockcharts";

/**
 * Select rows on the screener, open each one's StockCharts chart.
 *
 * **Why this exists.** The indicators this project fires on are not any
 * charting site's defaults, so reading a signal against a third-party chart
 * used to mean re-entering `20,2.0` and `14,3,3` by hand, per ticker, every
 * time. `lib/stockcharts.ts` folds those settings into the URL, and this
 * turns "one chart" into "the six that fired today".
 *
 * **Two steps, not one** (user's request, 2026-08-20). The first click arms
 * selection and the checkboxes appear; the second opens what is ticked. On
 * the common visit nobody is selecting anything, so the default state is one
 * button and a table with no checkbox column -- the control costs nothing
 * until it is wanted.
 *
 * **A client island around a server-rendered table.** The table stays a
 * server component -- it is the whole screener, and pushing it into the
 * bundle to gain a checkbox would be a bad trade. It always renders the
 * checkboxes; this component reveals them by putting a class on the form, so
 * arming is a CSS state change rather than a re-render of every row.
 */

/** The row checkboxes the server table rendered, in document order. */
function boxes(form: HTMLFormElement): HTMLInputElement[] {
  return Array.from(form.querySelectorAll<HTMLInputElement>('input[name="ticker"]'));
}

export default function OpenSelected({ children }: { children: React.ReactNode }) {
  const formRef = useRef<HTMLFormElement>(null);
  /** Whether the checkboxes are showing. False on every page load. */
  const [armed, setArmed] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  /** Tickers the browser refused to open. Empty in the ordinary case. */
  const [blocked, setBlocked] = useState<string[]>([]);

  const sync = useCallback(() => {
    const form = formRef.current;
    if (form === null) return;
    // Deduplicated: one ticker can hold several rows on one date, because
    // `?all=1` lists every signal type rather than just the confluences.
    // Ticking both would open the same chart twice and burn one of the
    // browser's per-gesture pop-ups doing it.
    const checked = boxes(form)
      .filter((b) => b.checked)
      .map((b) => b.value);
    setSelected([...new Set(checked)]);
    // Any previous block notice describes a selection that no longer
    // exists. Leaving it up would attach a stale list of links to a new set
    // of rows.
    setBlocked([]);
  }, []);

  const setAll = useCallback(
    (checked: boolean) => {
      const form = formRef.current;
      if (form === null) return;
      for (const box of boxes(form)) box.checked = checked;
      sync();
    },
    [sync],
  );

  /** Show the checkboxes and put the keyboard on the first one. */
  const arm = useCallback(() => {
    setArmed(true);
    // After paint, or the input is still `display: none` and cannot take
    // focus. A reader who arrived by keyboard would otherwise be left on a
    // button whose label just changed under them.
    requestAnimationFrame(() => {
      const form = formRef.current;
      if (form !== null) boxes(form)[0]?.focus();
    });
  }, []);

  const disarm = useCallback(() => {
    setAll(false);
    setArmed(false);
  }, [setAll]);

  const open = useCallback(() => {
    const refused: string[] = [];
    for (const ticker of selected) {
      // **`noopener` is deliberately not passed as a window feature.**
      // Chrome returns `null` from `window.open` whenever it is, which is
      // indistinguishable from the popup blocker refusing -- and detecting
      // the refusal is the entire point of reading this value. Severing
      // `opener` on the handle afterwards gives the same protection against
      // reverse tabnabbing while leaving the return value meaningful.
      const opened = window.open(chartUrl(ticker), "_blank");
      if (opened === null) refused.push(ticker);
      else opened.opener = null;
    }
    setBlocked(refused);
  }, [selected]);

  const count = selected.length;

  return (
    <form
      ref={formRef}
      // The class is the whole mechanism: `globals.css` hides `td.pick`
      // until it is present, so every row's checkbox appears without React
      // touching the table it did not render.
      className={armed ? "picker-form armed" : "picker-form"}
      onChange={sync}
      // Nothing here submits. Without this, Enter inside the form navigates
      // and the reader loses their place on the screener.
      onSubmit={(event) => event.preventDefault()}
    >
      {children}

      <div className="picker">
        {armed && (
          <>
            <span className="num picker-count" aria-live="polite">
              {count === 0 ? "none selected" : `${count} selected`}
            </span>
            <button type="button" className="picker-link" onClick={() => setAll(true)}>
              select all
            </button>
            <button
              type="button"
              className="picker-link"
              onClick={() => setAll(false)}
              disabled={count === 0}
            >
              clear
            </button>
            {/* The way back out. Without it, arming is one-way until a
                reload, and the checkbox column stays in a table the reader
                has stopped selecting from. */}
            <button type="button" className="picker-link" onClick={disarm}>
              cancel
            </button>
          </>
        )}
        <button
          type="button"
          className="picker-open"
          onClick={armed ? open : arm}
          // Only ever disabled in the armed state. Disabling the first click
          // would leave the reader with a dead control and no way to
          // discover what it wanted from them.
          disabled={armed && count === 0}
        >
          {/* Three labels, not two. The count is interpolated only when
              there is one -- `{count > 0 && ...}` inside the phrase renders
              a doubled space at zero, which is visible in a 12px button. */}
          {!armed && "Open in StockCharts"}
          {armed && count === 0 && "Open on StockCharts"}
          {armed && count > 0 && (
            <>
              Open <span className="num">{count}</span> on StockCharts
            </>
          )}
        </button>
      </div>

      {blocked.length > 0 && (
        /* **Browsers allow one `window.open` per gesture and silently drop
           the rest.** Opening six tabs opens one, which reads as a broken
           button rather than as a browser policy. The ones that were
           refused become ordinary links instead: a direct click is its own
           gesture, so these always work. */
        <div className="picker-blocked" role="status">
          <p>
            Your browser blocked <span className="num">{blocked.length}</span> of these tabs.
            Allow pop-ups for this site to open them together, or follow them one at a time:
          </p>
          <p className="picker-links">
            {blocked.map((ticker) => (
              <a
                key={ticker}
                href={chartUrl(ticker)}
                target="_blank"
                rel="noopener noreferrer"
                className="num"
              >
                {ticker}
              </a>
            ))}
          </p>
        </div>
      )}
    </form>
  );
}
