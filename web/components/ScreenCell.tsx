"use client";

import type { ReactNode } from "react";

/**
 * One screener `<td>`, with the column's constant attributes held here
 * rather than written into every row.
 *
 * **Why a client component renders a plain cell.** `ScreenerTable` is a
 * server component, so every attribute it writes reaches the browser twice:
 * once as HTML and once in the server-components payload, spelled out per
 * row. The `data-label` the phone layout prints (`globals.css`,
 * `td[data-label]::before`) and the column's class were ~316 B of every row
 * that never vary (2026-11-18). A client component's own constants ship once
 * in its bundle, so the payload carries only `col` and the per-row values.
 *
 * The HTML is byte-for-byte what the inline `<td>`s rendered, attribute
 * order included; `tests/row-payload.test.tsx` checks both that and the size.
 *
 * Imports nothing but a type: `boundary.test.ts` forbids a client component
 * importing a function from anything that reaches `./db`.
 */
const COLUMNS = {
  ticker: { label: "Ticker", className: "ticker" },
  sig: { label: "Signal", className: "sig" },
  bb: { label: "Bollinger", className: "r num" },
  stoch: { label: "Stochastic", className: "r num" },
  open: { label: "Open", className: "r num" },
  high: { label: "High", className: "r num dim" },
  low: { label: "Low", className: "r num dim" },
  close: { label: "Close", className: "r num" },
  vol: { label: "Vol", className: "r num" },
  live: { label: "Live", className: "r num" },
  fired: { label: "Fired", className: "r num" },
  infer: { label: "Inference", className: "c" },
  cell: { label: "Cell", className: undefined },
} as const;

export type ScreenColumn = keyof typeof COLUMNS;

export function ScreenCell({
  col,
  tone,
  title,
  children,
}: {
  col: ScreenColumn;
  /** The cool/warm class on Close and Live (`up`, `down` or empty). Appended
   * after a space even when empty, as the inline template did. */
  tone?: string;
  title?: string;
  children?: ReactNode;
}) {
  const { label, className } = COLUMNS[col];
  const cls = tone === undefined ? className : `${className} ${tone}`;
  // Inference wrote `data-label` before `className`; kept so the markup does
  // not move by even an attribute.
  if (col === "infer") {
    return (
      <td data-label={label} className={cls} title={title}>
        {children}
      </td>
    );
  }
  return (
    <td className={cls} data-label={label} title={title}>
      {children}
    </td>
  );
}

export default ScreenCell;
