"use client";

import { useState } from "react";

import type { ScreenRow } from "@/lib/screen";

import { InferenceModal } from "./InferenceModal";

/**
 * The button in the grid, and the modal it opens.
 *
 * A client island rather than a server component because it holds one
 * boolean. `ScreenerTable` stays a server component and renders dozens of
 * these; keeping the state here means the table itself never ships to the
 * browser.
 */
export function InferenceCell({ row }: { row: ScreenRow }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="infer"
        aria-label={`Model output for ${row.ticker} on ${row.signalDate}`}
        aria-haspopup="dialog"
        title="Model output: probabilities, range and sample size"
        onClick={() => setOpen(true)}
      >
        …
      </button>
      {open && <InferenceModal row={row} onClose={() => setOpen(false)} />}
    </>
  );
}

export default InferenceCell;
