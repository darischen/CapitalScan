/**
 * Formatting shared by the screener and the ticker page.
 *
 * Extracted when the second route needed the same seven helpers. A second
 * copy of `vol()` would eventually disagree with the first about where the
 * M/B boundary sits, and two columns of share counts on two pages would
 * round differently for no reason anyone could find.
 */

/**
 * Product-facing names for every model output.
 *
 * **The page is read by someone who knows markets, not this codebase.**
 * `p_touch_3` and `q05` are column names in a database; a reader wants
 * "Reaches +3%" and "Worst case". The screener already works this way --
 * `bb_lower` rather than `bb_lower_touch`, "oversold" rather than
 * `stoch_oversold` -- and the inference modal has to match, or the app
 * speaks two languages depending on which panel you open.
 *
 * Kept in one map so a renamed field cannot leave a raw identifier on
 * screen. An unmapped key falls back to the identifier, which is ugly on
 * purpose: it is how a gap gets noticed.
 */
/**
 * Split so the modal can collapse it. `_SUMMARY` is what a reader gets
 * without opening anything, so it carries the actionable half rather than
 * a label like "About this number" — a disclosure whose closed state says
 * nothing is a disclosure nobody opens.
 *
 * The ranking-versus-level claim leads because it is the one measured
 * against live results (2026-09-08, 4,020 resolved predictions): the
 * ordering held across all eight probability bands while the stated
 * percentage fell below the band's own 95% interval in six of them. See
 * RESULTS.md.
 */
export const PREDICTION_CAVEAT_SUMMARY =
  "Use these to rank signals, not as exact odds.";

/** The rest, shown only when the reader opens the disclosure. */
export const PREDICTION_CAVEAT_DETAIL =
  "Measured against live results, the ordering held across every " +
  "probability band, but the stated percentage ran low in six of eight. " +
  "How often any signal reaches +3% has ranged from 37% to 65% month to " +
  "month over the past year, while these numbers are anchored to a 43% " +
  "period, so expect them to understate in a rising market and overstate " +
  "in a falling one. Calibrated on the validate split, which was scored " +
  "repeatedly during model selection, so the interval is a lower bound on " +
  "the true uncertainty, and coverage decays with distance from the " +
  "training window. Advisory only: this is what historically followed " +
  "signals like this one, not what will happen.";

/**
 * Shown instead of the normal caveat when the row is cosmetic (ADR 183).
 *
 * **It leads with the limitation rather than appending it.** The normal
 * caveat says "rank, do not read the level", which still assumes the
 * number means something. This one does not: the model was never fitted on
 * this population, so there is no ranking claim either. Burying that under
 * the usual text would let it read as a footnote on a working number.
 */
export const COSMETIC_CAVEAT_SUMMARY =
  "Reference only. This name is outside the trade universe.";

/** The rest of the cosmetic caveat, shown when the disclosure is opened. */
export const COSMETIC_CAVEAT_DETAIL =
  "The model is fitted only on names inside the trade universe, so it has " +
  "never seen signals like this one and these figures are an " +
  "extrapolation. They are shown so every ticker has something to look " +
  "at, not because they have been checked. Nothing here has been measured " +
  "against what actually happened to names in this state. Treat it as a " +
  "rough reference point and not as evidence.";

/**
 * The whole thing, for any surface that cannot collapse it. Composed from
 * the two above rather than restated, so the halves cannot drift apart.
 */
export const PREDICTION_CAVEAT = `${PREDICTION_CAVEAT_SUMMARY} ${PREDICTION_CAVEAT_DETAIL}`;

/**
 * **The window lives in the label, not in a column header.** A single
 * "within 5 days" header would be wrong for one row in six: `p_touch_10`
 * reads the ten-day head (`TARGETS` in `research/predict.py`), every other
 * field the five-day one. A header states one thing about a column, so a
 * column whose rows disagree cannot have one.
 *
 * "days" rather than "sessions" because that is how a reader thinks about
 * a holding period; the tooltip says trading sessions, which is what they
 * actually are.
 */
export const MODEL_FIELD_LABELS: Record<string, string> = {
  p_touch_2: "Reaches +2% in 5 days",
  p_touch_3: "Reaches +3% in 5 days",
  p_touch_5: "Reaches +5% in 5 days",
  p_touch_10: "Reaches +10% in 10 days",
  // Side-adjusted: "against" is down for a long and up for a short, which
  // is why it does not say "falls".
  p_adverse_3: "Moves 3% against in 5 days",
  p_adverse_5: "Moves 5% against in 5 days",
  // The percentile is in the label, not only in the help text. "Worst
  // case" alone is opaque -- worst of what, and how bad is worst? Naming
  // the level makes the number self-describing in a cell the reader may
  // never hover over.
  q05: "Worst case (5th pct)",
  q25: "Weak case (25th pct)",
  q50: "Midpoint (50th pct)",
  q75: "Strong case (75th pct)",
  q95: "Best case (95th pct)",
  calib_n_eff: "Comparable past signals",
  ci_low: "Range low",
  ci_high: "Range high",
  model_version: "Model version",
};

