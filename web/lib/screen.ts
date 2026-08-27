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
  /**
   * The poller's **intraday** reversal judgement (ADR 117, surfaced by
   * ADR 123), or `null` when no quote has been evaluated for this event.
   *
   * Deliberately not the same thing as `bear_close_above_upper` in
   * `signalTypesAll`. That one is close-confirmed and only exists after
   * that night's `cscan events`; this one says where price is sitting
   * right now relative to today's open, and only exists while the poller
   * runs.
   */
  reversal: Reversal | null;
  cellId: string | null;
  /**
   * `null` while the poller has written the row and clustering has not run.
   * ADR 054's gap window needs the whole session, so an intraday row cannot
   * know yet whether it is a cluster head.
   */
  isClusterHead: boolean | null;
  /**
   * The ADR 149 watch universe: this name failed a trade criterion and was
   * admitted to watch instead. It still fires, is still priced, and is
   * **never** part of a statistical population -- `stats` is always
   * `Suppressed` with reason `watch_universe` on these rows.
   *
   * Stamped on the event at creation, not looked up now, so a fire from
   * 2019 reports why it was watched *then*.
   */
  inWatch: boolean;
  /**
   * Which route admitted it, or `null` on a trade-universe row.
   * `WATCH_REASON_LABEL` turns it into the display string.
   */
  watchReason: WatchReason | null;
  /** Populated only when `withStats` is requested. See ADR 114. */
  stats: CellStats | Suppressed | null;
}

/**
 * `universe.watch_reason`. Two routes today (ADR 149); the union is closed
 * so a third added to the enum fails the build here rather than rendering
 * as a raw slug on the screener.
 */
export type WatchReason = "pullback" | "history";

/**
 * Display copy, not the enum. A reader should not have to know that
 * `pullback` means "below its 200-day average".
 */
export const WATCH_REASON_LABEL: Record<WatchReason, string> = {
  pullback: "Watch Universe: Below 200-Day SMA",
  history: "Watch Universe: Insufficient History",
};

/** Falls back to a generic label rather than throwing on an unknown slug. */
export function watchLabel(reason: WatchReason | string | null): string {
  if (!reason) return "Watch Universe";
  return WATCH_REASON_LABEL[reason as WatchReason] ?? "Watch Universe";
}

