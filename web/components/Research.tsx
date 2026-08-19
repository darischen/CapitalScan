import { NONE, SIGNAL_LABELS, fmt, pct, signedPct } from "@/lib/format";
import type { Arm, Cell, EraCell } from "@/lib/research";
import { GRID, percentileOf } from "@/lib/research";

/**
 * `/research` — the measurement, including the parts that say nothing was
 * found.
 *
 * ADR 033: *"the strongest version of this project is a rigorous
 * event-study engine with a natural language interface and honest
 * confidence intervals. Built for that outcome, a null result stops being a
 * failure."* That only holds if the null result is legible, so nothing here
 * hides a suppressed cell, rounds a q-value to "not significant", or draws
 * an interval that spans zero as though it did not.
 */

/* --- the headline result ------------------------------------------------ */

export function KillBanner({ cells }: { cells: Cell[] }) {
  const scored = cells.filter((c) => c.qValue !== null);
  const survivors = scored.filter((c) => (c.qValue as number) <= GRID.fdrAlpha);
  const minQ = scored.length
    ? Math.min(...scored.map((c) => c.qValue as number))
    : null;
  const suppressed = cells.filter((c) => c.suppressed);

  return (
    <section className="kill">
      <h2>ADR 112 — the first kill criterion fired</h2>
      <p>
        {/* Computed from the rows on the page, not restated from
            RESULTS.md. A number transcribed into prose drifts from the
            table beside it; one derived from the same query cannot. */}
        <strong className="num">{survivors.length}</strong> of{" "}
        <strong className="num">{scored.length}</strong> scored cells survive
        Benjamini-Hochberg at <span className="num">q ≤ {GRID.fdrAlpha}</span>.
        The smallest q-value in the grid is{" "}
        <strong className="num">{minQ === null ? NONE : minQ.toFixed(4)}</strong>.{" "}
        <strong className="num">{suppressed.length}</strong> cells are suppressed
        for insufficient effective sample.
      </p>
      <p className="dim">
        These events do not predict five-session returns better than each
        ticker&rsquo;s own base rate, at this sample. Every hit rate below is a
        historical frequency conditional on a past state — not a forecast, and
        not an edge.
      </p>
    </section>
  );
}

/* --- cell grid ----------------------------------------------------------- */

/**
 * The hit rate with its Wilson interval, and the baseline it is measured
 * against.
 *
 * **The interval belongs to `p_hit`, not to `edge`** — `cell_stats.py`
 * computes `wilson_ci(p_hit * n_eff, n_eff)`, a proportion interval on the
 * hit rate. `edge` is `p_hit - baseline`, a *difference*, and no interval
 * for it is stored.
 *
 * The first version of this column was labelled "Edge (95% CI)" and drew
 * the edge point inside the hit-rate interval. Two different quantities on
 * one axis: `bb_lower_touch` 0-10 has `p_hit` 18.5% with CI [11.4%, 28.5%]
 * and `edge` −1.8%, so the point sat outside its own bar. Caught by
 * looking at the rendered page — every bar came out "does not span zero",
 * which is true of a hit-rate interval and meaningless for an edge.
 *
 * So the bar shows the interval on the rate, with the **baseline** marked
 * inside it. That is the comparison the reader wants: an interval that
 * contains its own baseline is a cell that has not distinguished itself
 * from the ticker's base rate, which on this data is nearly all of them.
 *
 * DESIGN §11.2 asked for an edge bar. An edge bar needs an edge interval,
 * and one does not exist in the schema — recorded in `RESULTS.md` rather
 * than faked by differencing two bounds, which would assume independence
 * the data does not have.
 */
function RateBar({ cell }: { cell: Cell }) {
  if (cell.suppressed || cell.ciLow === null || cell.ciHigh === null) {
    return <span className="suppressed">—</span>;
  }

  // 0-60% covers every cell in the grid with room to spare, and a fixed
  // scale keeps bars comparable between rows.
  const SPAN = 0.6;
  const at = (v: number) => Math.max(0, Math.min(100, (v / SPAN) * 100));
  const left = at(cell.ciLow);
  const right = at(cell.ciHigh);
  const containsBaseline =
    cell.baseline !== null && cell.ciLow <= cell.baseline && cell.ciHigh >= cell.baseline;

  return (
    <span
      className="edgebar"
      title={`hit ${pct(cell.pHit, 1)}, 95% CI ${pct(cell.ciLow, 1)}-${pct(cell.ciHigh, 1)}, baseline ${pct(cell.baseline, 1)}`}
    >
      <i
        className={containsBaseline ? "ci spans" : "ci"}
        style={{ left: `${left}%`, width: `${Math.max(1, right - left)}%` }}
      />
      {cell.pHit !== null && <i className="point" style={{ left: `${at(cell.pHit)}%` }} />}
      {/* The baseline, drawn inside the interval rather than in a column
          of its own: "does this interval contain its baseline" is the
          question, and two numbers in separate columns make it arithmetic. */}
      {cell.baseline !== null && <i className="base" style={{ left: `${at(cell.baseline)}%` }} />}
    </span>
  );
}

