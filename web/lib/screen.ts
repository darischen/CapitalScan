import { num, query } from "./db";

/**
 * The screener query. Reads `v_screen`, which already decides:
 *
 * - ADR 100's `config_hash` predicate, from the
 *   `capitalscan.default_config_hash` GUC rather than a guess.
 * - ADR 105's `arm = 'signal'` predicate, so the control and benchmark arms
 *   cannot leak into a row.
 * - ADR 107's pooled-over-`signal_strength` cell selection.
 * - `is_cluster_head AND entry_kind = 'next_open'`, the grain every Phase 4
 *   statistic was measured on.
 *
 * None of that is rebuilt here. ADR 076 puts the query logic in the view
 * precisely so this file and `capitalscan/handlers/screen.py` cannot drift.
 */

/** Long or short, derived from the signal type. */
export type Side = "long" | "short";

export interface ScreenRow {
  ticker: string;
  signalDate: string;
  signalType: string;
  signalTypesAll: string[];
  signalStrength: number;
  side: Side;
  sector: string | null;
  bbPctb: number | null;
  kFull: number | null;
  kFast: number | null;
  ddBucket: string | null;
  cofireCount: number | null;
  cellId: string | null;
  /** Populated only when `withStats` is requested. See ADR 114. */
  stats: CellStats | Suppressed | null;
}

export interface CellStats {
  kind: "cell";
  cellId: string;
  pHit: number | null;
  baseline: number | null;
  edge: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  qValue: number | null;
  nEff: number | null;
  survivesFdr: boolean;
}

export interface Suppressed {
  kind: "suppressed";
  cellId: string;
  reason: string;
  nEff: number | null;
}

export interface ScreenResult {
  rows: ScreenRow[];
  totalMatched: number;
  withStats: boolean;
  /**
   * The signal date actually being displayed, which is **not** the same as
   * `meta.asOf`.
   *
   * `meta.asOf` is the last ingested bar. This is the newest date `v_screen`
   * has a row for. They diverge because `v_screen` filters
   * `entry_kind = 'next_open'` and only `cscan backtest` writes that kind --
   * the poller and `cscan events` both write `touch`. So the screener trails
   * the last full backtest, which is a five-hour job.
   *
   * Measured 2026-08-18: newest `next_open` was 2026-08-13 while bars ran
   * through 2026-08-17. A single date in the status strip would have
   * reported the fresher of the two beside the older rows.
   */
  signalDate: string | null;
  meta: Meta;
}

export interface Meta {
  configHash: string | null;
  asOf: string | null;
  stalenessDays: number | null;
  stale: boolean;
  readOnly: boolean;
}

/**
 * `LONG_SIGNALS` and `SHORT_SIGNALS` from `core/cells.py`, and the one place
 * this app repeats anything from the Python side.
 *
 * `v_screen` carries no `side` column, and `core.signals.detect` emits one
 * hit per side and never mixes long and short types on a row, so the type
 * determines the side exactly. A test asserts this covers every type the
 * database actually produces, so a new signal type fails loudly here rather
 * than rendering with a grey rail.
 */
const SHORT_SIGNALS = new Set([
  "bb_upper_touch",
  "stoch_overbought",
  "confluence_high",
  "bear_close_above_upper",
]);

export function sideFor(signalType: string): Side {
  return SHORT_SIGNALS.has(signalType) ? "short" : "long";
}

/** ADR 074: capped server-side at 200 regardless of what the caller passes. */
export const MAX_LIMIT = 200;
export const DEFAULT_LIMIT = 50;

export function clampLimit(value: number | undefined): number {
  if (value === undefined || Number.isNaN(value)) return DEFAULT_LIMIT;
  return Math.max(1, Math.min(Math.trunc(value), MAX_LIMIT));
}

interface ScreenOptions {
  date?: string;
  /**
   * ADR 114. The default is the event feed: what fired, on what ticker, in
   * what state. The statistical fields sit behind a deliberate action.
   *
   * ADR 112 measured zero cells surviving FDR correction and 100 of 224
   * train cells suppressed, so four statistical columns would be blank or
   * near-blank on almost every row, every day. Four always-empty columns
   * teach a reader to skip the row, and the row is the part that carries
   * information.
   *
   * **This default is duplicated from `handlers/screen.py` and ADR 118 says
   * so.** `with_stats=False` is a handler decision and does not reach a
   * route that never calls a handler. Named here so it is not discovered as
   * a bug.
   */
  withStats?: boolean;
  limit?: number;
}