export interface Reversal {
  /** Price is above the band **and** below today's open. */
  confirmed: boolean;
  /** Price is above the band at all. A confluence with `aboveBand` false
   * has fallen back inside since it fired. */
  aboveBand: boolean;
  /**
   * Distance from today's open in ATR units. **Negative is below the open**
   * and therefore reversing; positive is not.
   *
   * The number is the point: ADR 117 chose to show every confluence and say
   * how far each is from confirming, rather than hiding the near-misses.
   */
  openGapAtr: number | null;
  /** The quote this judgement was made on. */
  ts: string;
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
  headsOnly: boolean;
  /** What the header row should mark as active. `null` is the default order. */
  sort: SortKey | null;
  dir: SortDir;
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
  /**
   * The dates on either side that actually have rows, for the previous/next
   * links (user's request, 2026-08-19).
   *
   * **Dates with events, not adjacent trading days.** Most sessions fire
   * nothing, so stepping by calendar or by `trading_days` would walk a
   * reader through a run of empty screens to reach the next one that is
   * not. `null` at either end of the history.
   */
  prevDate: string | null;
  nextDate: string | null;
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

/**
 * The ceiling for an explicitly requested `?limit=`.
 *
 * **ADR 074's cap is the MCP tool surface, not this page.** That ADR closes
 * the seven tools' enums and caps their `limit` argument at 200; ADR 118
 * makes `/` a different surface that selects from the views directly. The
 * constant is shared here only because the number is a sensible ceiling for
 * a hand-typed parameter.
 */
export const MAX_LIMIT = 200;

/**
 * **The screener shows every row for the date** (user's request,
 * 2026-08-20). `null` means no `LIMIT` clause at all.
 *
 * The 50-row default this replaced was cutting real days. Measured across
 * 4,108 trading days: the largest is 134 rows, the mean 34, the 95th
 * percentile 79. So the default truncated roughly one day in twenty while
 * the page said `Showing 50 of 85`, and a reader sorting by any column
 * would have been sorting a slice chosen by a different ordering.
 *
 * A caller may still ask for fewer with `?limit=`, clamped to `MAX_LIMIT`.
 */
export function clampLimit(value: number | undefined): number | null {
  if (value === undefined || Number.isNaN(value)) return null;
  return Math.max(1, Math.min(Math.trunc(value), MAX_LIMIT));
}

/**
 * Signal ordering for the `signal` sort key (user's specification,
 * 2026-08-20): confluence above single conditions, band above stochastic,
 * and the short side above the long side within each pair.
 *
 * `bear_close_above_upper` is not in the user's list and takes rank 0. It
 * is the most specific type ADR 057 assigns, and ADR 111 makes the
 * close-confirmed reversal *the* actionable short condition — so a row
 * carrying it as its `signal_type` is the strongest short signal on the
 * page, and "high above low" puts it above `confluence_high` rather than
 * below anything.
 */
export const SIGNAL_ORDER = [
  "bear_close_above_upper",
  "confluence_high",
  "confluence_low",
  "bb_upper_touch",
  "bb_lower_touch",
  "stoch_overbought",
  "stoch_oversold",
] as const;

const SIGNAL_RANK = `CASE s.signal_type
${SIGNAL_ORDER.map((t, i) => `  WHEN '${t}' THEN ${i}`).join("\n")}
  ELSE ${SIGNAL_ORDER.length} END`;

/**
 * Every column the header row can sort by, mapped to the SQL that orders it.
 *
 * **A closed map, and the only thing a request can choose is a key of it.**
 * `sortKey` below rejects anything else, so no caller-supplied text reaches
 * the query — the interpolation in `feedSql` is one of these seven strings
 * and nothing else. That is the same closed-enum discipline ADR 074 applies
 * to the tools, for the same reason.
 */
const SORT_COLUMNS = {
  ticker: "s.ticker",
  signal: SIGNAL_RANK,
  strength: "s.signal_strength",
  bollinger: "s.bb_lower",
  // The raw %K, because ADR 110 makes it the trigger and the smoothed one
  // only has to agree with it.
  stochastic: "s.k_fast",
  open: "s.open",
  high: "s.high",
  low: "s.low",
  close: "s.close",
  volume: "s.volume",
  live: "s.live_price",
  fired: "s.fired_at",
} as const;

export type SortKey = keyof typeof SORT_COLUMNS;
export type SortDir = "asc" | "desc";

/**
 * The direction a column takes on its *first* click, chosen so one click
 * answers the question the column is usually asked.
 *
 * Prices, volume, strength and recency are read largest-first; a name is
 * read A to Z; the signal rank and the stochastic are read from their most
 * specific and most oversold ends, which are the low values.
 */
const FIRST_DIR: Record<SortKey, SortDir> = {
  ticker: "asc",
  signal: "asc",
  strength: "desc",
  bollinger: "asc",
  stochastic: "asc",
  open: "desc",
  high: "desc",
  low: "desc",
  close: "desc",
  volume: "desc",
  live: "desc",
  fired: "desc",
};

export function isSortKey(value: string | undefined): value is SortKey {
  return (
    value !== undefined &&
    Object.prototype.hasOwnProperty.call(SORT_COLUMNS, value)
  );
}

/** The direction a header link should request. Flips only the active one. */
export function nextDir(
  key: SortKey,
  active: SortKey | null,
  dir: SortDir,
): SortDir {
  if (key !== active) return FIRST_DIR[key];
  return dir === "asc" ? "desc" : "asc";
}

export function defaultDir(key: SortKey): SortDir {
  return FIRST_DIR[key];
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
  /**
   * Show only cluster heads. **Off by default** (user's decision,
   * 2026-08-19): the feed is a record of what the poller saw that day, and
   * ADR 054's clustering is a statistical device, not a display one.
   *
   * It used to be on, inside the view, and it made rows *disappear*. The
   * poller cannot cluster, so its rows carry NULL and all of them showed
   * live; overnight `cscan events` clustered and the repeats became false.
   * Measured on 2026-08-06: 19 confluence fires live, 4 the next morning.
   */
  headsOnly?: boolean;
  limit?: number;
  /** A key of `SORT_COLUMNS`, already validated by `isSortKey`. */
  sort?: SortKey | null;
  dir?: SortDir;
}

/**
 * The feed's domain, restated from `v_screen_live`'s own WHERE clause.
 *
 * Every *date* question reads `events` directly rather than the view. The
 * view carries four `LEFT JOIN LATERAL` subqueries and Postgres cannot drop
 * an unused one, so asking it "what is the newest date" runs all four over
 * the whole history. Measured after ADR 122's rebuild took the live `touch`
 * slice from 157k to 1.31M rows: **23.3 s** through the view, **0.17 ms**
 * against the table, with `events_feed_latest` serving it.
 *
 * The duplication is deliberate. These ask a question *about* the feed's
 * domain rather than reading the feed, and paying four laterals per row to
 * learn a date is the wrong trade.
 */
const FEED_DOMAIN = `
  entry_kind = 'touch'
  AND (in_trade OR in_watch)
  AND config_hash = current_setting('capitalscan.default_config_hash', true)
`;

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
/*
 * `ORDER BY ... LIMIT 1`, not `max()`.
 *
 * Same answer, and measured 2026-08-19 it is **265 ms against 871 ms**.
 * `v_screen_live` carries four `LEFT JOIN LATERAL` subqueries, and
 * Postgres does not drop an unused one, so `max()` runs all four over every
 * row in the view before aggregating. `LIMIT 1` lets it stop.
 *
 * 265 ms is still most of the screener's load, and the remainder is the
 * same laterals. Recorded in RESULTS.md rather than fixed here: the fix is
 * an index or a narrower view, which is a migration and a decision, not a
 * query rewrite.
 */
const LATEST_DATE_SQL = `
  SELECT max(signal_date) AS d FROM events
   WHERE ${FEED_DOMAIN}
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
/**
 * The nearest date each way that has rows under the same filters.
 *
 * Takes the confluence flag so the arrows agree with the table: with
 * confluence-only on, "previous" must mean the previous day that had a
 * confluence, or a click lands on an empty screen.
 */
const NEIGHBOURS_SQL = `
  SELECT
    (SELECT max(signal_date) FROM events
      WHERE ${FEED_DOMAIN}
        AND signal_date < $1::date
        AND ($2::boolean IS NOT TRUE
             OR signal_types_all && ARRAY['confluence_high','confluence_low'])) AS prev,
    (SELECT min(signal_date) FROM events
      WHERE ${FEED_DOMAIN}
        AND signal_date > $1::date
        AND ($2::boolean IS NOT TRUE
             OR signal_types_all && ARRAY['confluence_high','confluence_low'])) AS next
`;

const CONFLUENCE_RANK = `
  CASE WHEN 'confluence_high' = ANY(s.signal_types_all) THEN 0
       WHEN 'confluence_low'  = ANY(s.signal_types_all) THEN 1
       ELSE 2 END
`;

const CONFLUENCE_FILTER = `
  AND ($3::boolean IS NOT TRUE
       OR s.signal_types_all && ARRAY['confluence_high','confluence_low'])
`;

/**
 * The default order, unchanged from before sorting existed: confluence
 * first, then newest, then alphabetical. A page with no `?sort=` is
 * byte-identical to what it rendered before this feature.
 *
 * Declared here rather than beside `SORT_COLUMNS` because it reads
 * `CONFLUENCE_RANK`, and a `const` cannot be referenced above its own
 * initialiser.
 */
const DEFAULT_ORDER = `${CONFLUENCE_RANK}, s.fired_at DESC NULLS LAST, s.ticker`;

/**
 * `ORDER BY` for one sort key.
 *
 * **`NULLS LAST` in both directions, deliberately.** Postgres puts nulls
 * first on `DESC`, which would open a descending sort with the rows that
 * have no value — and an absent close is not a large close. `s.ticker` is
 * the tiebreaker in every case, so equal values never reorder between two
 * renders of the same data.
 */
function orderBy(key: SortKey | null, dir: SortDir): string {
  if (key === null) return DEFAULT_ORDER;
  return `${SORT_COLUMNS[key]} ${dir === "asc" ? "ASC" : "DESC"} NULLS LAST, s.ticker`;
}

/**
 * **No `v_universe` join, and no cluster-head filter** (ADR 124).
 *
 * The join was redundant once ADR 122 put point-in-time `in_trade` on the
 * event, and worse than redundant: `v_universe` is `DISTINCT ON (ticker)
 * ORDER BY as_of DESC`, i.e. membership *now*, so it silently dropped a
 * historical row whose ticker had left the universe since. The view's own
 * predicate answers the right question — was it tradeable *on that bar*.
 *
 * The cluster filter is gone because the feed is a record of what the
 * poller saw (user's decision, 2026-08-19). `is_cluster_head` is still
 * projected and repeats are marked, so nothing is hidden and nothing is
 * unlabelled.
 */
/**
 * The feed, ordered by one of `SORT_COLUMNS` or by the default.
 *
 * **`ORDER` is interpolated; nothing a caller sends is.** `orderBy` returns
 * either `DEFAULT_ORDER` or a string built from `SORT_COLUMNS`, and the only
 * thing a request decides is which key of that closed map to use — `isSortKey`
 * rejects everything else before this is reached. `$2` is `null` when no
 * limit was asked for, which Postgres reads as `LIMIT ALL`.
 */
const feedSql = (order: string) => `
  SELECT s.ticker, s.signal_date, s.signal_type, s.signal_types_all,
         s.signal_strength, s.k_full, s.k_fast,
         s.dd_bucket, s.sector, s.cell_id, s.side, s.is_cluster_head,
         s.bb_lower, s.bb_mid, s.bb_upper, s.band_ts::date AS band_ts,
         s.open, s.high, s.low, s.close, s.volume,
         s.live_price, s.live_price_ts, s.fired_at,
         s.rev_confirmed, s.rev_above_band, s.rev_open_gap_atr, s.rev_ts,
         s.in_watch, s.watch_reason
    FROM v_screen_live s
   WHERE s.signal_date = $1::date
     AND ($4::boolean IS NOT TRUE OR s.is_cluster_head IS NOT FALSE)
     ${CONFLUENCE_FILTER}
   ORDER BY ${order}
   LIMIT $2
`;

const COUNT_SQL = `
  SELECT count(*)::int AS n
    FROM v_screen_live s
   WHERE s.signal_date = $1::date
     AND ($3::boolean IS NOT TRUE OR s.is_cluster_head IS NOT FALSE)
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
  rev_confirmed: boolean | null;
  rev_above_band: boolean | null;
  rev_open_gap_atr: string | null;
  rev_ts: Date | null;
  in_watch: boolean | null;
  watch_reason: string | null;
}

