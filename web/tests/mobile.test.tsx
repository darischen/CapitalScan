import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {} }),
}));

import { MobileSort, ScreenerTable, type SortContext } from "@/components/Screener";
import { noteFor } from "@/components/TouchNotes";
import type { ScreenRow } from "@/lib/screen";

/**
 * The two phone-only paths on the screener (DESIGN §11.9).
 *
 * Under 720px the card layout hides `thead`, which takes the sortable
 * headers with it, and a touch screen never shows a `title`. Each gets a
 * second path to the same function, and the property worth pinning is
 * "same", not "exists": a mobile sort list that built its own links would
 * be a second sort implementation, free to drift from the headers.
 */

function row(over: Partial<ScreenRow> = {}): ScreenRow {
  return {
    ticker: "TSM",
    signalDate: "2026-08-18",
    signalType: "confluence_high",
    signalTypesAll: ["confluence_high", "bb_upper_touch", "stoch_overbought"],
    signalStrength: 3,
    side: "short",
    bbLower: 385.63,
    bbMid: 413.01,
    bbUpper: 440.4,
    bandTs: "2026-08-17",
    kFast: 92.1,
    kSlow: 90.4,
    open: 410,
    high: 442,
    low: 409,
    close: 441,
    volume: 14_000_000,
    livePrice: 441.2,
    livePriceTs: "2026-08-18T13:35:00.000Z",
    firedAt: "2026-08-18T13:35:12.000Z",
    cellId: "cell-1",
    isClusterHead: null,
    reversal: null,
    stats: null,
    ...over,
  } as ScreenRow;
}

/** The pair as `app/page.tsx` renders it: the list, then the table. */
const render = (ctx: SortContext) =>
  renderToStaticMarkup(
    <>
      <MobileSort ctx={ctx} />
      <ScreenerTable rows={[row()]} withStats={ctx.withStats} sortCtx={ctx} />
    </>,
  );

/** Every `href` on an anchor carrying `cls`, in document order. */
function hrefs(html: string, cls: string): string[] {
  const out: string[] = [];
  for (const m of html.matchAll(/<a\b[^>]*>/g)) {
    const tag = m[0];
    const klass = /class="([^"]*)"/.exec(tag)?.[1] ?? "";
    if (!klass.split(" ").includes(cls)) continue;
    out.push((/href="([^"]*)"/.exec(tag)?.[1] ?? "").replace(/&amp;/g, "&"));
  }
  return out;
}

const CONTEXTS: [string, SortContext][] = [
  ["the default order", { sort: null, dir: "desc", date: null, withStats: false, confluenceOnly: true }],
  [
    "an active descending sort with every toggle set",
    { sort: "volume", dir: "desc", date: "2026-08-19", withStats: true, confluenceOnly: false },
  ],
  [
    "an active ascending sort",
    { sort: "ticker", dir: "asc", date: "2026-08-19", withStats: false, confluenceOnly: true },
  ],
];

describe("the mobile sort list is the header row, a second time", () => {
  it.each(CONTEXTS)("offers the same links as the headers under %s", (_label, ctx) => {
    const html = render(ctx);
    const desktop = hrefs(html, "sortlink");
    const mobile = hrefs(html, "msort-opt");
    expect(desktop).toHaveLength(11);
    // Same links, same order: tapping an entry is clicking its header.
    expect(mobile).toEqual(desktop);
  });

  it("flips the active column, exactly as a second header click does", () => {
    const html = render(CONTEXTS[1][1]);
    const volume = hrefs(html, "msort-opt").find((h) => h.includes("sort=volume"));
    expect(volume).toContain("dir=asc");
    expect(volume).toContain("date=2026-08-19");
    expect(volume).toContain("stats=1");
    expect(volume).toContain("all=1");
  });

  it("is out of the desktop render: the block carries `hidden`", () => {
    // No stylesheet rule outside the 720px query hides it, so the attribute
    // is the whole guarantee that a desktop reader never sees a second set
    // of sort links.
    expect(render(CONTEXTS[0][1])).toMatch(/<div class="msort" hidden="">/);
  });

  it("names the active column and its direction on the closed control", () => {
    expect(render(CONTEXTS[1][1])).toMatch(/<summary>.*Vol.*▼.*<\/summary>/s);
    expect(render(CONTEXTS[0][1])).toMatch(/<summary>.*default order.*<\/summary>/s);
  });

  it("offers the way back to the default order only when a sort is active", () => {
    expect(hrefs(render(CONTEXTS[0][1]), "msort-clear")).toEqual([]);
    // The footer's `clear sort` link, reachable without scrolling past
    // every card to find it.
    expect(hrefs(render(CONTEXTS[1][1]), "msort-clear")).toEqual(["/?date=2026-08-19&stats=1&all=1"]);
  });

  it("carries each header's hover text as visible text", () => {
    // The header `title` explains what a column sorts on. With `thead`
    // hidden nothing can be hovered, so the list prints it.
    const html = render(CONTEXTS[0][1]);
    const list = html.slice(html.indexOf('class="msort"'), html.indexOf("<table"));
    expect(list).toContain("Sorts on the lower band.");
    expect(list).toContain("Newest price the poller saw");
  });
});

/** The slice of `Element` that `noteFor` reads. */
interface Fake {
  title?: string;
  tag: string;
  parent?: Fake;
}

/** A stand-in for `Element.closest` over a hand-built ancestor chain. */
function el(node: Fake) {
  const chain: Fake[] = [];
  for (let n: Fake | undefined = node; n; n = n.parent) chain.push(n);
  return {
    closest(selector: string) {
      if (selector === "[title]") {
        const hit = chain.find((n) => n.title !== undefined);
        return hit ? { getAttribute: () => hit.title ?? null } : null;
      }
      const tags = selector.split(",").map((s) => s.trim());
      const hit = chain.find((n) => tags.includes(n.tag));
      return hit ? { getAttribute: () => null } : null;
    },
  };
}

describe("a tap reads the note a hover would have shown", () => {
  it("returns the title of the tapped element", () => {
    expect(noteFor(el({ tag: "td", title: "bands at 2026-08-17" }))).toBe("bands at 2026-08-17");
  });

  it("finds the title on an ancestor", () => {
    const cell = { tag: "td", title: "as of 06:35" };
    expect(noteFor(el({ tag: "span", parent: cell }))).toBe("as of 06:35");
  });

  it("stays out of the way of a control", () => {
    // A link or button does its own job on a tap. Reading its `title`
    // instead would turn every date arrow into a two-tap control.
    expect(noteFor(el({ tag: "a", title: "previous date with rows: 2026-08-17" }))).toBeNull();
    expect(noteFor(el({ tag: "button", title: "pick a date" }))).toBeNull();
    // A control inside a titled cell still wins: the sortable header.
    const th = { tag: "th", title: "Sorts on the raw %K." };
    expect(noteFor(el({ tag: "a", parent: th }))).toBeNull();
    // And so does a titled mark inside a link.
    const link = { tag: "a" };
    expect(noteFor(el({ tag: "abbr", title: "Watch Universe", parent: link }))).toBeNull();
  });

  it("returns nothing where there is nothing to read", () => {
    expect(noteFor(el({ tag: "td" }))).toBeNull();
    expect(noteFor(el({ tag: "td", title: "" }))).toBeNull();
    expect(noteFor(el({ tag: "td", title: "   " }))).toBeNull();
  });
});
