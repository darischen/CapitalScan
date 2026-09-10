"use client";

import { useState } from "react";

import { InferenceModal, type InferenceSubject } from "./InferenceModal";

/**
 * "Predict movement" on the graph page, opening the same dialog the
 * screener's ellipsis does.
 *
 * **The same modal, deliberately.** A second panel showing the same six
 * probabilities would drift from this one the first time either changed,
 * and the reader would have two slightly different accounts of one number.
 *
 * A box rather than the underline the sibling toggles use, because it does
 * something instead of navigating. The toggles swap which rows the page
 * lists; this opens a dialog and leaves the page where it is, and a
 * control that behaves differently should not look identical.
 *
 * Renders nothing when the ticker has no scored prediction. That is a
 * common state, not an error: the name may have fired only signal types
 * outside the model's fitted population (ADR 180), or not fired recently
 * enough to carry one.
 */
export function PredictButton({
  ticker,
  signalDate,
  side,
  prediction,
}: InferenceSubject) {
  const [open, setOpen] = useState(false);
  if (!prediction) return null;

  return (
    <>
      <button
        type="button"
        className="predict-btn"
        aria-haspopup="dialog"
        aria-label={`Model output for ${ticker}`}
        title={`What the model says about ${ticker}, from its most recent scored signal`}
        onClick={() => setOpen(true)}
      >
        predict movement
      </button>
      {open && (
        <InferenceModal
          row={{ ticker, signalDate, side, prediction }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

export default PredictButton;
