import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { NextConfig } from "next";

/**
 * The repository keeps one `.env.local`, at the root, and `jobs/db.py`
 * reads it. Next only looks inside its own directory, so `next start` came
 * up with no connection string and every page rendered the error state
 * saying neither variable was set — a real 2026-08-19 debugging cycle that
 * looked like a broken query and was a missing file path.
 *
 * Reading the root file here rather than copying it into `web/` keeps one
 * copy of the credentials. **Never overwrites**: a variable already in the
 * environment wins, so `DATABASE_URL_MCP=... npm run dev` still works and a
 * deployment that injects its own configuration is untouched.
 */
function loadRootEnv(): void {
  const path = resolve(process.cwd(), "..", ".env.local");
  if (!existsSync(path)) return;

  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (process.env[key] !== undefined) continue;
    // Strips one layer of matching quotes and nothing else. This is not a
    // full dotenv parser and does not try to be: the file it reads is the
    // one `jobs/db.py::_load_env` already reads with the same simplicity.
    const value = line.slice(eq + 1).trim();
    process.env[key] = value.replace(/^(['"])(.*)\1$/, "$2");
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
