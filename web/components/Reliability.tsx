import type { Reliability as ReliabilityData } from "@/lib/reliability";

/**
 * The reliability table, as a table with a bar rather than a scatter plot.
 *
 * **A scatter against the diagonal is the conventional rendering and the
 * wrong one here.** It shows whether points sit near a line; the question
 * this must answer is whether each shipped probability lies inside its own
 * measured interval, which is a per-row yes or no. A reader eyeballing
 * distance from a diagonal cannot tell 2pp inside from 2pp outside, and
 * that difference is the entire finding (ADR 182).
 *
 * So each row draws its interval as a span and the shipped value as a mark
 * on it, and a row whose mark falls outside is called out in words as well
 * as position. Colour is not the only carrier.
 *
 * Server component: it renders numbers and holds no state.
 */
export function Reliability({ data }: { data: ReliabilityData }) {
  if (data.n === 0) {
    return (
      <section className="rel">
        <h2>Calibration</h2>
        <p className="note-gate">
          No resolved predictions yet. The forward log fills as predictions
          reach their 5-day horizon and <code>cscan outcomes</code> scores
          them; until then there is nothing to check the model against.
        </p>
      </section>
    );
  }

  return (
    <section className="rel">
      <div className="history-head">
        <h2>Calibration</h2>
        <span className="dim">
          {data.n.toLocaleString()} resolved predictions
          {data.firstResolved ? `, ${data.firstResolved} to ${data.lastResolved}` : ""}
        </span>
      </div>

      <p className="rel-verdict">
        {data.missing === 0 ? (
          <>
            Every band&apos;s stated chance falls inside the rate actually
            observed for it. The model is calibrated on this evidence.
          </>
        ) : (
          <>
            <strong>
              {data.missing} of {data.buckets.length}
            </strong>{" "}
            bands have a stated chance outside the rate actually observed for
            them. The ordering still holds — higher bands do happen more
            often — so these numbers rank signals well and should not be read
            as exact odds.
          </>
        )}
      </p>

      <table className="modal-table rel-table">
        <thead>
          <tr>
            <th>Band</th>
            <th className="r">Stated</th>
            <th className="r">Observed</th>
            <th className="r">95% range</th>
            <th className="r">n</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.buckets.map((b) => (
            <tr key={b.index} className={b.inside ? undefined : "rel-miss"}>
              <th scope="row" className="num">
                {(b.lo * 100).toFixed(0)}–{(b.hi * 100).toFixed(0)}%
              </th>
              <td className="r num">{(b.predicted * 100).toFixed(1)}%</td>
              <td className="r num">{(b.actual * 100).toFixed(1)}%</td>
              <td className="r num dim">
                {(b.ciLow * 100).toFixed(1)}–{(b.ciHigh * 100).toFixed(1)}
              </td>
              <td className="r num dim">{b.n.toLocaleString()}</td>
              <td className="rel-flag">
                {b.inside ? (
                  <span className="dim">ok</span>
                ) : (
                  <span title="The stated chance lies outside the range actually observed for this band.">
                    {b.predicted < b.ciLow ? "understates" : "overstates"}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="modal-note">
        Bands hold equal numbers of predictions, not equal widths: an
        equal-width band at the extremes would hold too few rows for its
        range to mean anything, and would pass this check by having no
        evidence rather than by being right. Only signals the model was
        actually fitted on are counted.
      </p>
    </section>
  );
}

export default Reliability;
