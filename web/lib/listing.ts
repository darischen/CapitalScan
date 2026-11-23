import { num, query } from "./db";
import { isoDate } from "./screen";

/**
 * `v_listing`: a ticker that has daily bars, whether or not it has
 * indicators yet. The ticker page reads it only when `v_ticker_state` has
 * nothing, so an established name never pays for it.
 */
export interface Listing {
  ticker: string;
  name: string | null;
  sector: string | null;
  exchange: string | null;
  isActive: boolean;
  firstBar: string;
  lastBar: string;
  sessions: number;
  lastClose: number | null;
  /** First close on file, for "since listing". */
  firstClose: number | null;
  hasIndicators: boolean;
}

const LISTING_SQL = `
  SELECT l.ticker, l.name, l.sector, l.exchange, l.is_active,
         l.first_bar, l.last_bar, l.sessions, l.last_close, l.has_indicators,
         (SELECT b.close FROM v_chart b WHERE b.ticker = l.ticker
           ORDER BY b.ts LIMIT 1) AS first_close
    FROM v_listing l
   WHERE l.ticker = $1
`;

interface ListingRaw {
  ticker: string;
  name: string | null;
  sector: string | null;
  exchange: string | null;
  is_active: boolean;
  first_bar: Date;
  last_bar: Date;
  sessions: string;
  last_close: string | null;
  first_close: string | null;
  has_indicators: boolean;
}

export async function listing(ticker: string): Promise<Listing | null> {
  const [r] = await query<ListingRaw>(LISTING_SQL, [ticker]);
  if (!r) return null;
  return {
    ticker: r.ticker,
    name: r.name,
    sector: r.sector,
    exchange: r.exchange,
    isActive: r.is_active,
    firstBar: isoDate(r.first_bar),
    lastBar: isoDate(r.last_bar),
    sessions: Number(r.sessions),
    lastClose: num(r.last_close),
    firstClose: num(r.first_close),
    hasIndicators: r.has_indicators,
  };
}
