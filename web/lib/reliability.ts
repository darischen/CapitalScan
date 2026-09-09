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
