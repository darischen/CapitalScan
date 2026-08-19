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
