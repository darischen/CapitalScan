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

/**
 * Pins `capitalscan.default_config_hash` on every new connection.
 *
 * **Without it the screener renders empty and looks like a quiet day.**
 * Every screener and statistics view filters on
 * `current_setting('capitalscan.default_config_hash', true)`, and the
 * `true` makes that return NULL rather than raising when unset — so
 * `config_hash = NULL` matches nothing and `/` shows zero rows with no
 * error anywhere.
 *
 * Locally the GUC is set on the database with `ALTER DATABASE` (ADR 100).
 * **Neon does not allow that**: custom parameters need superuser and
 * `neondb_owner` is not one, so `ALTER DATABASE ... SET
 * capitalscan.default_config_hash` fails with `InsufficientPrivilege`. A
 * session-level `SET` is permitted, which is what this is.
 *
 * The value comes from `serving_config`, not from an environment variable.
 * That table is synced with the rows it describes, so the hash a
 * connection pins is by construction the hash whose events were copied —
 * there is no second place to update when the config changes, and no way
 * for a URL and a dataset to disagree.
 */
/**
 * **And `TimeZone`, for the same reason and in the same round trip.**
 *
 * `bars.ts` is stored at UTC midnight, so every `timestamptz::date` in the
 * serving queries renders one day early on any connection whose session
 * timezone is west of Greenwich. Research runs `Etc/UTC` and the Pi runs
 * `America/Los_Angeles`, so this was invisible locally and wrong in
 * production -- `META_SQL` reported `as_of 2026-08-24, staleness 2` against
 * a true `2026-08-25, staleness 1`, one session short of a false stale
 * banner at `STALE_AFTER_DAYS = 2`.
 *
 * `META_SQL`'s own comment asserted "the database runs `Etc/UTC`". That is
 * a property of one deployment, not of the schema, so it is set here rather
 * than assumed. `isoDate` documents the same day-shift reached from the
 * other direction; this is the upstream half of it.
 *
 * Both `set_config` calls are in one SELECT because the pin is awaited
 * before the connection is handed to a caller, and a second round trip
 * would be a second chance to race it.
 */
const PIN_CONFIG_HASH =
  "SELECT set_config('capitalscan.default_config_hash', " +
  "(SELECT config_hash FROM serving_config LIMIT 1), false), " +
  "set_config('TimeZone', 'UTC', false)";

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
/**
 * Physical connections that have already had the config hash pinned.
 *
 * A `WeakSet` rather than a flag on the client so a connection the pool
 * discards is not kept alive by this bookkeeping.
 */
const pinned = new WeakSet<object>();

export async function query<T>(text: string, values: unknown[] = []): Promise<T[]> {
  // **Checked out explicitly so the pin can be awaited.** The obvious
  // shape — `pool.on("connect", c => c.query(PIN))` — does not work:
  // node-postgres does not await that handler, so it hands the client to
  // the caller while the `SET` is still in flight. The first query on each
  // new connection then races it and usually wins, and every screener view
  // returns zero rows because `config_hash = NULL` matches nothing.
  //
  // That failed in production rather than in a test, because locally the
  // GUC is set on the database and the race has no visible effect.
  const client = await getPool().connect();
  try {
    if (!pinned.has(client)) {
      await client.query(PIN_CONFIG_HASH);
      pinned.add(client);
    }
    const result = await client.query(text, values);
    return result.rows as T[];
  } finally {
    client.release();
  }
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
