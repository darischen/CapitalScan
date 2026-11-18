import { Fragment, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { InferenceCell } from "@/components/InferenceCell";
import { ScreenCell } from "@/components/ScreenCell";
import { ScreenerTable, type SortContext } from "@/components/Screener";
import type { ScreenRow } from "@/lib/screen";

/**
 * What one screener row costs in the server-components payload.
 *
 * `ScreenerTable` is a server component, so every host element it renders
 * reaches the browser twice: once as HTML and once as a Flight row in the
 * payload, every prop spelled out. `flight` below walks the element tree the
 * way React's Flight server does for this table: host elements become
 * `["$", type, key, props]`, a client component becomes a reference plus its
 * props, and a server component is called and replaced by what it returns.
 * It is a model of the encoding, not the encoder, so the bounds below carry
 * headroom; what they pin is the shape.
 */

const CLIENT = new Set<unknown>([InferenceCell, ScreenCell]);

function flight(node: ReactNode): unknown {
  if (node === null || node === undefined || typeof node === "boolean") return null;
  if (Array.isArray(node)) return node.map(flight);
  if (!isValidElement(node)) return node;
  const el = node as ReactElement<Record<string, unknown>>;
  const props = (p: Record<string, unknown>) =>
    Object.fromEntries(
      Object.entries(p).map(([k, v]) => [k, k === "children" ? flight(v as ReactNode) : v]),
    );
  if (el.type === Fragment) return flight(el.props.children as ReactNode);
  if (typeof el.type === "string") return ["$", el.type, el.key, props(el.props)];
  if (CLIENT.has(el.type)) return ["$", "$L1", el.key, props(el.props)];
  const render = el.type as (p: Record<string, unknown>) => ReactNode;
  return flight(render(el.props));
}

const BAND = { p: 0.438507, lo: 0.398633, hi: 0.479204, nEff: 578 };

function row(i: number): ScreenRow {
  return {
    ticker: `T${String(i).padStart(3, "0")}`,
    signalDate: "2026-11-17",
    signalType: "confluence_low",
    signalTypesAll: ["confluence_low", "bb_lower_touch", "stoch_oversold"],
    signalStrength: 3,
    side: "long",
    bbLower: 385.63,
    bbMid: 413.01,
    bbUpper: 440.4,
    bandTs: "2026-11-14",
    kFast: 12.1,
    kSlow: 14.4,
    open: 410,
    high: 412,
    low: 384,
    close: 386,
    volume: 14_000_000,
    liveOpen: null,
    liveHigh: null,
    liveLow: null,
    livePrice: 386.2,
    livePriceTs: "2026-11-17T20:59:00.000Z",
    firedAt: "2026-11-17T15:35:12.000Z",
    cellId: "cell-1",
    isClusterHead: null,
    inWatch: false,
    reversal: null,
    bullReversal: null,
    stats: null,
    prediction: {
      pTouch3: 0.573302,
      ciLow: 0.532179,
      ciHigh: 0.613425,
      nEff: 559,
      modelVersion: null,
      bands: { p_touch_3: BAND, p_adverse_3: BAND },
      adverse3: BAND,
      cosmetic: false,
    },
  } as unknown as ScreenRow;
}

const CTX: SortContext = { sort: null, dir: "desc", date: null, withStats: false, confluenceOnly: true };

const table = (n: number, withStats = false) => (
  <ScreenerTable
    rows={Array.from({ length: n }, (_, i) => row(i))}
    withStats={withStats}
    sortCtx={{ ...CTX, withStats }}
  />
);

/** The first body row's payload. Body rows are the only keyed `<tr>`s. */
function perRow(): string {
  const find = (node: unknown): unknown => {
    if (!Array.isArray(node)) return undefined;
    if (node[0] === "$" && node[1] === "tr" && node[2] !== null) return node;
    for (const child of node[0] === "$" ? [(node[3] as { children?: unknown }).children] : node) {
      const hit = find(child);
      if (hit) return hit;
    }
    return undefined;
  };
  const tr = find(flight(table(1)));
  if (!tr) throw new Error("no body row in the payload");
  return JSON.stringify(tr);
}

describe("screener row payload", () => {
  it("is under the 2026-11-18 size, 2,084 B in this model against 2,400 B before", () => {
    // The 316 B came out of the cells' constant `data-label` and `className`
    // (`components/ScreenCell.tsx`). The bound leaves room for a field, not
    // for those strings to come back: they alone would exceed it.
    expect(perRow().length).toBeLessThanOrEqual(2_150);
  });

  it("carries no column label or column class per row", () => {
    const one = perRow();
    expect(one).not.toContain("data-label");
    expect(one).not.toContain('"r num');
    for (const label of ["Bollinger", "Stochastic", "Inference"]) {
      expect(one).not.toContain(`"${label}"`);
    }
  });

  it("still carries the dialog's n_eff and interval (invariant 8)", () => {
    const one = perRow();
    expect(one).toContain('"nEff":559');
    expect(one).toContain('"ciLow":0.532179');
    expect(one).toContain('"ciHigh":0.613425');
  });
});

describe("the row markup the payload cut must not move", () => {
  const html = renderToStaticMarkup(table(1, true));
  const cells = [...html.matchAll(/<td\b[^>]*>/g)].map((m) => m[0]);

  it("renders every labelled cell exactly as the inline <td>s did", () => {
    expect(cells.filter((c) => c.includes("data-label"))).toEqual([
      '<td class="ticker" data-label="Ticker">',
      '<td class="sig" data-label="Signal">',
      '<td class="r num" data-label="Bollinger" title="bands at 2026-11-14">',
      '<td class="r num" data-label="Stochastic">',
      '<td class="r num" data-label="Open">',
      '<td class="r num dim" data-label="High">',
      '<td class="r num dim" data-label="Low">',
      '<td class="r num down" data-label="Close">',
      '<td class="r num" data-label="Vol">',
      expect.stringMatching(/^<td class="r num " data-label="Live" title="as of [^"]+">$/),
      '<td class="r num" data-label="Fired">',
      '<td data-label="Inference" class="c">',
      '<td data-label="Cell">',
    ]);
  });

  it("keeps the trailing space an untoned Close and Live always had", () => {
    const flat = renderToStaticMarkup(
      <ScreenerTable rows={[{ ...row(0), close: 410, livePrice: null, livePriceTs: null }]} withStats={false} sortCtx={CTX} />,
    );
    expect(flat).toContain('<td class="r num " data-label="Close">');
    expect(flat).toContain('<td class="r num " data-label="Live">');
  });
});