/**
 * The date to show when the caller does not name one.
 *
 * **The screener is a one-day view**, so "no date given" has to resolve to a
 * date rather than to "no filter". Without this the feed ordered by
 * `signal_date DESC` and truncated at `limit` silently spanned two days
 * whenever the newest day had fewer rows than the limit -- measured
 * 2026-08-18, the newest day had 46 and `LIMIT 50` pulled four more from the
 * day before, under a single date in the status strip. The count was worse:
 * an unfiltered `count(*)` reported 290,019, the whole history.
 */
const LATEST_DATE_SQL = `
  SELECT max(signal_date) AS d FROM v_screen
`;

const FEED_SQL = `
  SELECT s.ticker, s.signal_date, s.signal_type, s.signal_types_all,
         s.signal_strength, s.bb_pctb, s.k_full, s.k_fast,
         s.dd_bucket, s.cofire_count, s.sector, s.cell_id
    FROM v_screen s
    JOIN v_universe u ON u.ticker = s.ticker
   WHERE u.in_trade
     AND s.signal_date = $1::date
   ORDER BY s.cofire_count DESC NULLS LAST, s.ticker
   LIMIT $2
`;

const COUNT_SQL = `
  SELECT count(*)::int AS n
    FROM v_screen s
    JOIN v_universe u ON u.ticker = s.ticker
   WHERE u.in_trade
     AND s.signal_date = $1::date
`;

/**
 * `numeric` columns arrive as strings from `pg`. Typed loosely here and
 * narrowed by `num()` rather than trusted, because a wrong assumption about
 * this is invisible: `"0.51" > 0.6` is false and so is `0.51 > 0.6`.
 */
interface FeedRowRaw {
  ticker: string;
  signal_date: Date;
  signal_type: string;
  signal_types_all: string[] | null;
  signal_strength: number | null;
  bb_pctb: string | null;
  k_full: string | null;
  k_fast: string | null;
  dd_bucket: string | null;
  cofire_count: number | null;
  sector: string | null;
  cell_id: string | null;
}

export async function screen(options: ScreenOptions = {}): Promise<ScreenResult> {
  const limit = clampLimit(options.limit);

  // Resolved before the feed runs, so the rows and the count describe the
  // same day. `null` here means the view is empty, which the caller renders
  // as the empty state rather than as a failure.
  const date =
    options.date ?? (await query<{ d: Date | null }>(LATEST_DATE_SQL))[0]?.d ?? null;
  if (date === null) {
    return { rows: [], totalMatched: 0, withStats: false, signalDate: null, meta: await readMeta() };
  }

  const [rowsRaw, countRows, meta] = await Promise.all([
    query<FeedRowRaw>(FEED_SQL, [date, limit]),
    query<{ n: number }>(COUNT_SQL, [date]),
    readMeta(),
  ]);

  const rows: ScreenRow[] = rowsRaw.map((r) => ({
    ticker: r.ticker,
    signalDate: isoDate(r.signal_date),
    signalType: r.signal_type,
    signalTypesAll: r.signal_types_all ?? [],
    signalStrength: r.signal_strength ?? 0,
    side: sideFor(r.signal_type),
    sector: r.sector,
    bbPctb: num(r.bb_pctb),
    kFull: num(r.k_full),
    kFast: num(r.k_fast),
    ddBucket: r.dd_bucket,
    cofireCount: r.cofire_count,
    cellId: r.cell_id,
    stats: null,
  }));

  if (options.withStats) {
    await attachStats(rows);
  }

  return {
    rows,
    totalMatched: countRows[0]?.n ?? 0,
    withStats: Boolean(options.withStats),
    signalDate: typeof date === "string" ? date : isoDate(date),
    meta,
  };
}

const CELL_SQL = `
  SELECT cell_id, p_hit, baseline, edge, ci_low, ci_high,
         q_value, n_eff, suppressed, suppress_reason
    FROM v_stats
   WHERE cell_id = ANY($1::text[])
     AND arm = 'signal'
     AND config_hash = current_setting('capitalscan.default_config_hash', true)
`;

