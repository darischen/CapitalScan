import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * A page loads the same way however you reach it (user's rule, 2026-11-02).
 *
 * A `next/link` click or a `router.push` fetches only the server-components
 * payload and builds the page in JavaScript on the visitor's device. A typed
 * URL, a reload or a plain `<a>` gets HTML, which the browser paints itself.
 * Leaving a ticker page by its `‹ screener` link was the first kind and every
 * other way onto the screener was the second, so the same page arrived two
 * ways. Every in-app navigation is now a plain `<a>` or
 * `window.location.assign`, as the screener's own row and sort links already
 * were (see `SortHeader` in `components/Screener.tsx`).
 */

const ROOT = join(__dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

const CLIENT_NAVIGATION = [
  /from\s+["']next\/link["']/,
  /import\s*\{[^}]*\buseRouter\b[^}]*\}\s*from\s*["']next\/navigation["']/,
  /\brouter\.(push|replace)\(/,
];

describe("every in-app navigation is a full page load", () => {
  it("leaves no next/link import and no client-router navigation", () => {
    const offenders = ["app", "components", "lib"]
      .flatMap((dir) => sources(join(ROOT, dir)))
      .filter((path) => {
        const code = readFileSync(path, "utf8");
        return CLIENT_NAVIGATION.some((pattern) => pattern.test(code));
      })
      .map((path) => relative(ROOT, path));
    expect(offenders).toEqual([]);
  });
});
