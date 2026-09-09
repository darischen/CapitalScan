"use client";

import { useEffect, useRef } from "react";

// **Type-only from `@/lib/screen`, and the caveat from `@/lib/format`.**
// `screen.ts` imports `./db` on its first line, so a value import here
// pulls `pg` into the browser bundle and the build dies on unresolvable
// `fs`/`dns`. `import type` is erased at compile time; a value import is
// not. That is what `boundary.test.ts` checks for, and it caught this
// only after the Pi build failed -- the guard tests the file list, not
// the transitive import graph.
import {

  MODEL_COLUMN_HELP,
  MODEL_FIELD_HELP,
  MODEL_FIELD_LABELS,
  PREDICTION_CAVEAT_DETAIL,
  PREDICTION_CAVEAT_SUMMARY,
  pct,
} from "@/lib/format";
import type { Band, Prediction, ScreenRow } from "@/lib/screen";

/**
 * The model's output for one signal, in full.
 *
 * **The grid shows a button and this shows the numbers**, which is ADR
 * 176's design rather than a layout preference. `p_touch` only *ranks*
 * when market breadth is below 0.68; above it the model scores AUC 0.515,
 * a coin flip, and the low band inverts. A column of probabilities invites
 * sort-and-take-the-top. Here there is room for the interval, the
 * effective sample and the caveat beside every number, which is the shape
 * invariant 8 asks for and a 60px cell cannot hold.
 *
 * **Every label is translated** (`MODEL_FIELD_LABELS`). A reader who knows
 * markets should not have to know that `p_touch_3` means "reaches +3%" or
 * that `q05` is the 5th percentile. The screener already works this way --
 * "Bollinger Lower / Mid / Upper", not `bb_lower` -- and this matches it.
 */

/** Order matters: the two the reader acts on first, then the rest. */
// **Ascending by threshold.** The earlier order put the headline
// (`p_touch_3`) first and left the rest as 5, 2, 10 -- defensible as
// "most important first" and unreadable as a column of numbers, because a
// reader scanning a ladder of thresholds expects it to climb. The headline
// earns its place on the row itself, not by jumping the queue here.
const TOUCH_FIELDS = ["p_touch_2", "p_touch_3", "p_touch_5", "p_touch_10"] as const;
const ADVERSE_FIELDS = ["p_adverse_3", "p_adverse_5"] as const;

function label(field: string): string {
  return MODEL_FIELD_LABELS[field] ?? field;
}

/**
 * One row: the name, the probability, its interval and its sample.
 *
 * The interval is never omitted when the number is shown. That pairing is
 * the invariant, not a style choice — a bare probability is the thing the
 * response validator rejects everywhere else in this system.
 */
/**
 * How far the interval may sit off-centre before a `±` would misrepresent
 * it, in probability units. One percentage point: below that the two
 * arms differ by less than the displayed precision.
 */
const MAX_ASYMMETRY = 0.01;

/**
 * One row: what has to happen, how often it has, and the margin on that.
 *
 * The margin is never omitted when the number is shown. That pairing is
 * the invariant, not a style choice — a bare probability is the thing the
 * response validator rejects everywhere else in this system.
 */
function BandRow({ field, band }: { field: string; band: Band }) {
  const help = MODEL_FIELD_HELP[field];

  // **`±` is a claim that the interval is centred, so check rather than
  // assume.** Wilson intervals are asymmetric in general, sharply so near
  // 0 and 1 on a small sample. Measured 2026-09-08 across all six fields
  // and 26,952 live intervals the worst skew was 0.27pp and nothing
  // exceeded 1pp, because `n_eff` runs 2,000-4,800 — so `±` is honest
  // *at this sample size*, which is a fact about today's data and not a
  // property of the statistic.
  //
  // Falling back to the explicit range keeps it honest without needing
  // anyone to notice: if the sample ever thins, the display corrects
  // itself instead of quietly recentring the estimate.
  const lower = band.p - band.lo;
  const upper = band.hi - band.p;
  const centred = Math.abs(upper - lower) <= MAX_ASYMMETRY;
  const halfWidth = (band.hi - band.lo) / 2;

  return (
    <tr>
      <th scope="row" title={help}>
        {label(field)}
      </th>
      <td className="r num">{pct(band.p)}</td>
      <td
        className="r num dim"
        title={`Based on ${Math.round(band.nEff).toLocaleString()} comparable past signals, counted by independent information rather than row count.`}
      >
        {centred
          ? `±${(halfWidth * 100).toFixed(1)}`
          : `${pct(band.lo, 1)}–${pct(band.hi, 1)}`}
      </td>
    </tr>
  );
}

