/**
 * The event history's skeleton: six rows at the real column widths.
 *
 * Two places draw it. `loading.tsx` shows it under the page skeleton, and
 * the ticker page shows it again as the history boundary's fallback once
 * the chart has arrived, so the rows the reader watches fill in are the
 * same rows in both phases.
 *
 * Its own module rather than a function in `Ticker.tsx`: that module
 * imports four client components, and `loading.tsx` needs none of them.
 */
export default function HistorySkeleton() {
  return (
    <section className="history" aria-busy="true">
      <table className="screen">
        <tbody>
          {Array.from({ length: 6 }, (_, i) => (
            <tr key={i}>
              <td className="rail">
                <span />
              </td>
              <td>
                <span className="skel" style={{ width: 72 }} />
              </td>
              <td>
                <span className="skel" style={{ width: 118 }} />
              </td>
              <td className="r">
                <span className="skel" style={{ width: 44 }} />
              </td>
              <td className="r">
                <span className="skel" style={{ width: 44 }} />
              </td>
              <td>
                <span className="skel" style={{ width: 52 }} />
              </td>
              <td className="r">
                <span className="skel" style={{ width: 24 }} />
              </td>
              <td className="r">
                <span className="skel" style={{ width: 40 }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
