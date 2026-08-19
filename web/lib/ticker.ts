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
  // Short windows first (user's request, 2026-08-19). `5d` is the exit
  // engine's own horizon -- `ExitParams.max_hold_days` -- so it frames a
  // fired signal against exactly the window its outcome was measured over.
  "5d": 5,
  "1m": 21,
  "3m": 63,
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
  /**
   * Today's session, still open (ADR 128). The candle is real OHLCV from
   * the poller's quote, but `close` is the current price rather than a
   * settled one, and **no indicator exists for it** — bands and stochastic
   * are computed on closed bars only, so they stop at yesterday.
   *
   * That is not a gap to fill. Yesterday's bands are exactly what a live
   * signal compares against (invariant 3), so a chart whose bands stop one
   * bar short of a partial candle is showing the comparison the system
   * actually makes.
   */
  partial?: boolean;
}

/**
 * The newest price the poller saw, with the time it saw it.
 *
 * A separate read rather than a column on `v_ticker_state`: that view is
 * the last *closed* session, and joining an intraday price onto it would
 * make one row mean two different times. `null` outside market hours and
 * for any ticker the poller does not cover — it polls
 * `_load_in_trade_tickers`, so a train-universe name never has one.
 */
export interface LiveQuote {
  price: number;
  ts: string;
  /** Sessions between the quote and the state row's bar. `0` means the
   * quote is intraday against today's close-not-yet-written, which is the
   * normal case during a session. */
  aheadOfBar: boolean;
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
  /** The poller recorded this fire and `cscan backtest` has not reached it
   * yet, so it has no measured outcome *yet*. 246 rows when measured. */
  pending: boolean;
  /** ADR 122 keeps out-of-trade events visible here and excludes them from
   * every statistical read, so they carry no outcome and never will. That
   * is a third state, distinct from `pending`, and conflating the two was
   * the first cut of `v_ticker_events` promising a number that was never
   * coming for 179,286 rows. */
  inTrade: boolean | null;
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
 * these are range reads and nothing else.
 *
 * **Two queries, one shape.** `CHART_SQL` takes the newest N sessions;
 * `CHART_BEFORE_SQL` takes the N strictly before a date, which is what the
 * chart asks for when you drag past the left edge (ADR 121). Both count in
 * **sessions**, taken from `trading_days`, never in calendar days:
 * `now() - interval '1 year'` drifts by the holiday count and gives two
 * tickers charts with different numbers of bars.
 */
const CHART_COLUMNS = `
  ts::date AS ts, open, high, low, close, volume,
  bb_lower, bb_mid, bb_upper, k_full, d_full, k_fast, sma_200,
  event_ids, signal_types, sides, signal_strength
`;

const CHART_SQL = `
  SELECT ${CHART_COLUMNS}
    FROM v_chart
   WHERE ticker = $1
     AND ts >= (SELECT min(d) FROM (
                  SELECT d FROM trading_days
                   WHERE d <= market_date()
                   ORDER BY d DESC LIMIT $2
                ) w)
   ORDER BY ts
`;

/**
 * The page before a cursor.
 *
 * `ts::date < $2` is strict, so the bar the caller already holds is not
 * sent twice. Ordered descending with a `LIMIT` so Postgres walks the index
 * backwards from the cursor and stops, then reversed in TypeScript — the
 * chart wants ascending and reversing a 252-element array is free next to
 * sorting the ticker's whole history in the database.
 */
const CHART_BEFORE_SQL = `
  SELECT * FROM (
    SELECT ${CHART_COLUMNS}
      FROM v_chart
     WHERE ticker = $1
       AND ts::date < $2::date
     ORDER BY ts DESC
     LIMIT $3
  ) page
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

/** One mapping, used by both reads, so a column added to one cannot be
 * missing from the other. */
function toBar(r: ChartRowRaw): ChartBar {
  return {
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
  };
}

/**
 * Today's partial candle (ADR 128).
 *
 * **Read separately and appended, rather than unioned into `v_chart`.**
 * The view joins `indicators`, so a row with no indicator row would arrive
 * with null bands that look like a data gap rather than an open session.
 * Keeping the join out of SQL also keeps `bars_live` invisible to anything
 * that computes an indicator, which is the entire reason it is a separate
 * table.
 *
 * Empty outside a session, and for any ticker the poller does not cover —
 * it polls `_load_in_trade_tickers`, so a train-universe name never has
 * one.
 */
const LIVE_BAR_SQL = `
  SELECT session_date, ts, open, high, low, close, volume
    FROM bars_live
   WHERE ticker = $1
     AND session_date = market_date()
`;

export interface LiveRow {
  session_date: Date;
  ts: Date;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
  volume: string | null;
}

/**
 * The one row that describes the open session, read once.
 *
 * Both the chart's candle and the header's live price are built from this,
 * and that is the point — see `liveQuote` below for what happened when
 * they came from two tables.
 */
export async function liveRowFor(ticker: string): Promise<LiveRow | null> {
  const rows = await query<LiveRow>(LIVE_BAR_SQL, [ticker]);
  return rows[0] ?? null;
}

/**
 * Today's partial candle, or null outside a session.
 *
 * **Its own exported function because the chart has to be able to re-read
 * it.** The page is a server component: it renders once, and the candle it
 * renders is whatever `bars_live` held at that instant. The poller rewrites
 * that row every five minutes, so a tab left open showed a price frozen at
 * page load — correct when it was drawn and progressively wronger all
 * session, with nothing on screen saying which. `/api/live` calls this on a
 * timer so the same shape reaches the chart on the first paint and on every
 * refresh after it.
 */
export async function liveBar(ticker: string): Promise<ChartBar | null> {
  return toLiveBar(await liveRowFor(ticker));
}

/** The candle shape, from the row. Pure, so a test can feed it one. */
export function toLiveBar(today: LiveRow | null): ChartBar | null {
  if (!today) return null;

  return {
    ts: isoDate(today.session_date),
    open: num(today.open),
    high: num(today.high),
    low: num(today.low),
    close: num(today.close),
    volume: num(today.volume),
    // Null, never carried forward from yesterday. Indicators are
    // computed on closed bars; inventing them here would be the
    // look-ahead this design exists to prevent, one layer up.
    bbLower: null,
    bbMid: null,
    bbUpper: null,
    kFull: null,
    dFull: null,
    kFast: null,
    sma200: null,
    eventIds: [],
    signalTypes: [],
    sides: [],
    signalStrength: null,
    partial: true,
  };
}

export async function chart(ticker: string, range: Range = DEFAULT_RANGE): Promise<ChartBar[]> {
  const [rows, today] = await Promise.all([
    query<ChartRowRaw>(CHART_SQL, [ticker, RANGES[range]]),
    liveBar(ticker),
  ]);

  const bars = rows.map(toBar);
  if (!today) return bars;

  // Across the nightly handover both exist: the ingest has written today's
  // closed bar and `bars_live` still holds the session's partial one. The
  // closed bar wins — it is settled and it carries indicators.
  if (bars.some((b) => b.ts === today.ts)) return bars;

  return [...bars, today];
}

/** How many sessions one drag-left fetches. A year, matching the default
 * window, so one gesture never lands mid-page. */
export const PAGE_SESSIONS = 252;
export const MAX_PAGE_SESSIONS = 1260;

export function clampPage(value: number | undefined): number {
  if (value === undefined || Number.isNaN(value)) return PAGE_SESSIONS;
  return Math.max(1, Math.min(Math.trunc(value), MAX_PAGE_SESSIONS));
}

/**
 * The sessions immediately before `before`, oldest first.
 *
 * Returns `[]` at the start of the ticker's history, which is how the
 * caller knows to stop asking (ADR 121).
 */
export async function chartBefore(
  ticker: string,
  before: string,
  limit?: number,
): Promise<ChartBar[]> {
  return (
    await query<ChartRowRaw>(CHART_BEFORE_SQL, [ticker, before, clampPage(limit)])
  ).map(toBar);
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
 * **Reads `v_ticker_events`, which is `next_open` plus the poller's
 * not-yet-backtested fires.** This read `entry_kind = 'touch'` until
 * 2026-08-19 and showed a blank entry and exit on 47% of its rows,
 * permanently: a `touch` entry means "enter at the level you touched", and
 * a stochastic threshold crossing has no level, so all 19,218
 * `stoch_oversold` and `stoch_overbought` rows carried no entry price and
 * no exit. The user reported it as old events with the horizon long past
 * still reading `— — —`, which is exactly what it looked like.
 *
 * The outcomes now also match the grain the screener's hit rates were
 * measured on (`GRID_ENTRY_KIND`), closing the "same event, two entries,
 * two outcomes" wart this comment used to apologise for.
 *
 * `pending` marks a fire `cscan backtest` has not reached. It renders as
 * "open", never as a blank — a blank is what the defect above looked like.
 *
 * **Confluence by default** (user's request, 2026-08-19), matching the
 * screener's own default and `cli.py::scan --confluence-only`: membership
 * of `confluence_low` or `confluence_high` in **`signal_types_all`**, not
 * in `signal_type`. The latter carries only the most specific type per ADR
 * 057, so a bar that fired both `bear_close_above_upper` and
 * `confluence_high` stores the reversal and would be dropped by a filter on
 * the single column — the row a short-side reader most wants.
 *
 * **No reversal requirement.** ADR 111's `--actionable` makes a short
 * actionable only with a confirming bear reversal; this filter deliberately
 * does not, so every confluence shows and the reader judges. That is the
 * same call ADR 117 made for the poller.
 *
 * `$3 = true` drops the filter and shows every fire, cluster heads and
 * repeats alike. Neither mode filters on `is_cluster_head` any more: the
 * page is for looking at what happened, and a cluster's second and third
 * days are things that happened.
 */
const EVENTS_SQL = `
  SELECT id, signal_date, signal_type, signal_types_all, signal_strength,
         is_cluster_head, dd_bucket, pending, in_trade, entry_date, entry_price,
         exit_date, exit_price, exit_reason, holding_days,
         net_ret, mfe, mae, earnings_in_window
    FROM v_ticker_events
   WHERE ticker = $1
     AND ($3::boolean IS TRUE
          OR signal_types_all && ARRAY['confluence_high','confluence_low'])
   ORDER BY signal_date DESC, id
   LIMIT $2 OFFSET $4
`;

/** The row count behind the page, so the reader is told how deep the list
 * goes rather than discovering it by scrolling. */
const EVENTS_COUNT_SQL = `
  SELECT count(*)::int AS n
    FROM v_ticker_events
   WHERE ticker = $1
     AND ($2::boolean IS TRUE
          OR signal_types_all && ARRAY['confluence_high','confluence_low'])
`;

interface EventRowRaw {
  id: string;
  signal_date: Date;
  signal_type: string;
  signal_types_all: string[] | null;
  signal_strength: number | null;
  is_cluster_head: boolean | null;
  dd_bucket: string | null;
  pending: boolean;
  in_trade: boolean | null;
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

/**
 * The scroll page for the history table.
 *
 * It lives here rather than in the route because a Next route module may
 * only export the handler names — `export const EVENT_PAGE` in
 * `app/api/events/route.ts` fails the build with a type error about
 * `OmitWithTag`, which does not mention the actual rule. Both the route and
 * the component import it from one place, so the server's "done" answer and
 * the client's expectations cannot drift.
 *
 * Fifty, not the chart's 252: a table row is read, not panned past, and
 * fifty is about two screens.
 */
export const EVENT_PAGE = 50;

export function clampEvents(value: number | undefined): number {
  if (value === undefined || Number.isNaN(value)) return DEFAULT_EVENT_LIMIT;
  return Math.max(1, Math.min(Math.trunc(value), MAX_EVENT_LIMIT));
}

/**
 * `OFFSET` paging, not a keyset cursor.
 *
 * A keyset would be `(signal_date, id) < (lastDate, lastId)` and is the
 * right answer on a table where the offset can reach the millions. It
 * cannot here: this is one ticker's own history, and the deepest in the
 * database is a few hundred rows. `OFFSET 200` on an index-ordered scan of
 * 272 rows costs nothing, and the two-column cursor costs a tuple
 * comparison in the SQL and in the client on every request.
 */
export async function events(
  ticker: string,
  options: { limit?: number; all?: boolean; offset?: number } = {},
): Promise<TickerEvent[]> {
  const rows = await query<EventRowRaw>(EVENTS_SQL, [
    ticker,
    clampEvents(options.limit),
    Boolean(options.all),
    Math.max(0, Math.trunc(options.offset ?? 0)),
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
    pending: r.pending,
    inTrade: r.in_trade,
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

export async function countEvents(ticker: string, all = false): Promise<number> {
  const [row] = await query<{ n: number }>(EVENTS_COUNT_SQL, [ticker, all]);
  return row?.n ?? 0;
}

/* --- ticker search ------------------------------------------------------ */

export interface TickerHit {
  ticker: string;
  name: string | null;
  sector: string | null;
  mcapUsd: number | null;
  inTrade: boolean | null;
}

/**
 * Type-ahead over `v_universe` — 622 names, **including the ones outside
 * the trade universe**, because the ticker page is where you go to look at
 * a name you are not trading.
 *
 * **Ranked by prefix, then by size.** Three tiers: the symbol starts with
 * what you typed, then the company name starts with it, then either
 * contains it. Within a tier, market cap descending. Typing `A` should
 * surface AAPL and AMZN, not AAL and ABNB, and alphabetical order gives you
 * the second list. `mcap_usd` is present on 620 of 622 rows; the two
 * without sort last rather than first.
 *
 * `position(... in ...)` rather than `LIKE $1 || '%'` so the tier and the
 * filter come from one expression and cannot disagree about what a match
 * is.
 */
const SEARCH_SQL = `
  SELECT ticker, name, sector, mcap_usd, in_trade
    FROM v_universe
   WHERE ticker ILIKE '%' || $1 || '%'
      OR name ILIKE '%' || $1 || '%'
   ORDER BY CASE
              WHEN position(upper($1) in upper(ticker)) = 1 THEN 0
              WHEN position(upper($1) in upper(coalesce(name, ''))) = 1 THEN 1
              ELSE 2
            END,
            mcap_usd DESC NULLS LAST,
            ticker
   LIMIT $2
`;

export const SEARCH_LIMIT = 6;

export async function searchTickers(term: string, limit = SEARCH_LIMIT): Promise<TickerHit[]> {
  // An empty term matches everything, which would return the six largest
  // companies to someone who has typed nothing. The caller shows no menu
  // in that state; this makes it impossible rather than conventional.
  const q = term.trim();
  if (q === "") return [];

  const rows = await query<{
    ticker: string;
    name: string | null;
    sector: string | null;
    mcap_usd: string | null;
    in_trade: boolean | null;
  }>(SEARCH_SQL, [q, Math.max(1, Math.min(Math.trunc(limit), 20))]);

  return rows.map((r) => ({
    ticker: r.ticker,
    name: r.name,
    sector: r.sector,
    mcapUsd: num(r.mcap_usd),
    inTrade: r.in_trade,
  }));
}

/**
 * The header's live price — `bars_live`, the same row the candle is drawn
 * from.
 *
 * **This read `quotes_live` until 2026-08-19 and was wrong most of the
 * time.** `poll.py` writes `quotes_live` *inside the breach loop*, so a
 * ticker gets a row only on a tick where it fired. ROST fired at 09:36 ET
 * and never again; at 13:26 PT, hours after the close, the header still
 * read 238.72 from that morning quote while the candle beside it showed
 * the true 234.63. Reported by the user, and the two numbers on one screen
 * disagreeing is precisely the failure `/api/live` was built to prevent —
 * except the disagreement was upstream, in which table each came from.
 *
 * `bars_live.close` *is* the current price: rewritten every tick, for every
 * ticker the poller covers (ADR 128). `quotes_live` was a second, sparser
 * source for the same quantity, so it stops being a display source rather
 * than being kept in sync. It remains the record of what price a signal
 * fired at, which is what it is actually for.
 *
 * Both shapes now come from one row, so they cannot disagree by
 * construction rather than by being fetched carefully.
 */
export async function liveQuote(ticker: string, barDate: string): Promise<LiveQuote | null> {
  return toLiveQuote(await liveRowFor(ticker), barDate);
}

/** The quote shape, from the row. Pure, so a test can feed it one. */
export function toLiveQuote(row: LiveRow | null, barDate: string): LiveQuote | null {
  if (!row) return null;
  // `close` is `NOT NULL` in the table, so this is a defence against a
  // future nullable column rather than a case that happens.
  const price = num(row.close);
  if (price === null) return null;

  // The SQL already pins `session_date = market_date()`, so a row that
  // exists is today's by construction. The date comparison the old
  // `quotes_live` read needed is gone with the table it guarded.
  const sessionDate = isoDate(row.session_date);
  return { price, ts: row.ts.toISOString(), aheadOfBar: sessionDate > barDate };
}
