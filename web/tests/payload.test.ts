import { describe, expect, it } from "vitest";

import { PAYLOAD_PROB_DIGITS, compactPrediction } from "@/lib/format";
import type { Prediction } from "@/lib/screen";

/**
 * The screener serializes each row's prediction into the page. At float8's
 * 17 digits the probabilities were most of a 3,045-byte row (Pi,
 * 2026-11-17), where the dialog prints a tenth of a percent.
 */
const BAND = { p: 0.43850726413625835, lo: 0.39863317712772195, hi: 0.4792043856988065, nEff: 578 };
const PRED: Prediction = {
  pTouch3: 0.573302047769766,
  ciLow: 0.532178711561755,
  ciHigh: 0.613424931627999,
  nEff: 559,
  modelVersion: "adr175-f183b0f5-7678431-20261115T0042Z",
  bands: { p_touch_3: BAND, p_adverse_3: BAND },
  adverse3: BAND,
  cosmetic: false,
};

describe("compactPrediction", () => {
  const out = compactPrediction(PRED);

  it("rounds every probability to the payload precision", () => {
    expect(PAYLOAD_PROB_DIGITS).toBe(6);
    expect(out.pTouch3).toBe(0.573302);
    expect(out.ciLow).toBe(0.532179);
    expect(out.ciHigh).toBe(0.613425);
    expect(out.bands.p_touch_3).toEqual({ p: 0.438507, lo: 0.398633, hi: 0.479204, nEff: 578 });
    expect(out.adverse3).toEqual({ p: 0.438507, lo: 0.398633, hi: 0.479204, nEff: 578 });
  });

  it("keeps n_eff and every interval (invariant 8)", () => {
    expect(out.nEff).toBe(559);
    for (const band of Object.values(out.bands)) {
      expect(band.nEff).toBe(578);
      expect(band.lo).toBeLessThanOrEqual(band.p);
      expect(band.hi).toBeGreaterThanOrEqual(band.p);
    }
  });

  it("cannot move a digit the dialog prints", () => {
    const shown = (x: number) => (x * 100).toFixed(1);
    expect(shown(out.pTouch3!)).toBe(shown(PRED.pTouch3!));
    expect(shown(out.bands.p_touch_3.lo)).toBe(shown(BAND.lo));
  });

  it("drops modelVersion, which no surface renders, and leaves the input alone", () => {
    expect(out.modelVersion).toBeNull();
    expect(PRED.modelVersion).not.toBeNull();
    expect(PRED.pTouch3).toBe(0.573302047769766);
  });

  it("passes a missing adverse block and null probabilities through", () => {
    const bare = compactPrediction({ ...PRED, adverse3: null, pTouch3: null, ciLow: null, ciHigh: null });
    expect(bare.adverse3).toBeNull();
    expect(bare.pTouch3).toBeNull();
  });

  it("makes the serialized prediction much smaller", () => {
    const before = JSON.stringify(PRED).length;
    const after = JSON.stringify(out).length;
    expect(after).toBeLessThan(before * 0.7);
  });
});
