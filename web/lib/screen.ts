import { num, query } from "./db";

/**
 * The screener query. Reads `v_screen_live` (ADR 119), which already decides:
 *
 * - ADR 100's `config_hash` predicate, from the
 *   `capitalscan.default_config_hash` GUC rather than a guess.
 * - ADR 105's `arm = 'signal'` predicate, so the control and benchmark arms
 *   cannot leak into a row.
 * - ADR 107's pooled-over-`signal_strength` cell selection.
 * - `entry_kind = 'touch'` with `is_cluster_head IS NOT FALSE`: what fired,
 *   at detection time. `v_screen` filters `next_open`, which only
 *   `cscan backtest` writes, so it trails the last five-hour backtest --
 *   measured 2026-08-18, newest `next_open` was 2026-08-13 while 67 events
 *   had fired that day.
 * - The cell join still pins `next_open`, because that is the entry the
 *   grid measured. The feed's grain and the statistics' grain differ on
 *   purpose.
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
  /** The t-1 bands the signal compared against (invariant 3). */
  bbLower: number | null;
  bbMid: number | null;
  bbUpper: number | null;
  bandTs: string | null;
  /** Raw %K, the ADR 110 trigger. */
  kFast: number | null;
  /** Smoothed %K, which must agree within `fast_agreement_tol`. */
  kSlow: number | null;
  /** The signal date's own bar. Null intraday: bars are ingested nightly. */
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  /** The newest price the poller saw, with the time it saw it. */
  livePrice: number | null;
  livePriceTs: string | null;
  /** When the poller detected and notified this signal. */
  firedAt: string | null;
  cellId: string | null;
  /**
   * `null` while the poller has written the row and clustering has not run.
   * ADR 054's gap window needs the whole session, so an intraday row cannot
   * know yet whether it is a cluster head.
   */
  isClusterHead: boolean | null;
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
  confluenceOnly: boolean;
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
   * ADR 111's `--confluence-only`, applied by default (user's request,
   * 2026-08-19).
   *
   * Matches `cli.py::scan`'s flag exactly: membership of `confluence_low`
   * or `confluence_high` in **`signal_types_all`**, not in `signal_type`.
   * The latter carries only the most specific type per ADR 057, so a bar
   * that fired both `bear_close_above_upper` and `confluence_high` stores
   * the reversal and would be dropped by a filter on the single column --
   * which is the row a short-side reader most wants.
   */
  confluenceOnly?: boolean;
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
  SELECT max(signal_date) AS d FROM v_screen_live
`;

/**
 * `confluence_high` first, then `confluence_low`, then everything else.
 *
 * A short-side confluence is the row that needs a decision soonest: ADR 111
 * makes it actionable only with a confirming reversal, so it is the one a
 * reader has to look at rather than merely note. Fire time orders within
 * each group, newest first, so the feed still reads chronologically where
 * the ranking does not separate rows.
 */
const CONFLUENCE_RANK = `
  CASE WHEN 'confluence_high' = ANY(s.signal_types_all) THEN 0
       WHEN 'confluence_low'  = ANY(s.signal_types_all) THEN 1
       ELSE 2 END
`;

const CONFLUENCE_FILTER = `
  AND ($3::boolean IS NOT TRUE
       OR s.signal_types_all && ARRAY['confluence_high','confluence_low'])
`;

const FEED_SQL = `
  SELECT s.ticker, s.signal_date, s.signal_type, s.signal_types_all,
         s.signal_strength, s.k_full, s.k_fast,
         s.dd_bucket, s.sector, s.cell_id, s.side, s.is_cluster_head,
         s.bb_lower, s.bb_mid, s.bb_upper, s.band_ts,
         s.open, s.high, s.low, s.close, s.volume,
         s.live_price, s.live_price_ts, s.fired_at
    FROM v_screen_live s
    JOIN v_universe u ON u.ticker = s.ticker
   WHERE u.in_trade
     AND s.signal_date = $1::date
     ${CONFLUENCE_FILTER}
   ORDER BY ${CONFLUENCE_RANK}, s.fired_at DESC NULLS LAST, s.ticker
   LIMIT $2
