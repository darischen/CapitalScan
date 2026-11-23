/**
 * How far a new listing is from its first indicator row.
 *
 * `cscan indicators` writes nothing for a ticker until it holds
 * `MIN_BARS_FOR_INDICATORS` daily bars (`capitalscan/jobs/compute.py`), and
 * the ticker page's normal view is built on `indicators`. Until then the
 * page shows the warm-up instead (`YoungListing`).
 *
 * **The number is pinned to the Python constant by a test**
 * (`test_web_warmup_constant.py`), not imported: the web cannot read
 * Python, and a copy that drifts would show a countdown that ends on the
 * wrong day.
 *
 * No database import: a pure module, safe for any component.
 */
export const INDICATOR_WARMUP_SESSIONS = 280;

export interface Warmup {
  /** Sessions held, capped at the target. */
  held: number;
  /** Sessions still to come. 0 once the target is reached. */
  remaining: number;
  /** `held / target`, 0 to 1. */
  fraction: number;
}

export function warmup(
  sessions: number,
  target = INDICATOR_WARMUP_SESSIONS,
): Warmup {
  const n =
    Number.isFinite(sessions) && sessions > 0 ? Math.floor(sessions) : 0;
  const held = Math.min(n, target);
  return {
    held,
    remaining: target - held,
    fraction: target > 0 ? held / target : 1,
  };
}