interface CellRowRaw {
  cell_id: string;
  p_hit: string | null;
  baseline: string | null;
  edge: string | null;
  ci_low: string | null;
  ci_high: string | null;
  q_value: string | null;
  n_eff: number | null;
  suppressed: boolean | null;
  suppress_reason: string | null;
}

/**
 * One query for every distinct cell on the page, not one per row.
 *
 * Fifty rows in one drawdown bucket share a cell, so a per-row lookup is
 * fifty round trips for one answer.
 */
async function attachStats(rows: ScreenRow[]): Promise<void> {
  const ids = [...new Set(rows.map((r) => r.cellId).filter((v): v is string => Boolean(v)))];
  if (ids.length === 0) return;

  const raw = await query<CellRowRaw>(CELL_SQL, [ids]);
  const alpha = 0.05; // StatsParams.fdr_alpha
  const byCell = new Map<string, CellStats | Suppressed>();

  for (const c of raw) {
    if (c.suppressed) {
      byCell.set(c.cell_id, {
        kind: "suppressed",
        cellId: c.cell_id,
        // The *stored* reason. `research/cell_stats.py` wrote it against the
        // `min_n_eff` in force at compute time; a reason regenerated here
        // would quote today's floor against yesterday's counts.
        reason: c.suppress_reason ?? "suppressed",
        nEff: c.n_eff,
      });
      continue;
    }
    const q = num(c.q_value);
    byCell.set(c.cell_id, {
      kind: "cell",
      cellId: c.cell_id,
      pHit: num(c.p_hit),
      baseline: num(c.baseline),
      edge: num(c.edge),
      ciLow: num(c.ci_low),
      ciHigh: num(c.ci_high),
      qValue: q,
      nEff: c.n_eff,
      survivesFdr: q !== null && q <= alpha,
    });
  }

  for (const row of rows) {
    row.stats = row.cellId ? (byCell.get(row.cellId) ?? null) : null;
  }
}

const META_SQL = `
  SELECT current_setting('capitalscan.default_config_hash', true) AS config_hash,
         (SELECT max(ts)::date FROM bars WHERE interval = '1d') AS as_of,
         (SELECT count(*)::int FROM trading_days
           WHERE d > (SELECT max(ts)::date FROM bars WHERE interval = '1d')
             AND d <= CURRENT_DATE) AS staleness_days
`;

/** `MonitoringThresholds.stale_after_days`. */
export const STALE_AFTER_DAYS = 2;

export async function readMeta(): Promise<Meta> {
  const [row] = await query<{
    config_hash: string | null;
    as_of: Date | null;
    staleness_days: number | null;
  }>(META_SQL);

  const staleness = row?.staleness_days ?? null;
  return {
    configHash: row?.config_hash ?? null,
    asOf: row?.as_of ? isoDate(row.as_of) : null,
    stalenessDays: staleness,
    stale: staleness !== null && staleness > STALE_AFTER_DAYS,
    readOnly: Boolean(process.env.DATABASE_URL_MCP),
  };
}

const LAST_FIRE_SQL = `
  SELECT ticker, signal_date
    FROM v_screen
   ORDER BY signal_date DESC, ticker
   LIMIT 1
`;

/**
 * DESIGN §11.2's empty state: `No signals today. Last fire: TSM, 3 days ago`.
 *
 * Most days nothing fires, so this is the common path rather than an edge
 * case, and the reference is what stops an empty screen from being
 * indistinguishable from a broken one.
 */
export async function lastFire(): Promise<{ ticker: string; signalDate: string } | null> {
  const [row] = await query<{ ticker: string; signal_date: Date }>(LAST_FIRE_SQL);
  return row ? { ticker: row.ticker, signalDate: isoDate(row.signal_date) } : null;
}

/** `pg` returns `date` as a JS Date at local midnight; take the calendar day. */
export function isoDate(value: Date | string): string {
  if (typeof value === "string") return value.slice(0, 10);
  const y = value.getFullYear();
  const m = String(value.getMonth() + 1).padStart(2, "0");
  const d = String(value.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}
