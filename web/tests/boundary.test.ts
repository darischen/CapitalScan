import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The two structural guarantees this app makes, asserted against the source
 * rather than trusted.
 *
 * Session 16 has the same shape of test for `mcp/`, and the plan for
 * Session 17 says to copy it. This is that copy, plus the holdout guard,
 * because both are claims about what the code *cannot* do and neither is
 * visible in any rendered page.
 */

const ROOT = join(__dirname, "..");
const DIRS = ["app", "components", "lib"];

function sources(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) {
        walk(path);
      } else if (/\.(ts|tsx)$/.test(name)) {
        out.push(path);
      }
    }
  };
  for (const d of DIRS) walk(join(ROOT, d));
  return out;
}

function read(path: string): string {
  return readFileSync(path, "utf8");
}

/** The file's code with block and line comments removed.
 *
 * Every guard below is about what the code *does*, and these files document
 * the boundaries they respect. `lib/ticker.ts` explains in prose why it
 * never reads `split_key`, and a naive grep would fail on the explanation
 * of the rule rather than on a breach of it. */
function code(path: string): string {
  return read(path)
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");
}

describe("gate 2: no web module imports the Python data layer (ADR 118)", () => {
  it("finds source files to check, so the sweep cannot pass vacuously", () => {
    const files = sources();
    expect(files.length).toBeGreaterThanOrEqual(8);
  });

  it.each(sources())("%s imports neither sqlalchemy nor db_io", (path) => {
    const text = code(path);
    expect(text).not.toMatch(/\bsqlalchemy\b/i);
    expect(text).not.toMatch(/\bdb_io\b/);
  });

  /**
   * The stronger version of the same rule. ADR 118 is not "do not import
   * SQLAlchemy", it is "these routes talk to Postgres and nothing else", so
   * an HTTP call to the MCP server or a spawned Python process would satisfy
   * the letter and break the decision.
   */
  /**
   * The chat path, and the only files allowed to reach the MCP server.
   *
   * ADR 118's rule has two halves and Session 17 could only test one,
   * because the LLM caller did not exist yet: *routes select from the
   * views, and MCP is for LLM callers.* `/chat` is that caller, so the
   * guard below became "everything except these six files", and the list is
   * written out rather than matched by directory — `components/Chat.tsx`
   * and `app/chat/page.tsx` are on it because they are the surface, not
   * because they connect, and if either ever does the reason should be an
   * edit here.
   */
  const CHAT_PATH = new Set([
    "lib/chat.ts",
    "lib/mcp.ts",
    "lib/prompt.ts",
    "app/api/chat/route.ts",
    "components/Chat.tsx",
    "app/chat/page.tsx",
  ]);

  const rel = (path: string) => relative(ROOT, path).split(sep).join("/");

  it.each(sources())("%s does not shell out or call the MCP server", (path) => {
    const text = code(path);
    // Universal. Nothing in this app spawns a process, chat path included:
    // ADR 070 says no Python runs in the request path, and reaching the
    // handlers over HTTP is exactly how the chat route obeys that.
    expect(text).not.toMatch(/child_process|spawnSync|execSync/);
    if (CHAT_PATH.has(rel(path))) return;
    expect(text).not.toMatch(/8787|mcp\/v1|modelcontextprotocol/i);
  });

  /**
   * The carve-out is asserted from the other side too.
   *
   * A list of exempt files is only meaningful while the files on it still
   * need the exemption. Without this, deleting the MCP client and having
   * `/chat` query Postgres directly would leave every test in this file
   * green — the exemption would simply stop being used.
   */
  it("the chat route does reach the MCP server, which is why it is exempt", () => {
    const client = sources().find((path) => rel(path) === "lib/mcp.ts");
    expect(client, "lib/mcp.ts is missing").toBeDefined();
    expect(code(client!)).toMatch(/modelcontextprotocol/);
    expect(code(join(ROOT, "app", "api", "chat", "route.ts"))).toMatch(/@\/lib\/mcp/);
  });
});