export async function screen(
  options: ScreenOptions = {},
): Promise<ScreenResult> {
  const limit = clampLimit(options.limit);
  const sort = options.sort ?? null;
  const dir: SortDir =
    options.dir ?? (sort === null ? "desc" : defaultDir(sort));
  // Default on. `?all=1` clears it, so nothing is unreachable.
  const confluenceOnly = options.confluenceOnly !== false;
  const headsOnly = options.headsOnly === true;

  // Resolved before the feed runs, so the rows and the count describe the
  // same day. `null` here means the view is empty, which the caller renders
  // as the empty state rather than as a failure.
  const date =
    options.date ??
    (await query<{ d: Date | null }>(LATEST_DATE_SQL))[0]?.d ??
    null;
  if (date === null) {
    return {
      rows: [],
      totalMatched: 0,
      withStats: false,
      confluenceOnly,
      headsOnly,
      sort,
      dir,
      signalDate: null,
      prevDate: null,
      nextDate: null,
      meta: await readMeta(),
    };
  }

  const [rowsRaw, countRows, neighbours, meta] = await Promise.all([
    query<FeedRowRaw>(feedSql(orderBy(sort, dir)), [
      date,
      limit,
      confluenceOnly,
      headsOnly,
    ]),
    query<{ n: number }>(COUNT_SQL, [date, confluenceOnly, headsOnly]),
    query<{ prev: Date | null; next: Date | null }>(NEIGHBOURS_SQL, [
      date,
      confluenceOnly,
    ]),
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
    side: (r.side === "short" || r.side === "long"
      ? r.side
      : sideFor(r.signal_type)) as Side,
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
    // `rev_ts` decides presence, not `rev_confirmed`. A false `confirmed`
    // is a real judgement -- "evaluated, not reversing" -- and collapsing
    // it into `null` would lose exactly the near-miss ADR 117 exists to
    // show.
    reversal: r.rev_ts
      ? {
          confirmed: r.rev_confirmed === true,
          aboveBand: r.rev_above_band === true,
          openGapAtr: num(r.rev_open_gap_atr),
          ts: r.rev_ts.toISOString(),
        }
      : null,
    ddBucket: r.dd_bucket,
    cellId: r.cell_id,
    isClusterHead: r.is_cluster_head,
    // `=== true` rather than a cast: the column is nullable, and a NULL
    // here means "not a watch row", never "unknown".
    inWatch: r.in_watch === true,
    watchReason: (r.watch_reason as WatchReason | null) ?? null,
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
    sort,
    dir,
    headsOnly,
    signalDate: typeof date === "string" ? date : isoDate(date),
    prevDate: neighbours[0]?.prev ? isoDate(neighbours[0].prev) : null,
    nextDate: neighbours[0]?.next ? isoDate(neighbours[0].next) : null,
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
  const ids = [
    ...new Set(
      rows.map((r) => r.cellId).filter((v): v is string => Boolean(v)),
    ),
  ];
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

/**
 * `v_screen_live`, the same view the feed reads.
 *
 * It said `v_screen` until 2026-08-19, which made the empty state answer a
 * different question from the page it appears on: `v_screen` filters
 * `entry_kind = 'next_open'` and trails the last backtest, so on a quiet
 * day it would have reported "last fire 2026-08-13" while the feed's own
 * view held events from 2026-08-18. The one line on the page whose job is
 * to tell you when something last happened would have been the one line
 * that was wrong.
 */
const LAST_FIRE_SQL = `
  SELECT ticker, signal_date
    FROM v_screen_live
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
export async function lastFire(): Promise<{
  ticker: string;
  signalDate: string;
} | null> {
  const [row] = await query<{ ticker: string; signal_date: Date }>(
    LAST_FIRE_SQL,
  );
  return row
    ? { ticker: row.ticker, signalDate: isoDate(row.signal_date) }
    : null;
}

/**
 * `pg` returns `date` as a JS Date at local midnight; take the calendar day.
 *
 * **Only ever pass it a `date`.** A `timestamptz` is a different animal and
 * this function is wrong on one: `bars.ts` is stored at UTC midnight, so
 * `pg` builds a Date whose *local* getters read the previous day anywhere
 * west of Greenwich. Measured 2026-08-19 in Pacific — `2026-08-18T00:00:00Z`
 * came back as `2026-08-17`, so the ticker chart drew every bar one session
 * left of where it belonged and every marker with it, while the event
 * history beside it read `signal_date` (a real `date`) and was correct. Two
 * panels, one day apart, no error anywhere.
 *
 * The fix is in the SQL, not here: `v_chart.ts`, `v_ticker_state.as_of` and
 * `v_screen_live.band_ts` are all selected `::date`. Casting in the query
 * means the wrong type cannot reach this function, where the two cases are
 * genuinely indistinguishable — a `date` needs local getters and a
 * `timestamptz` needs UTC ones, and a `Date` object remembers neither.
 */
export function isoDate(value: Date | string): string {
  if (typeof value === "string") return value.slice(0, 10);
  const y = value.getFullYear();
  const m = String(value.getMonth() + 1).padStart(2, "0");
  const d = String(value.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/* --- the calendar ------------------------------------------------------- */

/**
 * Every date in a month that has rows, with how many.
 *
 * **Counts, not just dates.** A calendar that only knew which days were
 * clickable would still make you click one to find out whether it was worth
 * it. The count turns the picker into a density map of the month, which is
 * the actual question — "when did things fire" — rather than a date entry
 * widget.
 *
 * Takes the confluence flag for the same reason the arrows do: a day the
 * calendar calls populated must be populated under the filters the reader
 * is currently using, or clicking it lands on an empty screen.
 */
const MONTH_SQL = `
  SELECT signal_date AS d, count(*)::int AS n
    FROM events
   WHERE ${FEED_DOMAIN}
     AND signal_date >= $1::date
     AND signal_date < ($1::date + interval '1 month')
     AND ($2::boolean IS NOT TRUE
          OR signal_types_all && ARRAY['confluence_high','confluence_low'])
   GROUP BY 1
   ORDER BY 1
`;

export interface DayCount {
  date: string;
  n: number;
}

/** `month` is any date inside it; the query floors to the first. */
export async function monthCounts(
  month: string,
  confluenceOnly = true,
): Promise<DayCount[]> {
  const rows = await query<{ d: Date; n: number }>(MONTH_SQL, [
    month,
    confluenceOnly,
  ]);
  return rows.map((r) => ({ date: isoDate(r.d), n: r.n }));
}

/**
 * The first and last dates the feed has anything for, so the calendar can
 * stop offering months that cannot contain a row.
 */
const SPAN_SQL = `
  SELECT min(signal_date) AS lo, max(signal_date) AS hi FROM events
   WHERE ${FEED_DOMAIN}
`;

export async function feedSpan(): Promise<{ lo: string; hi: string } | null> {
  const [row] = await query<{ lo: Date | null; hi: Date | null }>(SPAN_SQL);
  if (!row?.lo || !row?.hi) return null;
  return { lo: isoDate(row.lo), hi: isoDate(row.hi) };
}
