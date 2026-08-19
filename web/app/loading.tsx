/**
 * DESIGN §11.6: a skeleton matching the final layout, not a spinner.
 *
 * The row height and column count are the real ones, so the page does not
 * jump when the data arrives. Eight rows because that is roughly what a
 * firing day produces and an empty frame that is too tall reads as a
 * failure that has not finished failing.
 */
export default function Loading() {
  return (
    <>
      <div className="strip">
        <span className="skel" style={{ width: 88 }} />
        <span className="skel" style={{ width: 132 }} />
        <span className="skel" style={{ width: 96 }} />
      </div>
      <div className="wrap">
        <table className="screen">
          <tbody>
            {Array.from({ length: 8 }, (_, i) => (
              <tr key={i}>
                <td className="rail">
                  <span />
                </td>
                <td>
                  <span className="skel" style={{ width: 46 }} />
                </td>
                <td>
                  <span className="skel" style={{ width: 104 }} />
                </td>
                <td className="r">
                  <span className="skel" style={{ width: 18 }} />
                </td>
                <td className="r">
                  <span className="skel" style={{ width: 34 }} />
                </td>
                <td className="r">
                  <span className="skel" style={{ width: 62 }} />
                </td>
                <td>
                  <span className="skel" style={{ width: 40 }} />
                </td>
                <td className="r">
                  <span className="skel" style={{ width: 22 }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
