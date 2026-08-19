import { num, query } from "./db";
import { isoDate, sideFor, type Side } from "./screen";

/**
 * The ticker page's three reads: series, state, history.
 *
 * Three queries rather than one, and that is a decision rather than an
 * oversight. DESIGN §8.2: "The ticker page needs `v_ticker_state` (one row,
 * the state rail) and `v_chart` (one row per bar) as separate views.
 * Merging them would force the chart query to re-read every state column on
 * every bar." The history is a third grain again — one row per event, with
 * outcomes — and `v_events` already carries it.
 *
 * ADR 118: these are SELECTs against the views. No handler, no MCP, no
 * query logic here.
 */

/** How far back the chart runs. Sessions, not calendar days. */
export const RANGES = {
  "6m": 126,
  "1y": 252,
  "2y": 504,
  "5y": 1260,
} as const;

export type Range = keyof typeof RANGES;
export const DEFAULT_RANGE: Range = "1y";

/**
 * `Object.hasOwn`, not `in`.
 *
 * `in` walks the prototype chain, so `?range=__proto__` and
 * `?range=toString` both passed the check and reached `RANGES[range]`,
 * which then handed an object or a function to `LIMIT $2`. Caught by the
 * test, not by review.
 */
export function parseRange(raw: string | undefined): Range {
  return raw !== undefined && Object.hasOwn(RANGES, raw) ? (raw as Range) : DEFAULT_RANGE;
}

export interface ChartBar {
  /** `YYYY-MM-DD`. `lightweight-charts` takes this form directly. */
  ts: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  bbLower: number | null;
  bbMid: number | null;
  bbUpper: number | null;
  /** Smoothed %K. */
  kFull: number | null;
  dFull: number | null;
  /**
   * Raw %K, the ADR 110 trigger. Both render: the agreement between them is
   * part of the signal definition, so a chart showing one shows half the
   * rule.
   */
  kFast: number | null;
  sma200: number | null;
  /**
   * Every event on this bar (ADR 120).
   *
   * Arrays because 116 dates carry both a long and a short head, and a
   * chart library keyed on time silently keeps the last of a duplicate.
   */
  eventIds: number[];
  signalTypes: string[];
  sides: Side[];
  signalStrength: number | null;
}

export interface TickerState {
  ticker: string;
  name: string | null;
  sector: string | null;
  asOf: string;
  close: number | null;
  volume: number | null;
  bbLower: number | null;
  bbMid: number | null;
  bbUpper: number | null;
  bbPctB: number | null;
  bbWidthPct: number | null;
  kFull: number | null;
  dFull: number | null;
  kFast: number | null;
  kCrossUp: boolean | null;
  kCrossDown: boolean | null;
  sma200: number | null;
  atr14: number | null;
  rv20d: number | null;
  ddPct: number | null;
  daysToEarnings: number | null;
  vixClose: number | null;
  inTrade: boolean | null;
  aboveSma200: boolean | null;
  mcapUsd: number | null;
}

export interface TickerEvent {
  id: number;
  signalDate: string;
  signalType: string;
  signalTypesAll: string[];
  signalStrength: number;
  side: Side;
  isClusterHead: boolean | null;
  ddBucket: string | null;
  entryDate: string | null;
  entryPrice: number | null;
  exitDate: string | null;
  exitPrice: number | null;
  exitReason: string | null;
  holdingDays: number | null;
  netRet: number | null;
  mfe: number | null;
  mae: number | null;
  earningsInWindow: boolean | null;
}

/**
 * `v_chart` is already config-scoped and marker-aggregated (ADR 120), so
 * this is a range read and nothing else.
 *
 * The window is counted in **sessions**, taken from `trading_days`, not in
 * calendar days. `now() - interval '1 year'` drifts by the holiday count
 * and makes two tickers' charts cover different numbers of bars.
 */
const CHART_SQL = `
  SELECT ts::date AS ts, open, high, low, close, volume,
         bb_lower, bb_mid, bb_upper, k_full, d_full, k_fast, sma_200,
         event_ids, signal_types, sides, signal_strength
    FROM v_chart
   WHERE ticker = $1
     AND ts >= (SELECT min(d) FROM (
                  SELECT d FROM trading_days
                   WHERE d <= market_date()
                   ORDER BY d DESC LIMIT $2
                ) w)
   ORDER BY ts
`;