export function CellGrid({ cells, split }: { cells: Cell[]; split: string }) {
  const rows = cells.filter((c) => c.splitKey === split);

  return (
    <table className="screen research">
      <thead>
        <tr>
          <th className="rail" />
          <th>Signal</th>
          <th>Bucket</th>
          <th className="r">n</th>
          <th className="r" title="effective sample size after ADR 098's autocorrelation correction">
            n_eff
          </th>
          <th className="r">Hit</th>
          <th className="r">Base</th>
          <th className="edge-col" title="Wilson interval on the hit rate, with the baseline marked. No edge interval is stored.">Hit rate 95% CI · baseline</th>
          <th className="r">q</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.cellId} className={c.suppressed ? "dimmed" : undefined}>
            <td className="rail">
              <span data-side={c.side} />
            </td>
            <td className="sig">{SIGNAL_LABELS[c.signalType] ?? c.signalType}</td>
            <td className="dim">{c.ddBucket}</td>
            <td className="r num">{c.nEvents ?? NONE}</td>
            <td className="r num">{c.nEff === null ? NONE : c.nEff.toFixed(1)}</td>
            {/* A suppressed cell renders its stored reason and never a
                number (ADR 026). `v_stats` nulls the rate in SQL, so this
                is structural rather than a convention. */}
            {c.suppressed ? (
              <td className="suppressed" colSpan={4}>
                {c.suppressReason ?? "suppressed"}
              </td>
            ) : (
              <>
                <td className="r num">{pct(c.pHit, 1)}</td>
                <td className="r num dim">{pct(c.baseline, 1)}</td>
                <td className="edge-col">
                  <RateBar cell={c} />
                </td>
                {/* Full precision. Rounding 0.849 to "not significant"
                    hides the more informative number. */}
                <td className="r num">
                  <span className={c.qValue !== null && c.qValue <= GRID.fdrAlpha ? "up" : "flagged"}>
                    {c.qValue === null ? NONE : c.qValue.toFixed(3)}
                  </span>
                </td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* --- era breakdown ------------------------------------------------------- */

/**
 * The same cells across eras, side by side.
 *
 * **No q-values.** ADR 103 makes era a reporting split rather than a tested
 * family, so a q-value here would be claiming a test that was never run.
 * The question is whether a cell lights up in one era only, which is a
 * shape you read.
 *
 * Era 2024+ is absent — it is the holdout window. A test asserts it.
 */
export function EraGrid({ cells }: { cells: EraCell[] }) {
  const eras = [...new Set(cells.map((c) => c.era))].sort();
  const keys = [...new Set(cells.map((c) => `${c.side}|${c.signalType}|${c.ddBucket}`))];

  return (
    <table className="screen research">
      <thead>
        <tr>
          <th className="rail" />
          <th>Signal</th>
          <th>Bucket</th>
          {eras.map((e) => (
            <th className="r" key={e}>
              {e}
              {/* Which split the column's cells come from. `SplitParams`
                  puts 2010-2019 entirely in train, so without this the
                  reader would compare a train era against a validate one
                  without being told. */}
              <span className="dim split-tag">
                {[...new Set(cells.filter((c) => c.era === e).map((c) => c.splitKey))].join("/")}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {keys.map((key) => {
          const [side, signalType, ddBucket] = key.split("|");
          return (
            <tr key={key}>
              <td className="rail">
                <span data-side={side} />
              </td>
              <td className="sig">{SIGNAL_LABELS[signalType] ?? signalType}</td>
              <td className="dim">{ddBucket}</td>
              {eras.map((era) => {
                const c = cells.find(
                  (x) =>
                    x.era === era &&
                    x.side === side &&
                    x.signalType === signalType &&
                    x.ddBucket === ddBucket,
                );
                if (!c || c.suppressed) {
                  return (
                    <td className="r suppressed" key={era} title={c?.suppressReason ?? "no cell"}>
                      —
                    </td>
                  );
                }
                return (
                  <td className="r num" key={era} title={`n_eff ${c.nEff?.toFixed(1) ?? "?"}`}>
                    {pct(c.pHit, 1)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/* --- arms and the null --------------------------------------------------- */

/**
 * The random-entry null as a histogram, with the signal arm marked.
 *
 * ADR 013's whole point: 200 replications exist so this can be a
 * distribution. A mean would answer "did the signal beat random on average"
 * when the question is "where does it fall inside the range random
 * produces".
 */
export function NullDistribution({
  values,
  signal,
  era,
}: {
  values: number[];
  signal: number | null;
  era: string;
}) {
  if (values.length === 0) return <p className="dim">No replications for {era}.</p>;

  const lo = Math.min(...values, signal ?? Infinity);
  const hi = Math.max(...values, signal ?? -Infinity);
  const BINS = 28;
  const width = (hi - lo) / BINS || 1;
  const counts = new Array(BINS).fill(0);
  for (const v of values) {
    counts[Math.min(BINS - 1, Math.floor((v - lo) / width))] += 1;
  }
  const peak = Math.max(...counts);
  const pctile = signal === null ? null : percentileOf(signal, values);
  const at = (v: number) => ((v - lo) / (hi - lo || 1)) * 100;

  return (
    <div className="nulldist">
      <div className="bars">
        {counts.map((n, i) => (
          <i key={i} style={{ height: `${(n / peak) * 100}%` }} title={`${n} replications`} />
        ))}
        {signal !== null && (
          <span
            className="marker"
            style={{ left: `${at(signal)}%` }}
            title={`signal arm ${signedPct(signal, 1)}`}
          />
        )}
      </div>
      <div className="scale num">
        <span>{signedPct(lo, 1)}</span>
        <span>{signedPct(hi, 1)}</span>
      </div>
      <p className="dim">
        {values.length} random-entry replications, {era}. The signal arm returned{" "}
        <strong className="num">{signedPct(signal, 1)}</strong>
        {pctile !== null && (
          <>
            , at the <strong className="num">{(pctile * 100).toFixed(0)}th</strong> percentile of
            the null
          </>
        )}
        .
      </p>
      {/* **`RESULTS.md` records the opposite and the database is newer.**
          Session 13 measured the signal arm below the null's 97.5th
          percentile on both splits, under `config_hash 1835688bf7d760ba`.
          The live config's benchmark run (2026-08-16, `86e91448a65aa40b`)
          puts validate/pooled at +12.6% against a 97.5th of +6.4%.

          The page reports what the database holds and says the record
          disagrees. Deciding which is right means re-running the arms and
          comparing, which is a measurement rather than a rendering
          decision. Recorded in RESULTS.md. */}
      {pctile !== null && pctile > 0.975 && (
        <p className="conflict">
          This exceeds the null&rsquo;s 97.5th percentile. <code>RESULTS.md</code>{" "}
          records the signal arm as <em>below</em> it on both splits, measured under an
          earlier config. The two disagree and the discrepancy is unresolved — do not
          read either as settled.
        </p>
      )}
    </div>
  );
}

export function ArmsTable({ arms }: { arms: Arm[] }) {
  return (
    <table className="screen research">
      <thead>
        <tr>
          <th>Arm</th>
          <th>Breadth</th>
          <th className="r">Return</th>
          <th className="r">Sharpe</th>
          <th className="r">Max DD</th>
          <th className="r" title="fraction of capital deployed">
            Deployed
          </th>
          <th className="r">Trades</th>
        </tr>
      </thead>
      <tbody>
        {arms.map((a) => (
          <tr key={`${a.arm}-${a.era}`}>
            <td className="sig">{a.arm}</td>
            {/* ADR 099's split, and the reason the signal arm has two rows.
                Pooled and high-breadth disagree in sign on validate. */}
            <td className="dim">{a.era}</td>
            <td className={`r num ${a.totalRet !== null && a.totalRet >= 0 ? "up" : "down"}`}>
              {signedPct(a.totalRet, 1)}
            </td>
            <td className="r num">{fmt(a.sharpe, 3)}</td>
            <td className="r num">{pct(a.maxDrawdown, 1)}</td>
            <td className="r num">{pct(a.fracDeployed, 0)}</td>
            <td className="r num">{a.nTrades ?? NONE}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* --- drawdown slice ------------------------------------------------------ */

/**
 * ADR 015's central claim: signals in deeper drawdowns resolve up more
 * often.
 *
 * Session 14 measured **every interval crossing zero**, so the page shows
 * the intervals and lets the reader see that. Softening it would defeat the
 * purpose of the page.
 */
export function DrawdownSlice({ cells }: { cells: Cell[] }) {
  const buckets = [...new Set(cells.map((c) => c.ddBucket))].sort();
  const scored = cells.filter((c) => !c.suppressed && c.ciLow !== null && c.baseline !== null);
  // **Contains its baseline**, not "spans zero". The stored interval is on
  // the hit rate, so zero is not the null value — the baseline is. A cell
  // whose interval covers its own baseline has not distinguished itself
  // from the ticker's base rate.
  const overlapping = scored.filter(
    (c) => (c.ciLow as number) <= (c.baseline as number) && (c.ciHigh as number) >= (c.baseline as number),
  );

  return (
    <>
      <p className="dim">
        <strong className="num">{overlapping.length}</strong> of{" "}
        <strong className="num">{scored.length}</strong> hit-rate intervals contain
        their own baseline.
      </p>
      {buckets.map((b) => (
        <div className="ddgroup" key={b}>
          <h3>{b}% drawdown</h3>
          <CellGrid cells={cells.filter((c) => c.ddBucket === b)} split="validate" />
        </div>
      ))}
    </>
  );
}
