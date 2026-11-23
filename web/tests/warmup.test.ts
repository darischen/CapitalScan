import { describe, expect, it } from "vitest";

import { INDICATOR_WARMUP_SESSIONS, warmup } from "@/lib/warmup";

describe("warmup", () => {
  it("counts ANT's two sessions on 2026-11-20", () => {
    expect(warmup(2)).toEqual({
      held: 2,
      remaining: INDICATOR_WARMUP_SESSIONS - 2,
      fraction: 2 / INDICATOR_WARMUP_SESSIONS,
    });
  });

  it("caps at the target rather than overflowing the meter", () => {
    expect(warmup(500)).toEqual({ held: 280, remaining: 0, fraction: 1 });
  });

  it("reads nonsense as zero, never as negative progress", () => {
    for (const bad of [0, -3, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(warmup(bad).held).toBe(0);
    }
  });
});
