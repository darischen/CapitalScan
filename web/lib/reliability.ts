import { query } from "./db";

/**
 * The reliability table, computed from the forward log (ADR 182).
 *
 * **This is the evidence gate, not a chart.** Phase 6's third gate used to
 * read "reliability diagram renders", which a picture satisfies without
 * anyone checking what it shows. What it shows, measured 2026-09-08 across
 * 4,020 resolved in-population predictions, is that the ordering holds
 * across every band while the *shipped* probability falls outside the
 * band's own 95% interval in six of eight — always low. So the restated
 * gate requires that fact to be visible, and `inside` below is what makes
 * it so.
 *
 * **Only `model_scored` rows.** ADR 180: a prediction for a signal type
 * the model was never fitted on is extrapolation, and mixing those in
 * would measure the calibration of two different populations at once.
 */
export interface ReliabilityBucket {
  index: number;
  /** Range of shipped probability that falls in this bucket. */
  lo: number;
  hi: number;
  /** Mean shipped probability, and how often it actually happened. */
  predicted: number;
  actual: number;
  /** 95% Wilson interval on `actual`. */
  ciLow: number;
  ciHigh: number;
  n: number;
  /**
   * Whether the shipped probability lies inside the realised rate's own
   * interval. **`false` is the finding**, not an error state.
   */
  inside: boolean;
}

export interface Reliability {
  buckets: ReliabilityBucket[];
  /** Rows behind the whole table, and the window they cover. */
  n: number;
  firstResolved: string | null;
  lastResolved: string | null;
  /** How many buckets miss their own interval. Zero would be calibrated. */
  missing: number;
}

/**
 * Wilson score interval, matching `core.stats.wilson_ci`.
 *
 * Wilson rather than the normal approximation because at the ends of the
 * range the normal interval leaves [0, 1] — a lower bound below zero on a
 * probability is not a bound, it is a rendering bug waiting to happen.
 */
function wilson(successes: number, n: number, z = 1.96): [number, number] {
  if (n <= 0) return [0, 0];
  const p = successes / n;
  const denom = 1 + (z * z) / n;
  const centre = p + (z * z) / (2 * n);
  const spread = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return [
    Math.max(0, (centre - spread) / denom),
    Math.min(1, (centre + spread) / denom),
  ];
}

const SQL = `
  SELECT p.p_touch_3::float AS predicted,
         (o.touched_3)::int AS realised,
         o.resolved_at
    FROM outcomes o
    JOIN predictions p ON p.id = o.prediction_id
   WHERE p.model_scored
     AND p.p_touch_3 IS NOT NULL
     AND o.touched_3 IS NOT NULL
     AND p.config_hash = current_setting('capitalscan.default_config_hash', true)
   ORDER BY p.p_touch_3
`;

/**
 * Equal-count buckets rather than equal-width.
 *
 * Equal-width leaves the tails nearly empty, and a bucket of nine rows
 * produces an interval so wide it can never miss — which would make the
 * gate pass by having no evidence rather than by being calibrated.
 */
export async function reliability(nBuckets = 8): Promise<Reliability> {
  const rows = await query<{
    predicted: number;
    realised: number;
    resolved_at: Date | null;
  }>(SQL);

  if (rows.length === 0) {
    return { buckets: [], n: 0, firstResolved: null, lastResolved: null, missing: 0 };
  }

  const buckets: ReliabilityBucket[] = [];
  const size = Math.floor(rows.length / nBuckets);
  for (let i = 0; i < nBuckets; i++) {
    const start = i * size;
    const end = i === nBuckets - 1 ? rows.length : start + size;
    const slice = rows.slice(start, end);
    if (slice.length === 0) continue;

    const n = slice.length;
    const successes = slice.reduce((acc, r) => acc + r.realised, 0);
    const predicted = slice.reduce((acc, r) => acc + r.predicted, 0) / n;
    const actual = successes / n;
    const [ciLow, ciHigh] = wilson(successes, n);

    buckets.push({
      index: i,
      lo: slice[0].predicted,
      hi: slice[slice.length - 1].predicted,
      predicted,
      actual,
      ciLow,
      ciHigh,
      n,
      inside: predicted >= ciLow && predicted <= ciHigh,
    });
  }

  const dates = rows
    .map((r) => r.resolved_at)
    .filter((d): d is Date => d instanceof Date)
    .sort((a, b) => a.getTime() - b.getTime());

  return {
    buckets,
    n: rows.length,
    firstResolved: dates[0]?.toISOString().slice(0, 10) ?? null,
    lastResolved: dates[dates.length - 1]?.toISOString().slice(0, 10) ?? null,
    missing: buckets.filter((b) => !b.inside).length,
  };
}

// ---------------------------------------------------------------------------
// The trailing realised rate (2026-09-29)
// ---------------------------------------------------------------------------

/**
 * Sessions of resolved signals the trailing rate covers, and the effective
 * sample below which it is withheld. **Mirrors
 * `core.config.ServingParams.realised_rate_sessions` and
 * `.realised_rate_min_n_eff`**; `test_predict_pipeline.py` fails if the two
 * disagree. A copy rather than a round trip for the same reason the caveat
 * is one (`format.ts`).
 */
export const REALISED_RATE_SESSIONS = 20;
export const REALISED_RATE_MIN_N_EFF = 30;

/**
 * What the model stated against what happened, lately, for one side.
 *
 * **Why this exists.** The published level leans with the market: under
 * ADR 193's rolling calibration it ran about 7 points high on 2026-09-27,
 * and under the fixed split before that about 5 low. The caveat says so in
 * words; this shows the reader the gap in numbers, next to the number it
 * qualifies. It is the forward log itself, so nothing here was fitted.
 */
