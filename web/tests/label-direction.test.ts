/**
 * A probability label must never imply the wrong direction.
 *
 * **This file exists because it did.** `p_touch_*` is side-adjusted at the
 * source — `research/peak_labels.py` builds `path.favorable` as
 * `(high - entry)/entry` for a long and `(entry - low)/entry` for a short —
 * so on a short signal it is the probability of a *fall*. The label said
 * "Reaches +3%" until 2026-09-09.
 *
 * A reader looking at VOD (`bear_close_above_upper`, side short, 47.8%) read
 * that as the model expecting a 3% rise. It meant a 3% fall. On a short
 * setup that is the worst available direction to be wrong in, and it is
 * exactly the reading the `+` invites.
 *
 * The tooltip had said "in the signal's own direction" correctly the whole
 * time, which is the part worth remembering: the accurate wording was one
 * hover away from a label that contradicted it, and the hover is the half
 * nobody reads.
 */

import { describe, expect, it } from "vitest";

import { MODEL_FIELD_LABELS, modelFieldLabel } from "@/lib/format";

const TOUCH = ["p_touch_2", "p_touch_3", "p_touch_5", "p_touch_10"];
const ADVERSE = ["p_adverse_3", "p_adverse_5"];

describe("a label never implies a direction the number does not have", () => {
  it("names a fall for a short-side favourable move", () => {
    expect(modelFieldLabel("p_touch_3", "short")).toBe("Falls 3% in 5 days");
  });

  it("names a rise for a long-side favourable move", () => {
    expect(modelFieldLabel("p_touch_3", "long")).toBe("Rises 3% in 5 days");
  });

  it("flips the adverse direction too", () => {
    // Adverse is the other way by definition, so a short's adverse is a rise.
    expect(modelFieldLabel("p_adverse_3", "short")).toBe(
      "Rises 3% against you in 5 days",
    );
    expect(modelFieldLabel("p_adverse_5", "long")).toBe(
      "Falls 5% against you in 5 days",
    );
  });

  /**
   * The specific regression. A `+` is a direction claim, and it is wrong on
   * every short row.
   */
  it("no touch label carries a bare + anywhere", () => {
    for (const f of TOUCH) {
      expect(MODEL_FIELD_LABELS[f]).not.toContain("+");
      expect(modelFieldLabel(f, "short")).not.toContain("+");
      expect(modelFieldLabel(f, "long")).not.toContain("+");
    }
  });

  it("no short-side label says the opposite of what it means", () => {
    for (const f of TOUCH) {
      // A short's favourable move is down; the label must not say "Rises".
      expect(modelFieldLabel(f, "short")).not.toContain("Rises");
    }
    for (const f of ADVERSE) {
      // A short's adverse move is up; the label must not say "Falls".
      expect(modelFieldLabel(f, "short")).not.toContain("Falls");
    }
  });

  /**
   * **The fallback must be neutral, not a guess.** A caller that does not
   * know the side is better served by wording that requires a translation
   * than by one that might assert the wrong direction — a missing
   * direction is recoverable, a wrong one is not.
   */
  it("says neither rise nor fall when the side is unknown", () => {
    for (const side of [undefined, null, "", "unknown"]) {
      for (const f of [...TOUCH, ...ADVERSE]) {
        const l = modelFieldLabel(f, side as string | null | undefined);
        expect(l).not.toContain("Rises");
        expect(l).not.toContain("Falls");
        expect(l).not.toContain("+");
      }
    }
  });

  it("keeps the threshold and the window in every label", () => {
    expect(modelFieldLabel("p_touch_10", "short")).toBe("Falls 10% in 10 days");
    expect(modelFieldLabel("p_touch_5", "long")).toBe("Rises 5% in 5 days");
  });

  it("passes non-probability fields through untouched", () => {
    expect(modelFieldLabel("model_version", "short")).toBe("Model version");
    expect(modelFieldLabel("q50", "long")).toBe("Midpoint (50th pct)");
  });
});
