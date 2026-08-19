import { num, query } from "./db";

/**
 * `/research` reads four things: the cell grid, the era breakdown, the
 * benchmark arms with their null distribution, and the drawdown slice.
 *
 * **The page exists to make a negative result legible.** ADR 112 measured
 * zero cells surviving FDR correction, and ADR 033 says a null result reads
 * better than a suspiciously profitable backtest — but only if the null
 * result is readable. Every function here returns suppressed cells,
 * intervals that span zero, and q-values near 1, because those are the
 * measurement rather than an absence of one.
 *
 * ADR 118: SELECTs against the views. No handler, no MCP.
 *
 * **No `split` parameter anywhere.** Gate item 9. `validate` is hardcoded
 * in the queries that take a split at all; holdout is unreachable because
 * nothing here can express it.
 */

/** The grid's fixed coordinates. `research/cell_stats.py` measured on
 * these, so a page that let them vary would be offering cells that were
 * never computed. */
export const GRID = {
  horizonDays: 5,
  targetPct: 0.03,
  entryKind: "next_open",
  arm: "signal",
  /** `StatsParams.fdr_alpha`. Nothing survives it (ADR 112); the page says
   * so rather than hiding the comparison. */
  fdrAlpha: 0.05,
} as const;

export interface Cell {
  cellId: string;
  signalType: string;
  side: string;
  ddBucket: string;
  splitKey: string;
  nEvents: number | null;
  nEff: number | null;
  pHit: number | null;
  baseline: number | null;
  edge: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  qValue: number | null;
  suppressed: boolean;
  suppressReason: string | null;
}

interface CellRaw {
  cell_id: string;
  signal_type: string;
  side: string;
  dd_bucket: string;
  split_key: string;
  n_events: number | null;
  n_eff: number | null;
  p_hit: string | null;
  baseline: string | null;
  edge: string | null;
  ci_low: string | null;
  ci_high: string | null;
  q_value: string | null;
  suppressed: boolean | null;
  suppress_reason: string | null;
}

/**
 * Every cell in the headline grid, **suppressed included**.
 *
 * `v_stats` already nulls the rates on a suppressed cell (DESIGN §8.2), so
 * a suppressed row arrives with `p_hit = null` and its reason intact —
 * ADR 026 is structural rather than something this page has to remember.
 *
 * `era IS NULL` selects the all-era rows; era is a reporting split (ADR
 * 103) and is fetched separately.
 */
const CELLS_SQL = `
  SELECT cell_id, signal_type, side, dd_bucket, split_key,
         n_events, n_eff, p_hit, baseline, edge, ci_low, ci_high,
         q_value, suppressed, suppress_reason
    FROM v_stats
   WHERE config_hash = current_setting('capitalscan.default_config_hash', true)
     AND arm = $1
     AND era IS NULL
     AND signal_strength IS NULL
     AND horizon_days = $2
     AND target_pct = $3
     AND entry_kind = $4
     AND split_key = ANY($5::text[])
   ORDER BY split_key, side, signal_type, dd_bucket
`;

function toCell(r: CellRaw): Cell {
  return {
    cellId: r.cell_id,
    signalType: r.signal_type,
    side: r.side,
    ddBucket: r.dd_bucket,
    splitKey: r.split_key,
    nEvents: r.n_events,
    nEff: r.n_eff,
    pHit: num(r.p_hit),
    baseline: num(r.baseline),
    edge: num(r.edge),
    ciLow: num(r.ci_low),
    ciHigh: num(r.ci_high),
    qValue: num(r.q_value),
    suppressed: r.suppressed === true,
    suppressReason: r.suppress_reason,
  };
}

/**
 * `train` and `validate` only. **Holdout is not a parameter**, it is not in
 * the list, and no caller can add it — the splits are a constant here
 * rather than an argument (ADR 019, ADR 033).
 */
export async function cells(): Promise<Cell[]> {
  const rows = await query<CellRaw>(CELLS_SQL, [
    GRID.arm,
    GRID.horizonDays,
    GRID.targetPct,
    GRID.entryKind,
    ["train", "validate"],
  ]);
  return rows.map(toCell);
}

/**
 * The same cells across eras, **descriptive only**.
 *
 * ADR 103: era is a reporting split, never a tested family, so these carry
 * no q-value and the page must not present one. The stability question is
 * "does a cell light up in one era only", which is a shape you read rather
 * than a test you pass.
 *
 * Era 2024+ is absent by construction — it is the holdout window, and
 * `research/cell_stats.py` never computed it. Asserted by test rather than
 * trusted.
 */
const ERAS_SQL = `
  SELECT cell_id, signal_type, side, dd_bucket, split_key, era,
         n_events, n_eff, p_hit, baseline, edge, ci_low, ci_high,
         NULL::numeric AS q_value, suppressed, suppress_reason
    FROM v_stats
   WHERE config_hash = current_setting('capitalscan.default_config_hash', true)
     AND arm = $1
     AND era IS NOT NULL
     AND signal_strength IS NULL
     AND horizon_days = $2
     AND target_pct = $3
     AND entry_kind = $4
     AND split_key = ANY($5::text[])
   ORDER BY era, side, signal_type, dd_bucket
`;

