/**
 * The `.env.local` parser `next.config.ts` runs at startup.
 *
 * It lives here rather than inside the config so it can be tested. Config
 * files are executed by the build, not imported by it, and the one thing
 * this function does wrong is invisible until a request fails several
 * layers away — see the duplicate-key note below.
 */

/**
 * Applies a dotenv-style document to an environment map, in place.
 *
 * Two rules, and they are `python-dotenv`'s so that one credentials file
 * read by two runtimes resolves to one value:
 *
 * - **The process environment wins over the file.** `DATABASE_URL_MCP=...
 *   npm run dev` still works, and a deployment injecting its own
 *   configuration is untouched.
 * - **Within the file, the last assignment wins.**
 *
 * **The second rule was wrong here and cost a debugging cycle.**
 * `.env.local` carries `MCP_BEARER_TOKEN` twice with different values.
 * `python-dotenv` takes the last, so `cscan mcp serve` came up on one
 * token; this loader took the first and skipped every later line for that
 * key, so `/chat` presented the other and got a flat 401. The MCP SDK
 * surfaces that as a transport error, which reads like the server being
 * down rather than like two files disagreeing about a secret.
 *
 * A duplicate key is still a mistake worth removing from the file. This
 * makes the two readers agree about what the mistake means.
 */
export function applyEnv(document: string, env: Record<string, string | undefined>): void {
  const fromFile = new Set<string>();

  for (const raw of document.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;

    const key = line.slice(0, eq).trim();
    // Already set by the real environment, and not by an earlier line of
    // this same file. Those are the two cases the rules above separate.
    if (env[key] !== undefined && !fromFile.has(key)) continue;
    fromFile.add(key);

    // Strips one layer of matching quotes and nothing else. This is not a
    // full dotenv parser and does not try to be: the file it reads is the
    // one `jobs/db.py::_load_env` already reads with the same simplicity.
    env[key] = line.slice(eq + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
  }
}

/**
 * Keys assigned more than once in the document.
 *
 * `next.config.ts` prints these at startup. Silently resolving a duplicate
 * is what made the last one expensive: both readers were behaving
 * correctly and neither said the file was ambiguous.
 */
export function duplicateKeys(document: string): string[] {
  const counts = new Map<string, number>();
  for (const raw of document.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].filter(([, n]) => n > 1).map(([key]) => key);
}
