/**
 * DESIGN §11.6: a skeleton matching the final layout, not a spinner.
 *
 * The chart block is the real height, because that is the element the page
 * would otherwise jump by. Six history rows rather than the screener's
 * eight: a single ticker's recent history is shorter than a firing day.
 */
export default function Loading() {
  return (
    <main className="wrap tk">
      <header className="tk-head">
        <div className="tk-id">
          <span className="skel" style={{ width: 64 }} />
          <span className="skel" style={{ width: 168 }} />
        </div>
        <div className="tk-price">
          <span className="skel" style={{ width: 96, height: 18 }} />
        </div>
      </header>

      <div className="tk-top">
        <section className="rail-strip">
          {Array.from({ length: 11 }, (_, i) => (
            <div className="stat" key={i}>
              <span className="skel" style={{ width: 44 }} />
              <span className="skel" style={{ width: 34 }} />
            </div>
          ))}
        </section>
      </div>

      <div className="tk-chart">
        <span className="skel" style={{ height: 520, borderRadius: 2 }} />
      </div>

      <section className="history">
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
    </main>
  );
}