`;

const COUNT_SQL = `
  SELECT count(*)::int AS n
    FROM v_screen_live s
    JOIN v_universe u ON u.ticker = s.ticker
   WHERE u.in_trade
     AND s.signal_date = $1::date
     AND ($2::boolean IS NOT TRUE
          OR s.signal_types_all && ARRAY['confluence_high','confluence_low'])
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
  bb_lower: string | null;
  bb_mid: string | null;
  bb_upper: string | null;
  band_ts: Date | null;
  k_full: string | null;
  k_fast: string | null;
  dd_bucket: string | null;
  sector: string | null;
  cell_id: string | null;
  side: string;
  is_cluster_head: boolean | null;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
  volume: string | null;
  live_price: string | null;
  live_price_ts: Date | null;
  fired_at: Date | null;
}

export async function screen(options: ScreenOptions = {}): Promise<ScreenResult> {
  const limit = clampLimit(options.limit);
  // Default on. `?all=1` clears it, so nothing is unreachable.
  const confluenceOnly = options.confluenceOnly !== false;

  // Resolved before the feed runs, so the rows and the count describe the
  // same day. `null` here means the view is empty, which the caller renders
  // as the empty state rather than as a failure.
  const date =
    options.date ?? (await query<{ d: Date | null }>(LATEST_DATE_SQL))[0]?.d ?? null;
  if (date === null) {
    return {
      rows: [],
      totalMatched: 0,
      withStats: false,
      confluenceOnly,
      signalDate: null,
      meta: await readMeta(),
    };
  }

  const [rowsRaw, countRows, meta] = await Promise.all([
    query<FeedRowRaw>(FEED_SQL, [date, limit, confluenceOnly]),
    query<{ n: number }>(COUNT_SQL, [date, confluenceOnly]),
    readMeta(),
  ]);

  const rows: ScreenRow[] = rowsRaw.map((r) => ({
    ticker: r.ticker,
    signalDate: isoDate(r.signal_date),
    signalType: r.signal_type,
    signalTypesAll: r.signal_types_all ?? [],
    signalStrength: r.signal_strength ?? 0,
    // `v_screen_live` projects `events.side` directly, so this is the
    // stored value rather than a re-derivation. `sideFor` stays as the
    // fallback and is still tested: it is what a caller without the column
    // would need, and the two must agree.
    sector: r.sector,
    side: (r.side === "short" || r.side === "long" ? r.side : sideFor(r.signal_type)) as Side,
    bbLower: num(r.bb_lower),
    bbMid: num(r.bb_mid),
    bbUpper: num(r.bb_upper),
    bandTs: r.band_ts ? isoDate(r.band_ts) : null,
    kFast: num(r.k_fast),
    kSlow: num(r.k_full),
    open: num(r.open),
    high: num(r.high),
    low: num(r.low),
    close: num(r.close),
    volume: num(r.volume),
    livePrice: num(r.live_price),
    livePriceTs: r.live_price_ts ? r.live_price_ts.toISOString() : null,
    firedAt: r.fired_at ? r.fired_at.toISOString() : null,
    ddBucket: r.dd_bucket,
    cellId: r.cell_id,
    isClusterHead: r.is_cluster_head,
    stats: null,
  }));

  if (options.withStats) {
    await attachStats(rows);
  }

  return {
    rows,
    totalMatched: countRows[0]?.n ?? 0,
    withStats: Boolean(options.withStats),
    confluenceOnly,
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

/**
 * `market_date()`, not `CURRENT_DATE` (ADR 119). The database runs
 * `Etc/UTC`, so between 00:00 UTC and midnight ET the two differ by a day
 * and the staleness count is one session high. Measured live at 2026-08-19
 * 03:06 UTC: 2 reported against a true 1, and the banner fires above
 * `stale_after_days` = 2, so it would have raised a full day early.
 */
const META_SQL = `
  SELECT current_setting('capitalscan.default_config_hash', true) AS config_hash,
         (SELECT max(ts)::date FROM bars WHERE interval = '1d') AS as_of,
         (SELECT count(*)::int FROM trading_days
           WHERE d > (SELECT max(ts)::date FROM bars WHERE interval = '1d')
             AND d <= market_date()) AS staleness_days
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