describe("gate 9: no route reads holdout (ADR 019, ADR 033)", () => {
  /**
   * `v_events` projects `split_key` and `v_stats` is keyed by it, so the
   * column is one `WHERE` clause away at all times. The Python handlers
   * refuse a holdout request by raising `HoldoutRequested`; here the
   * guarantee is that no query mentions the word and no route accepts a
   * parameter that could carry it.
   *
   * Invariant 5b is the other half and lives in SQL: the serving views
   * hardcode `split_key = 'validate'` on their statistics join rather than
   * inheriting an event's own key, which on a live event is `holdout`.
   */
  /**
   * The word may appear in prose explaining why holdout is absent — that
   * is documentation of the rule, not a breach of it. What must not appear
   * is holdout as a **string literal**, which is the only form that can
   * reach a query.
   *
   * The earlier version matched the bare word and fired on
   * `app/research/page.tsx` for the sentence "it is the holdout window".
   */
  it.each(sources())("%s never names holdout as a value", (path) => {
    expect(code(path)).not.toMatch(/["'`]holdout["'`]/i);
  });

  /**
   * `split_key` is banned from routes that serve *live* data, where a split
   * has no meaning and reaching for one is how holdout leaks.
   *
   * `/research` is the exception and is exempted by name: showing train
   * against validate is the page's entire purpose. It is safe because the
   * split list there is a literal `["train", "validate"]` rather than a
   * parameter — `research.test.ts` asserts that, and asserts the route
   * takes no `searchParams` at all.
   */
  const SPLIT_ALLOWED = new Set(["lib/research.ts"]);

  it.each(sources())("%s never filters or selects split_key", (path) => {
    const rel = relative(ROOT, path).split(sep).join("/");
    if (SPLIT_ALLOWED.has(rel)) return;
    expect(code(path)).not.toMatch(/split_key/);
  });

  it("every split_key exemption still reads split_key", () => {
    // An exemption kept past its reason hides a working thing instead of
    // documenting a broken one.
    for (const rel of SPLIT_ALLOWED) {
      expect(code(join(ROOT, rel))).toMatch(/split_key/);
    }
  });

  it("the ticker route exposes no split parameter", () => {
    const page = read(join(ROOT, "app", "ticker", "[sym]", "page.tsx"));
    const params = page.slice(page.indexOf("searchParams: Promise<"));
    expect(params.slice(0, 200)).not.toMatch(/split|era|arm/);
  });
});

describe("parameterisation", () => {
  /**
   * Every query is parameterised. A template literal is how these SQL
   * constants are written, so the check is for an *interpolation* inside
   * one — `${...}` in a string that also contains SELECT — rather than for
   * backticks, which are everywhere and benign.
   *
   * Three named fragments are interpolated on purpose, and every one is a
   * constant defined in the same file with no argument reaching it. They
   * are listed here so each exception is visible rather than silent — this
   * test caught `CHART_COLUMNS` the moment it was added, which is the
   * behaviour wanted from it.
   */
  const ALLOWED = new Set([
    "${CONFLUENCE_RANK}",
    "${CONFLUENCE_FILTER}",
    "${CHART_COLUMNS}",
    "${FEED_DOMAIN}",
  ]);

  it.each(sources())("%s interpolates nothing user-supplied into SQL", (path) => {
    const text = code(path);
    for (const block of text.match(/`[^`]*`/g) ?? []) {
      if (!/\bSELECT\b/i.test(block)) continue;
      for (const hole of block.match(/\$\{[^}]*\}/g) ?? []) {
        expect(ALLOWED, `${relative(ROOT, path)} interpolates ${hole}`).toContain(hole);
      }
    }
  });
});

describe("the client boundary", () => {
  /**
   * `pg` must never reach a browser bundle. `next.config.ts` marks it
   * external, which turns a stray import into a build error, and this is the
   * cheaper check that says which file did it.
   */
  it("the client components are exactly the six that need a browser", () => {
    const clients = sources()
      .filter((p) => /^\s*["']use client["']/.test(read(p)))
      .map((p) => relative(ROOT, p).split(sep).join("/"))
      .sort();
    // Each earns it: a streaming transcript, a popover calendar, an
    // IntersectionObserver, a polled quote, a canvas chart, and a
    // keyboard-driven menu. The list is asserted whole rather than as a
    // count, so adding one is a decision someone makes here.
    expect(clients).toEqual([
      "components/Chat.tsx",
      "components/DatePicker.tsx",
      "components/EventRows.tsx",
      "components/LivePrice.tsx",
      "components/TickerChart.tsx",
      "components/TickerSearch.tsx",
    ]);
    for (const path of sources().filter((p) => /^\s*["']use client["']/.test(read(p)))) {
      expect(code(path)).not.toMatch(/from "pg"|@\/lib\/db/);
    }
  });
});

describe("props crossing the server/client boundary", () => {
  /**
   * **A function cannot be a prop on a client component.** React serializes
   * props into the RSC payload and there is no wire form for a closure, so
   * it throws `Functions cannot be passed directly to Client Components` at
   * request time — every page render, in production.
   *
   * Nothing else catches it. `tsc` is happy: the prop type is a function
   * and the value is a function. `next build` is happy: it compiles, and
   * the fault only exists once a render serializes. The component tests use
   * `renderToStaticMarkup`, which does not serialize at all.
   *
   * Shipped once, on `DatePicker`, and took a 500 with an opaque digest to
   * find.
   *
   * **The first version of this test did not work**, which is worth
   * recording: it matched the tag as `<Name[^>]*`, and `[^>]*` stops at the
   * `>` inside `=>`. It passed against the exact code it was written to
   * catch. A window of characters after the tag name has no such hole.
   */
  const CLIENT = ["DatePicker", "EventRows", "TickerChart", "TickerSearch"];

  /** Long enough to cover a multi-line JSX tag, short enough not to run
   * into the next element's props. */
  const WINDOW = 500;

  it.each(sources())("%s passes no function into a client component", (path) => {
    const text = code(path);
    for (const name of CLIENT) {
      // `\\b`, not `\b`. In a template literal `\b` is a backspace
      // character, so the first version of this matched nothing and passed
      // against the exact code it was written to catch.
      const open = new RegExp(`<${name}\\b`, "g");
      let m: RegExpExecArray | null;
      while ((m = open.exec(text)) !== null) {
        // Up to the tag's own close, or a fixed window when the close is
        // further than any real prop list would be.
        const rest = text.slice(m.index, m.index + WINDOW);
        const close = rest.search(/\/>|>\s*$/m);
        const tag = close === -1 ? rest : rest.slice(0, close);
        expect(tag, `${relative(ROOT, path)} passes a closure to <${name}>`).not.toMatch(
          /=\{\s*(?:\([^)]*\)|\w+)\s*=>/,
        );
        expect(tag, `${relative(ROOT, path)} passes a function to <${name}>`).not.toMatch(
          /=\{\s*(?:async\s+)?function/,
        );
      }
    }
  });

  it("finds the client components it claims to check", () => {
    // Guards the list above against a rename making every assertion vacuous.
    for (const name of CLIENT) {
      const file = join(ROOT, "components", `${name}.tsx`);
      expect(readFileSync(file, "utf8")).toMatch(/^\s*["']use client["']/);
    }
  });
});
