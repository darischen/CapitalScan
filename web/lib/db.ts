import { Pool } from "pg";

type Env = Record<string, string | undefined>;

/**
 * The only place this app opens a connection.
 *
 * ADR 070: every endpoint is an indexed SELECT executed against Postgres
 * through the driver. No computation, no model calls, no Python in the
 * request path.
 *
 * ADR 118: this is the deterministic-retrieval half of the system. The MCP
 * server and the chat route go through the Python handlers; `/`, `/ticker`,
 * and `/research` come here.
 */

let pool: Pool | undefined;

/**
 * Reads `DATABASE_URL_MCP` first, then `DATABASE_URL_RESEARCH`.
 *
 * The read-only role is preferred for the same reason the MCP server
 * prefers it: no route here writes, and no future route *bug* should be
 * able to. `cscan db grant-readonly` provisions it. Falling back rather
 * than refusing keeps a fresh checkout runnable, and `readOnlyRole()` lets
 * the page say which one it got.
 */
// `Record<string, string | undefined>` rather than `NodeJS.ProcessEnv`:
// the latter requires `NODE_ENV`, so a test passing the two keys it cares
// about has to cast, and a cast is exactly the thing that would hide a real
// type error here later.
export function databaseUrl(env: Env = process.env): string {
  const url = env.DATABASE_URL_MCP || env.DATABASE_URL_RESEARCH;
  if (!url) {
    throw new Error(
      "Neither DATABASE_URL_MCP nor DATABASE_URL_RESEARCH is set. " +
        "Copy .env.example to .env.local and fill one in.",
    );
  }
  return url;
}

export function readOnlyRole(env: Env = process.env): boolean {
  return Boolean(env.DATABASE_URL_MCP);
}

export function getPool(): Pool {
  if (!pool) {
    pool = new Pool({
      connectionString: databaseUrl(),
      // A research tool for one operator. A large pool would hold
      // connections open against a database that `cscan backtest` also
      // wants, and ADR 039 keeps the heavy jobs local, so they compete.
      max: 4,
      idleTimeoutMillis: 30_000,
      // Fail visibly rather than hanging a page render forever. The
      // staleness banner exists to report a database that stopped being
      // updated; a request that never returns reports nothing at all.
      connectionTimeoutMillis: 5_000,
    });
  }
  return pool;
}

/**
 * One query. Parameterised only: every caller passes `$1`-style placeholders
 * and an argument array, and nothing in this app interpolates a value into
 * SQL text.
 */
export async function query<T>(text: string, values: unknown[] = []): Promise<T[]> {
  const result = await getPool().query(text, values);
  return result.rows as T[];
}

/**
 * `numeric` arrives from `pg` as a string, on purpose: JavaScript numbers
 * are doubles and `pg` will not silently narrow a value the database stores
 * with more precision than a double holds.
 *
 * Everything this app renders is already within double range, so converting
 * is safe. Doing it in one place is what stops a `parseFloat` appearing in a
 * component, where a `null` would quietly become `NaN` and render as blank
 * next to a real number.
 */
export function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}