interface ChartRowRaw {
  ts: Date;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
  volume: string | null;
  bb_lower: string | null;
  bb_mid: string | null;
  bb_upper: string | null;
  k_full: string | null;
  d_full: string | null;
  k_fast: string | null;
  sma_200: string | null;
  event_ids: string[] | null;
  signal_types: string[] | null;
  sides: string[] | null;
  signal_strength: number | null;
}

export async function chart(ticker: string, range: Range = DEFAULT_RANGE): Promise<ChartBar[]> {
  const rows = await query<ChartRowRaw>(CHART_SQL, [ticker, RANGES[range]]);
  return rows.map((r) => ({
    ts: isoDate(r.ts),
    open: num(r.open),
    high: num(r.high),
    low: num(r.low),
    close: num(r.close),
    volume: num(r.volume),
    bbLower: num(r.bb_lower),
    bbMid: num(r.bb_mid),
    bbUpper: num(r.bb_upper),
    kFull: num(r.k_full),
    dFull: num(r.d_full),
    kFast: num(r.k_fast),
    sma200: num(r.sma_200),
    // `bigint[]` arrives as an array of strings for the same reason a bare
    // `bigint` does: it does not fit a double. An event id does fit, and is
    // only ever compared, so it converts here rather than at every use.
    eventIds: (r.event_ids ?? []).map(Number),
    signalTypes: r.signal_types ?? [],
    sides: (r.sides ?? []).map((s) => (s === "short" ? "short" : "long")) as Side[],
    signalStrength: r.signal_strength,
  }));
}

const STATE_SQL = `
  SELECT ticker, name, sector, as_of::date AS as_of, close, volume,
         bb_lower, bb_mid, bb_upper, bb_pctb, bb_width_pct,
         k_full, d_full, k_fast, k_cross_up, k_cross_down,
         sma_200, atr_14, rv_20d, dd_52w, days_to_earnings, vix_close,
         in_trade, above_sma200, mcap_usd
    FROM v_ticker_state
   WHERE ticker = $1
`;

interface StateRowRaw {
  ticker: string;
  name: string | null;
  sector: string | null;
  as_of: Date;
  close: string | null;
  volume: string | null;
  bb_lower: string | null;
  bb_mid: string | null;
  bb_upper: string | null;
  bb_pctb: string | null;
  bb_width_pct: string | null;
  k_full: string | null;
  d_full: string | null;
  k_fast: string | null;
  k_cross_up: boolean | null;
  k_cross_down: boolean | null;
  sma_200: string | null;
  atr_14: string | null;
  rv_20d: string | null;
  dd_52w: string | null;
  days_to_earnings: number | null;
  vix_close: string | null;
  in_trade: boolean | null;
  above_sma200: boolean | null;
  mcap_usd: string | null;
}

/**
 * `null` when the symbol has no indicator history.
 *
 * That covers both an unknown symbol and a known one with no bars, and the
 * page renders the same thing for each because the database cannot tell
 * them apart from here: every one of the 96 inactive tickers either carries
 * no bars at all or carries a handful with no indicators, so
 * `v_ticker_state` returns nothing for all of them. Measured 2026-08-19.
 */
export async function state(ticker: string): Promise<TickerState | null> {
  const [r] = await query<StateRowRaw>(STATE_SQL, [ticker]);
  if (!r) return null;
  return {
    ticker: r.ticker,
    name: r.name,
    sector: r.sector,
    asOf: isoDate(r.as_of),
    close: num(r.close),
    volume: num(r.volume),
    bbLower: num(r.bb_lower),
    bbMid: num(r.bb_mid),
    bbUpper: num(r.bb_upper),
    bbPctB: num(r.bb_pctb),
    bbWidthPct: num(r.bb_width_pct),
    kFull: num(r.k_full),
    dFull: num(r.d_full),
    kFast: num(r.k_fast),
    kCrossUp: r.k_cross_up,
    kCrossDown: r.k_cross_down,
    sma200: num(r.sma_200),
    atr14: num(r.atr_14),
    rv20d: num(r.rv_20d),
    ddPct: num(r.dd_52w),
    daysToEarnings: r.days_to_earnings,
    vixClose: num(r.vix_close),
    inTrade: r.in_trade,
    aboveSma200: r.above_sma200,
    mcapUsd: num(r.mcap_usd),
  };
}

