import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { NextConfig } from "next";

import { applyEnv, duplicateKeys } from "./lib/env";

/**
 * The repository keeps one `.env.local`, at the root, and `jobs/db.py`
 * reads it. Next only looks inside its own directory, so `next start` came
 * up with no connection string and every page rendered the error state
 * saying neither variable was set — a real 2026-08-19 debugging cycle that
 * looked like a broken query and was a missing file path.
 *
 * Reading the root file here rather than copying it into `web/` keeps one
 * copy of the credentials. The parsing rules, and the duplicate key that
 * made them matter, are in `lib/env.ts`.
 */
function loadRootEnv(): void {
  const path = resolve(process.cwd(), "..", ".env.local");
  if (!existsSync(path)) return;

  const document = readFileSync(path, "utf8");
  applyEnv(document, process.env);

  // Said out loud, once, at startup. A file that assigns one key twice is
  // ambiguous, and every reader of it resolving that ambiguity quietly is
  // how the MCP token ended up different in two processes.
  const duplicates = duplicateKeys(document);
  if (duplicates.length > 0) {
    console.warn(
      `.env.local assigns ${duplicates.join(", ")} more than once. ` +
        "The last assignment wins, here and in python-dotenv. Remove the earlier one.",
    );
  }
}

loadRootEnv();

const config: NextConfig = {
  // `pg` is a Node driver and must not be bundled into a client chunk. Every
  // query in this app runs in a server component or a route handler; this
  // makes a stray `'use client'` import a build error rather than a runtime
  // one in a browser.
  serverExternalPackages: ["pg"],
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: true },
};

export default config;