export interface RecentRate {
  side: "long" | "short";
  /** Mean shipped `p_touch_3`, and how often +3% was actually reached. */
  stated: number;
  realised: number;
  /** 95% Wilson interval on `realised`, sized on `nEff`. */
  ciLow: number;
  ciHigh: number;
  n: number;
  /**
   * Rows discounted for same-day clustering: signals on one day share one
   * market move, so twenty days of 400 signals is not 400 coin flips.
   */
  nEff: number;
  sessions: number;
  firstDate: string;
  lastDate: string;
  /** False when `nEff` is below `REALISED_RATE_MIN_N_EFF`: shown as withheld. */
  enough: boolean;
}

export interface RecentRow {
  asOf: string;
  side: string;
  stated: number;
  realised: number;
}

/**
 * Pure, so the arithmetic is tested without a database.
 *
 * **The effective sample is a cluster design effect over sessions.** The
 * variance of the pooled rate, estimated from how far each session's
 * successes sit from what the pooled rate predicts for it, against the
 * binomial variance the raw row count would imply. Their ratio is the
 * design effect; `n / deff` is the effective sample, floored at one
 * session's worth of independence and capped at `n`. The Wilson interval is
 * then taken on that, which is what the modal's hover text already
 * promises: "counted by independent information rather than row count".
 */
export function summariseRecent(rows: RecentRow[], side: "long" | "short"): RecentRate | null {
  const mine = rows.filter((r) => r.side === side);
  const n = mine.length;
  if (n === 0) return null;
  const successes = mine.reduce((acc, r) => acc + r.realised, 0);
  const p = successes / n;
  const stated = mine.reduce((acc, r) => acc + r.stated, 0) / n;

  const bySession = new Map<string, { n: number; y: number }>();
  for (const r of mine) {
    const s = bySession.get(r.asOf) ?? { n: 0, y: 0 };
    s.n += 1;
    s.y += r.realised;
    bySession.set(r.asOf, s);
  }
  const k = bySession.size;
  let nEff = n;
  const binomial = (p * (1 - p)) / n;
  if (k > 1 && binomial > 0) {
    let ss = 0;
    for (const s of bySession.values()) ss += (s.y - p * s.n) ** 2;
    const clustered = (ss / (n * n)) * (k / (k - 1));
    const deff = Math.max(1, clustered / binomial);
    nEff = Math.min(n, Math.max(k, n / deff));
  }
  const [ciLow, ciHigh] = wilson(p * nEff, nEff);
  const dates = [...bySession.keys()].sort();
  return {
    side,
    stated,
    realised: p,
    ciLow,
    ciHigh,
    n,
    nEff: Math.round(nEff),
    sessions: k,
    firstDate: dates[0],
    lastDate: dates[dates.length - 1],
    enough: nEff >= REALISED_RATE_MIN_N_EFF,
  };
}

/**
 * The last `REALISED_RATE_SESSIONS` signal dates that have resolved
 * outcomes, in-population only. **`NOT p.cosmetic`**: ADR 183's watch-universe
 * scores are extrapolation and would measure a different population.
 * Side comes from the event the prediction scored (ADR 191 remaps
 * `event_id` into serving's id space); an unresolved link has no side and
 * is left out rather than guessed.
 */
const RECENT_SQL = `
  WITH r AS (
    SELECT p.as_of, e.side, p.p_touch_3::float AS stated, (o.touched_3)::int AS realised
      FROM outcomes o
      JOIN predictions p ON p.id = o.prediction_id
      JOIN events e ON e.id = p.event_id
     WHERE p.model_scored
       AND NOT p.cosmetic
       AND p.p_touch_3 IS NOT NULL
       AND o.touched_3 IS NOT NULL
       AND p.config_hash = current_setting('capitalscan.default_config_hash', true)
  ), days AS (
    SELECT DISTINCT as_of FROM r ORDER BY as_of DESC LIMIT $1
  )
  SELECT to_char(r.as_of, 'YYYY-MM-DD') AS as_of, r.side, r.stated, r.realised
    FROM r JOIN days USING (as_of)
`;

export async function recentRealised(): Promise<Record<"long" | "short", RecentRate | null>> {
  // `as_of` arrives as text (`to_char` in the SQL): a `date` parsed into a
  // JS `Date` can land on the wrong day depending on the server's zone.
  const rows = await query<{ as_of: string; side: string; stated: number; realised: number }>(
    RECENT_SQL,
    [REALISED_RATE_SESSIONS],
  );
  const shaped: RecentRow[] = rows.map((r) => ({
    asOf: r.as_of,
    side: r.side,
    stated: Number(r.stated),
    realised: Number(r.realised),
  }));
  return { long: summariseRecent(shaped, "long"), short: summariseRecent(shaped, "short") };
}

/**
 * For page loaders: a failure hides the comparison and never the page. The
 * modal renders nothing for a side with no figures, which is the honest
 * empty state for a comparison that could not be made.
 */
export async function recentRealisedOrNone(): Promise<Record<"long" | "short", RecentRate | null>> {
  const now = Date.now();
  if (cache && now - cache.at < RECENT_CACHE_MS) return cache.value;
  try {
    const value = await recentRealised();
    cache = { at: now, value };
    return value;
  } catch {
    return { long: null, short: null };
  }
}

/**
 * **Cached per server process for ten minutes.** The figures change only
 * when a nightly resolves outcomes, twice a day at most, and the query took
 * ~1.2 s on the Pi (2026-09-29) -- too much to add to every screener and
 * ticker load. A failure is not cached, so the next load retries.
 */
const RECENT_CACHE_MS = 10 * 60 * 1000;
let cache: { at: number; value: Record<"long" | "short", RecentRate | null> } | null = null;