/**
 * How each model output should be read, in one sentence.
 *
 * Shown beside the number in the modal. A probability with no statement of
 * what it is a probability *of* invites the reader to supply their own
 * meaning, and the most natural wrong guess -- "chance this trade makes
 * money" -- is the one the system must never imply (ADR 001, advisory
 * only).
 */
/**
 * Row help, generated rather than written six times. The threshold and the
 * window are the only things that vary, and hand-writing each invites the
 * drift that left four rows with no tooltip at all.
 */
function touchHelp(threshold: string, sessions: string): string {
  return (
    `How often price has reached ${threshold} in the signal's own ` +
    `direction within ${sessions} sessions, for past signals that looked ` +
    "like this one. Not a forecast that the trade is profitable."
  );
}

function adverseHelp(threshold: string): string {
  return (
    `How often price has moved ${threshold} against the position within ` +
    "five sessions — down for a long, up for a short."
  );
}

export const MODEL_FIELD_HELP: Record<string, string> = {
  // **Every row gets its own help, not just the headline.** Only
  // `p_touch_3` and `p_adverse_3` had text, so four of the six rows
  // offered a tooltip that never appeared -- worse than none, because the
  // reader learns hovering does nothing and stops trying.
  p_touch_2: touchHelp("2%", "five"),
  p_touch_3: touchHelp("3%", "five"),
  p_touch_5: touchHelp("5%", "five"),
  p_touch_10: touchHelp("10%", "ten"),
  // The one row on a different horizon, called out because the label's
  // "10 days" is easy to read as a typo beside five rows saying 5.
  p_adverse_3: adverseHelp("3%"),
  p_adverse_5: adverseHelp("5%"),
  q05: "5 of every 100 comparable signals did worse than this.",
  q25: "25 of every 100 comparable signals did worse than this.",
  q50:
    "Half of comparable signals did better, half worse. Historically " +
    "negative out of sample, so read it as context and not as a direction " +
    "call.",
  q75: "75 of every 100 comparable signals did worse than this.",
  q95: "95 of every 100 comparable signals did worse than this.",
  calib_n_eff:
    "How many independent past signals stand behind this number, after " +
    "correcting for signals that fired together on the same day.",
};

/**
 * The column headers, and the help behind each. A reader asked what
 * "Range" and "Signals" meant, which is the answer: they did not say.
 * "Range" now says what it is a range of, and both carry hover text.
 */
export const MODEL_COLUMN_HELP: Record<string, string> = {
  outcome:
    "What has to happen, and the window it has to happen in. Measured in " +
    "trading sessions from the signal, not calendar days.",
  chance:
    "The share of comparable past signals that did this. A frequency " +
    "already observed, not a forecast.",
  error:
    "The 95% margin of error on that chance. A bigger number means less " +
    "certainty. It is set by how many independent past signals stand " +
    "behind the estimate, so it already tells you how thin the evidence " +
    "is.",
};

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
  return value === null || value === undefined
    ? NONE
    : `${(value * 100).toFixed(digits)}%`;
}

/** A signed percentage. Returns carry their direction in the glyph as well
 * as the colour, so the number still reads correctly in a screenshot or for
 * a reader who cannot separate the two hues. */
export function signedPct(
  value: number | null | undefined,
  digits = 1,
): string {
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

/** A dollar figure with thousands separators and two decimals -- the `$10k`
 * equity curve column, which is read at the cent (`compoundEquity` never
 * rounds between trades, only for this display). */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return NONE;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
 * The zone every timestamp on this site is rendered in, and its label.
 *
 * **Pacific, not Eastern and not the viewer's own zone** (user's decision,
 * 2026-08-27). This read `America/New_York` on the argument that "09:35"
 * should mean the open wherever the reader sits. The reader is one person,
 * sitting in Pacific, and a page showing 11:47 while their clock says 08:47
 * is a subtraction they have to do on every glance.
 *
 * The viewer's own zone is deliberately not used either: the server renders
 * these, so `toLocaleTimeString` without a zone would format in the *Pi's*
 * zone and change meaning if the Pi ever moved.
 *
 * `America/Los_Angeles` handles the PDT/PST switch itself, which is why the
 * label is the zone-agnostic "PT".
 */
export const DISPLAY_TZ = "America/Los_Angeles";
export const DISPLAY_TZ_LABEL = "PT";

/**
 * A timestamp as wall-clock time in `DISPLAY_TZ`.
 *
 * Callers that print the label must use `DISPLAY_TZ_LABEL` rather than
 * writing it out: three tooltips said "ET" in string literals, so changing
 * the zone here alone would have left them confidently wrong.
 */
export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    timeZone: DISPLAY_TZ,
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
export function sessionsBetween(
  from: string | null,
  to: string | null,
): string {
  if (!from || !to) return "?";
  const days = Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) /
      86_400_000,
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
  return raw
    .toUpperCase()
    .replace(/[^A-Z0-9.\-]/g, "")
    .slice(0, 12);
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
