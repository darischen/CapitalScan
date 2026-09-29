import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { InferenceModal } from "@/components/InferenceModal";
import {
  REALISED_RATE_MIN_N_EFF,
  type RecentRate,
  type RecentRow,
  summariseRecent,
} from "@/lib/reliability";
import type { Prediction } from "@/lib/screen";

/**
 * The trailing realised rate (2026-09-29): what the model stated against
 * what happened, lately, beside the number it qualifies. The published
 * level leans with the market (7 points high on 2026-09-27); this shows the
 * reader by how much, from the forward log itself.
 */

function rows(side: string, days: number, perDay: number, hitsPerDay: number, stated = 0.6): RecentRow[] {
  const out: RecentRow[] = [];
  for (let d = 0; d < days; d++) {
    const asOf = `2026-09-${String(d + 1).padStart(2, "0")}`;
    for (let i = 0; i < perDay; i++) {
      out.push({ asOf, side, stated, realised: i < hitsPerDay ? 1 : 0 });
    }
  }
  return out;
}

describe("summariseRecent", () => {
  it("reports the stated average and the realised rate for one side", () => {
    const r = summariseRecent([...rows("long", 20, 10, 5, 0.6), ...rows("short", 20, 10, 9)], "long");
    expect(r).not.toBeNull();
    expect(r!.stated).toBeCloseTo(0.6, 9);
    expect(r!.realised).toBeCloseTo(0.5, 9);
    expect(r!.n).toBe(200);
    expect(r!.sessions).toBe(20);
    expect(r!.firstDate).toBe("2026-09-01");
    expect(r!.lastDate).toBe("2026-09-20");
  });

  it("returns null for a side with no rows", () => {
    expect(summariseRecent(rows("long", 5, 5, 2), "short")).toBeNull();
  });

  it("discounts same-day clustering: all-or-nothing days count as one each", () => {
    // Every signal on a day agrees, so each day is one observation.
    const clustered: RecentRow[] = [];
    for (let d = 0; d < 20; d++) {
      for (let i = 0; i < 20; i++) {
        clustered.push({ asOf: `2026-08-${String(d + 1).padStart(2, "0")}`, side: "long", stated: 0.5, realised: d % 2 });
      }
    }
    const r = summariseRecent(clustered, "long")!;
    expect(r.n).toBe(400);
    expect(r.nEff).toBeLessThanOrEqual(25);
    expect(r.nEff).toBeGreaterThanOrEqual(20);
  });

  it("does not inflate a sample that is not clustered", () => {
    const r = summariseRecent(rows("long", 20, 10, 5), "long")!;
    expect(r.nEff).toBeLessThanOrEqual(r.n);
  });

  it("puts the realised rate inside its own interval", () => {
    const r = summariseRecent(rows("long", 20, 10, 5), "long")!;
    expect(r.ciLow).toBeLessThanOrEqual(r.realised);
    expect(r.ciHigh).toBeGreaterThanOrEqual(r.realised);
  });

  it("withholds the comparison below the minimum effective sample", () => {
    const r = summariseRecent(rows("long", 3, 4, 2), "long")!;
    expect(r.nEff).toBeLessThan(REALISED_RATE_MIN_N_EFF);
    expect(r.enough).toBe(false);
  });
});

function prediction(recent: RecentRate | null, cosmetic = false): Prediction {
  return {
    pTouch3: 0.61,
    ciLow: 0.57,
    ciHigh: 0.65,
    nEff: 800,
    modelVersion: "adr175-test",
    adverse3: null,
    bands: { p_touch_3: { p: 0.61, lo: 0.57, hi: 0.65, nEff: 800 } },
    side: "long",
    cosmetic,
    recent,
  };
}

const ENOUGH: RecentRate = {
  side: "long",
  stated: 0.614,
  realised: 0.521,
  ciLow: 0.481,
  ciHigh: 0.561,
  n: 412,
  nEff: 180,
  sessions: 20,
  firstDate: "2026-08-28",
  lastDate: "2026-09-18",
  enough: true,
};

function render(p: Prediction): string {
  return renderToStaticMarkup(
    <InferenceModal
      row={{ ticker: "TSM", signalDate: "2026-09-28", side: "long", prediction: p }}
      onClose={() => {}}
    />,
  );
}

describe("InferenceModal, recent results", () => {
  it("shows what the model said beside what happened", () => {
    const html = render(prediction(ENOUGH));
    expect(html).toContain("Model said, on average");
    expect(html).toContain("61.4%");
    expect(html).toContain("Actually happened");
    expect(html).toContain("52.1%");
    expect(html).toContain("±4.0");
    expect(html).toContain("2026-08-28 to 2026-09-18, 20 sessions");
  });

  it("carries the effective sample in the hover text (invariant 8)", () => {
    expect(render(prediction(ENOUGH))).toContain("180 effective signals");
  });

  it("says so, with no numbers, when there are too few resolved signals", () => {
    const html = render(prediction({ ...ENOUGH, enough: false, nEff: 12 }));
    expect(html).toContain("Too few resolved long signals");
    expect(html).not.toContain("Actually happened");
  });

  it("renders nothing extra when the comparison could not be computed", () => {
    const html = render(prediction(null));
    expect(html).not.toContain("Recent long signals");
  });

  it("stays off a cosmetic prediction, which is a different population", () => {
    expect(render(prediction(ENOUGH, true))).not.toContain("Recent long signals");
  });
});