export interface EraCell extends Cell {
  era: string;
}

/**
 * **Both splits, not validate alone.**
 *
 * `SplitParams` puts 2010-2019 entirely in train, so a validate-only era
 * grid renders two empty columns and one populated one — measured: 0 of 14
 * unsuppressed cells in 2010-2014 and 2015-2019 on validate, 7 in
 * 2020-2023. It looked like a broken query and was a split boundary.
 *
 * Holdout is still excluded, and still by literal rather than parameter.
 */

export async function eraCells(): Promise<EraCell[]> {
  const rows = await query<CellRaw & { era: string }>(ERAS_SQL, [
    GRID.arm,
    GRID.horizonDays,
    GRID.targetPct,
    GRID.entryKind,
    ["train", "validate"],
  ]);
  return rows.map((r) => ({ ...toCell(r), era: r.era }));
}

/* --- benchmark arms ------------------------------------------------------ */

export interface Arm {
  arm: string;
  /**
   * ADR 099's breadth split, stored in `benchmarks.era`: `pooled` is every
   * event, `breadth_high` is the subset firing on high-breadth days.
   *
   * **Not a duplicate row, and not averageable.** The signal arm on
   * validate is **+12.6% pooled and −6.5% on high breadth** — the split is
   * the finding, and a page that showed one number would be hiding it.
   */
  era: string;
  totalRet: number | null;
  sharpe: number | null;
  maxDrawdown: number | null;
  fracDeployed: number | null;
  nTrades: number | null;
}

/**
 * The seven non-null arms, on validate.
 *
 * `random` is excluded here and fetched as a distribution: it is 200
 * replications and an average of them is the summary ADR 013 exists to
 * avoid.
 */
const ARMS_SQL = `
  SELECT arm, era, total_ret, sharpe, max_drawdown, frac_deployed, n_trades
    FROM benchmarks
   WHERE config_hash = current_setting('capitalscan.default_config_hash', true)
     AND split_key = 'validate'
     AND arm <> 'random'
     AND replication IS NULL
   ORDER BY era, arm
`;

export async function arms(): Promise<Arm[]> {
  const rows = await query<{
    arm: string;
    era: string;
    total_ret: string | null;
    sharpe: string | null;
    max_drawdown: string | null;
    frac_deployed: string | null;
    n_trades: number | null;
  }>(ARMS_SQL);
  return rows.map((r) => ({
    arm: r.arm,
    era: r.era,
    totalRet: num(r.total_ret),
    sharpe: num(r.sharpe),
    maxDrawdown: num(r.max_drawdown),
    fracDeployed: num(r.frac_deployed),
    nTrades: r.n_trades,
  }));
}

/**
 * The random-entry null, **every replication**.
 *
 * The `replication` column exists so this can be a distribution rather than
 * a mean, and the session plan says to render it as one. A summary would
 * hide the only thing worth looking at: where the signal arm falls inside
 * it. Session 13 measured the signal arm below the null's 97.5th percentile
 * on both splits.
 */
const NULL_SQL = `
  SELECT total_ret
    FROM benchmarks
   WHERE config_hash = current_setting('capitalscan.default_config_hash', true)
     AND split_key = 'validate'
     AND arm = 'random'
     AND era = $1
     AND replication IS NOT NULL
   ORDER BY total_ret
`;

/** Scoped to one breadth split, because the arms are. Comparing a signal
 * arm measured on high-breadth days against a null pooled over every day
 * would be comparing two populations. */
export async function nullDistribution(era = "pooled"): Promise<number[]> {
  const rows = await query<{ total_ret: string | null }>(NULL_SQL, [era]);
  return rows.map((r) => num(r.total_ret)).filter((v): v is number => v !== null);
}

/** The signal arm's percentile inside the null, computed here because it is
 * a rank rather than arithmetic on a rate — a count of replications below a
 * value, which is what "below the 97.5th percentile" means. */
export function percentileOf(value: number, sorted: number[]): number | null {
  if (sorted.length === 0) return null;
  let below = 0;
  for (const v of sorted) {
    if (v < value) below += 1;
    else break;
  }
  return below / sorted.length;
}

/* --- drawdown slice ------------------------------------------------------ */

/**
 * ADR 015's central claim, sliced by drawdown bucket.
 *
 * Session 14 measured **every interval crossing zero**, so this returns the
 * intervals and the page draws them. The claim is that signals in deeper
 * drawdowns resolve up more often; the measurement does not support it, and
 * the page's job is to show that clearly enough to check.
 */
export async function drawdownSlice(): Promise<Cell[]> {
  const all = await cells();
  return all.filter((c) => c.splitKey === "validate");
}