/**
 * Event history.
 *
 * **`entry_kind = 'touch'`, matching the chart's markers (ADR 120).**
 * `v_events` carries all four kinds, so an unfiltered read returns the same
 * signal four times. `touch` is the one the markers use, and a history row
 * with no marker on the chart above it is the kind of disagreement nobody
 * debugs — they assume the chart is wrong.
 *
 * The consequence, stated because it is a real one: the outcome columns are
 * then measured on the **touch** entry, while the screener's hit rates are
 * measured on `next_open` (`GRID_ENTRY_KIND`). Same event, two entries, two
 * outcomes. The column header says which.
 *
 * `is_cluster_head IS NOT FALSE` by default, not `is_cluster_head`: the
 * poller writes NULL because ADR 054's gap window needs the whole session
 * (ADR 119). `$3 = true` drops the filter and shows every row in the
 * cluster.
 */
const EVENTS_SQL = `
  SELECT id, signal_date, signal_type, signal_types_all, signal_strength,
         is_cluster_head, dd_bucket, entry_date, entry_price,
         exit_date, exit_price, exit_reason, holding_days,
         net_ret, mfe, mae, earnings_in_window
    FROM v_events
   WHERE ticker = $1
     AND entry_kind = 'touch'
     AND ($3::boolean IS TRUE OR is_cluster_head IS NOT FALSE)
   ORDER BY signal_date DESC, id
   LIMIT $2
`;

interface EventRowRaw {
  id: string;
  signal_date: Date;
  signal_type: string;
  signal_types_all: string[] | null;
  signal_strength: number | null;
  is_cluster_head: boolean | null;
  dd_bucket: string | null;
  entry_date: Date | null;
  entry_price: string | null;
  exit_date: Date | null;
  exit_price: string | null;
  exit_reason: string | null;
  holding_days: number | null;
  net_ret: string | null;
  mfe: string | null;
  mae: string | null;
  earnings_in_window: boolean | null;
}

export const DEFAULT_EVENT_LIMIT = 40;
export const MAX_EVENT_LIMIT = 200;

export function clampEvents(value: number | undefined): number {
  if (value === undefined || Number.isNaN(value)) return DEFAULT_EVENT_LIMIT;
  return Math.max(1, Math.min(Math.trunc(value), MAX_EVENT_LIMIT));
}

export async function events(
  ticker: string,
  options: { limit?: number; all?: boolean } = {},
): Promise<TickerEvent[]> {
  const rows = await query<EventRowRaw>(EVENTS_SQL, [
    ticker,
    clampEvents(options.limit),
    Boolean(options.all),
  ]);
  return rows.map((r) => ({
    id: Number(r.id),
    signalDate: isoDate(r.signal_date),
    signalType: r.signal_type,
    signalTypesAll: r.signal_types_all ?? [],
    signalStrength: r.signal_strength ?? 0,
    // `v_events` projects no `side` column. `core.signals.detect` never
    // mixes long and short types on one row, so the type determines it
    // exactly, and `sideFor` is the same mapping the screener uses rather
    // than a second one.
    side: sideFor(r.signal_type),
    isClusterHead: r.is_cluster_head,
    ddBucket: r.dd_bucket,
    entryDate: r.entry_date ? isoDate(r.entry_date) : null,
    entryPrice: num(r.entry_price),
    exitDate: r.exit_date ? isoDate(r.exit_date) : null,
    exitPrice: num(r.exit_price),
    exitReason: r.exit_reason,
    holdingDays: r.holding_days,
    netRet: num(r.net_ret),
    mfe: num(r.mfe),
    mae: num(r.mae),
    earningsInWindow: r.earnings_in_window,
  }));
}
