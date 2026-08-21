/**
 * Formatting shared by the screener and the ticker page.
 *
 * Extracted when the second route needed the same seven helpers. A second
 * copy of `vol()` would eventually disagree with the first about where the
 * M/B boundary sits, and two columns of share counts on two pages would
 * round differently for no reason anyone could find.
 */

export const SIGNAL_LABELS: Record<string, string> = {
  bb_lower_touch: "bb lower",
  bb_upper_touch: "bb upper",
  stoch_oversold: "oversold",
  stoch_overbought: "overbought",
  confluence_low: "confluence low",
  confluence_high: "confluence high",
  bear_close_above_upper: "bear close",
  // ADR 144's mirror. Dormant in `enabled_signal_types`, so it cannot reach
  // a row today -- present because the fallback for an unmapped type is the
  // raw enum value, and "bull_close_below_lower" in a 12px cell is how a
  // label gap shows up on the day the type is switched on.
  bull_close_below_lower: "bull close",
};

/** The em-dash placeholder. One spelling, so a missing value never renders
 * as a blank cell that reads like a zero. */
export const NONE = "—";

export function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? NONE : value.toFixed(digits);
}

export function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? NONE : `${(value * 100).toFixed(digits)}%`;
}

/** A signed percentage. Returns carry their direction in the glyph as well
 * as the colour, so the number still reads correctly in a screenshot or for
 * a reader who cannot separate the two hues. */
export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return NONE;
  const s = (value * 100).toFixed(digits);
  return value > 0 ? `+${s}%` : `${s}%`;
}

/**
 * Market capitalisation, abbreviated.
 *
 * Two decimals at T and none below, because the interesting comparison
 * changes scale: among trillion-dollar names the difference between 2.56T
 * and 2.61T is the story, while at 195B nobody is reading the last digit.
 */
export function mcap(value: number | null | undefined): string {
  if (value === null || value === undefined) return NONE;
  if (value >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `${(value / 1e9).toFixed(0)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(0)}M`;
  return String(Math.round(value));
}

/** Volume, abbreviated. A ten-digit share count in a dense row is noise. */
export function vol(value: number | null | undefined): string {
  if (value === null || value === undefined) return NONE;
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
  return String(value);
}

/**
 * A timestamp as market-clock time.
 *
 * ET, not the viewer's zone: the reader is looking at a trading session and
 * "09:35" has to mean the open regardless of where they are sitting.
 */
export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Calendar days between two ISO dates, as a rough "how far behind".
 *
 * Deliberately not trading days: this is a gap between two *artifacts*, not
 * a staleness measure, and the exactness would imply a precision the number
 * does not have. `meta.stalenessDays` is the one counted in sessions.
 */
export function sessionsBetween(from: string | null, to: string | null): string {
  if (!from || !to) return "?";
  const days = Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000,
  );
  return `${days}d`;
}

/**
 * The ticker symbol as it must appear in a query parameter.
 *
 * Upper-cased and stripped to the characters a US symbol can hold. This is
 * not SQL escaping -- every query in this app is parameterised and nothing
 * interpolates -- it is so `/ticker/tsm`, `/ticker/TSM` and a pasted
 * `/ticker/TSM?` all resolve to the same page rather than to an empty one.
 */
export function normalizeSymbol(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9.\-]/g, "").slice(0, 12);
}

/**
 * The screener's URL for a given date, keeping the toggles.
 *
 * **Lives here rather than in `Screener.tsx` because a client component
 * needs it too**, and a function cannot cross the server/client boundary as
 * a prop — React has to serialize props into the RSC payload, and there is
 * no wire form for a closure. Passing `href` to `DatePicker` threw
 * `Functions cannot be passed directly to Client Components` at request
 * time; `next build` compiles it happily, because it is a serialization
 * fault rather than a type error.
 *
 * `null` means the newest date, which is the *absence* of the parameter.
 * Setting it empty would send `?date=` and the route would receive `""`,
 * which is neither a date nor missing.
 */
export function screenHref(
  date: string | null,
  {
    withStats,
    confluenceOnly,
    sort,
    dir,
  }: {
    withStats: boolean;
    confluenceOnly: boolean;
    /** Carried so stepping a date or toggling statistics keeps the sort. */
    sort?: string | null;
    dir?: string;
  },
): string {
  const q = new URLSearchParams();
  if (date) q.set("date", date);
  if (withStats) q.set("stats", "1");
  if (!confluenceOnly) q.set("all", "1");
  // Both or neither. A `dir` with no `sort` names a direction for the
  // default order, which is not a thing the query can honour.
  if (sort) {
    q.set("sort", sort);
    if (dir) q.set("dir", dir);
  }
  const query = q.toString();
  return query ? `/?${query}` : "/";
}
