import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

import { config, middleware } from "@/middleware";

/**
 * The site is locked, and the ways that control usually fails.
 *
 * It exists because Vercel's Hobby plan cannot protect a production URL:
 * `capitalscan.vercel.app` and the branch alias were both publicly
 * readable. This is the replacement, so it carries the weight Vercel's
 * setting would have.
 *
 * The interesting tests are not "a good password works". They are the
 * failure modes: an unset secret, a route nobody remembered to list, and a
 * response that differs depending on *why* it failed.
 */

const ROOT = join(__dirname, "..");
const PASSWORD = "correct-horse-battery-staple";

/** A request the middleware will accept or reject. `NextRequest` is heavy
 * here; the middleware reads only `nextUrl.pathname` and one header. */
function request(pathname: string, authorization?: string) {
  return {
    nextUrl: { pathname },
    headers: { get: (name: string) => (name === "authorization" ? (authorization ?? null) : null) },
  } as unknown as Parameters<typeof middleware>[0];
}

const basic = (user: string, pass: string) => `Basic ${btoa(`${user}:${pass}`)}`;

function withPassword<T>(value: string | undefined, run: () => T): T {
  const previous = process.env.SITE_PASSWORD;
  if (value === undefined) delete process.env.SITE_PASSWORD;
  else process.env.SITE_PASSWORD = value;
  try {
    return run();
  } finally {
    if (previous === undefined) delete process.env.SITE_PASSWORD;
    else process.env.SITE_PASSWORD = previous;
  }
}

describe("the correct password gets through", () => {
  it("accepts it, whatever the username", () => {
    withPassword(PASSWORD, () => {
      for (const user of ["", "daris", "admin"]) {
        expect(middleware(request("/", basic(user, PASSWORD))).status).not.toBe(401);
      }
    });
  });

  it("accepts a password containing a colon", () => {
    // `user:pass` splits on the FIRST colon. Splitting on every one would
    // silently truncate any password with a colon in it, and the symptom
    // would be "the right password is rejected".
    const colonful = "a:b:c";
    withPassword(colonful, () => {
      expect(middleware(request("/", basic("u", colonful))).status).not.toBe(401);
    });
  });
});

describe("everything else is refused", () => {
  it.each([
    ["no header", undefined],
    ["wrong password", basic("u", "wrong")],
    ["empty password", basic("u", "")],
    ["bearer instead of basic", "Bearer sometoken"],
    ["malformed base64", "Basic !!!not-base64!!!"],
    ["basic with no payload", "Basic"],
    ["a password that is a prefix of the real one", basic("u", PASSWORD.slice(0, -1))],
    ["a password that extends the real one", basic("u", `${PASSWORD}x`)],
  ])("401s on %s", (_label, header) => {
    withPassword(PASSWORD, () => {
      expect(middleware(request("/", header)).status).toBe(401);
    });
  });

  it("every failure returns an identical response", () => {
    withPassword(PASSWORD, () => {
      const bodies = new Set<string>();
      for (const h of [undefined, basic("u", "wrong"), "Bearer x", "Basic !!!"]) {
        const res = middleware(request("/", h));
        bodies.add(`${res.status}|${res.headers.get("www-authenticate")}`);
      }
      // One distinct response. A different body or header for "wrong
      // password" than for "no header" tells an attacker which half they
      // got right.
      expect(bodies.size).toBe(1);
    });
  });

  it("never lets a 401 be cached", () => {
    withPassword(PASSWORD, () => {
      expect(middleware(request("/")).headers.get("cache-control")).toBe("no-store");
    });
  });
});

describe("an unset SITE_PASSWORD locks the site rather than opening it", () => {
  /**
   * The failure this control most plausibly has: someone deploys without
   * the variable and the middleware waves everything through, because
   * "no password configured" reads naturally as "no password required".
   */
  it("503s instead of allowing the request", () => {
    withPassword(undefined, () => {
      const res = middleware(request("/"));
      expect(res.status).toBe(503);
      expect(res.status).not.toBe(200);
    });
  });

  it("refuses even with a plausible-looking header", () => {
    withPassword(undefined, () => {
      expect(middleware(request("/", basic("u", "anything"))).status).toBe(503);
    });
  });
});

describe("coverage: no route escapes", () => {
  /**
   * Enumerates the real route tree and asserts each path is challenged.
   *
   * A hand-written list of routes would go stale the day someone adds
   * one — and the new route would be the unprotected one. This walks
   * `app/` instead, so a new `page.tsx` or `route.ts` joins the test
   * automatically.
   */
  function routes(): string[] {
    const out: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/^(page|route)\.tsx?$/.test(name)) {
          const rel = relative(join(ROOT, "app"), dir).split(sep).join("/");
          out.push(`/${rel}`.replace(/\/$/, "") || "/");
        }
      }
    };
    walk(join(ROOT, "app"));
    return out;
  }

  it("finds the route tree, so the sweep cannot pass vacuously", () => {
    const found = routes();
    expect(found.length).toBeGreaterThanOrEqual(6);
    expect(found).toContain("/");
  });

  it.each(routes())("%s is challenged without credentials", (route) => {
    withPassword(PASSWORD, () => {
      // `[sym]` stays literal: the middleware does not parse segments, and
      // a concrete value would test the same code path.
      expect(middleware(request(route)).status).toBe(401);
    });
  });

  it("the api surface in particular is never exempt", () => {
    withPassword(PASSWORD, () => {
      for (const route of ["/api/chat", "/api/live", "/api/chart", "/api/events"]) {
        expect(middleware(request(route)).status).toBe(401);
      }
    });
  });
});

describe("the exemption list stays short", () => {
  const source = readFileSync(join(ROOT, "middleware.ts"), "utf8");

  it("exempts only immutable build assets", () => {
    const match = /const PUBLIC_PREFIXES = \[([^\]]*)\]/.exec(source);
    expect(match).not.toBeNull();
    const entries = (match![1].match(/"[^"]*"/g) ?? []).map((s) => s.slice(1, -1));
    // An exemption list is how this control usually fails. Adding one is a
    // deliberate act that has to update this assertion.
    expect(entries).toEqual(["/_next/static", "/favicon.ico"]);
  });

  it("does not exempt /_next/image, which proxies arbitrary URLs", () => {
    withPassword(PASSWORD, () => {
      expect(middleware(request("/_next/image")).status).toBe(401);
    });
  });

  it("lets the hashed assets through, or the login page renders unstyled", () => {
    withPassword(PASSWORD, () => {
      expect(middleware(request("/_next/static/chunks/main.js")).status).not.toBe(401);
    });
  });

  it("matches every path rather than a list of routes", () => {
    // A matcher enumerating routes goes stale the day one is added.
    expect(config.matcher).toBe("/:path*");
  });
});