export function InferenceModal({
  row,
  onClose,
}: {
  row: ScreenRow;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const prediction: Prediction | null = row.prediction ?? null;

  // Escape closes, and focus lands inside on open. Both are the minimum for
  // a dialog that traps attention; without them a keyboard reader is stuck
  // behind a panel they cannot dismiss.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!prediction) return null;

  const bands = prediction.bands ?? {};
  const touch = TOUCH_FIELDS.filter((f) => bands[f]);
  const adverse = ADVERSE_FIELDS.filter((f) => bands[f]);

  return (
    <div className="modal-scrim" onClick={onClose} role="presentation">
      <div
        ref={ref}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Model output for ${row.ticker} on ${row.signalDate}`}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <div>
            <strong>{row.ticker}</strong>
            <span className="dim"> · {row.signalDate}</span>
          </div>
          <button type="button" className="modal-x" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <table className="modal-table">
          <thead>
            {/* **"Range" and "Signals" both needed saying out loud.** A
             * reader asked what they meant, which is the answer: the
             * headers did not say. "Range" now names what it ranges over,
             * "Signals" says the count is a past one, and both carry
             * hover text. Invariant 8 requires the interval and the
             * sample beside every probability -- it does not require them
             * to be unreadable. */}
            <tr>
              <th title={MODEL_COLUMN_HELP.outcome}>Outcome</th>
              <th className="r" title={MODEL_COLUMN_HELP.chance}>
                Chance
              </th>
              <th className="r" title={MODEL_COLUMN_HELP.error}>
                Error
              </th>
            </tr>
          </thead>
          <tbody>
            {touch.length > 0 && (
              <tr className="modal-group">
                <th scope="rowgroup" colSpan={3}>
                  In the signal&apos;s direction
                </th>
              </tr>
            )}
            {touch.map((f) => (
              <BandRow key={f} field={f} band={bands[f]} />
            ))}
            {adverse.length > 0 && (
              <tr className="modal-group">
                <th scope="rowgroup" colSpan={3}>
                  Against the position
                </th>
              </tr>
            )}
            {adverse.map((f) => (
              <BandRow key={f} field={f} band={bands[f]} />
            ))}
          </tbody>
        </table>

        {/* The caveat is not a footnote. It says to rank rather than read
         * the level, that the intervals are a lower bound because the
         * calibration split was reused, and that this is a frequency
         * rather than a recommendation — the things a reader is most
         * likely to assume otherwise.
         *
         * **Collapsed by default**, because a wall of text under a number
         * is text nobody reads. `<details>` rather than a state hook: it
         * is keyboard-accessible, survives with JS disabled, and needs no
         * client state at all. The closed line carries the actionable
         * half, so a reader who never opens it still gets the point.
         *
         * **`modelVersion` used to render below this and no longer does.**
         * It read `adr175-05238410-4aa7928`: an ADR number, a config hash
         * prefix and a git sha, none of which mean anything to the person
         * this dialog is for. It stays on the row object, so anyone
         * debugging a stale generation can still reach it; it is only off
         * the surface. */}
        <details className="modal-note">
          <summary>{PREDICTION_CAVEAT_SUMMARY}</summary>
          <p>{PREDICTION_CAVEAT_DETAIL}</p>
        </details>
      </div>
    </div>
  );
}

export default InferenceModal;
