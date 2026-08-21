/**
 * StockCharts URLs that arrive with this project's indicators already drawn.
 *
 * **Why a stored chart id rather than indicator parameters.** StockCharts
 * retired the inline chart-args format (`def/servlet/SC.web?c=TSLA,uu[...]`)
 * that used to encode overlays in the URL; it now returns nothing. Their
 * current model stores a chart definition server-side and hands out an
 * opaque key, and a ChartStyle is documented as "everything about a chart
 * except its ticker symbol". So `id` supplies the indicators and `s`
 * supplies the symbol, independently -- which is exactly the seam this
 * needs.
 *
 * Verified 2026-08-21 against a cookieless request: `s=DAL` with the id
 * below renders DAL as candlesticks over one year, with `BB(20,2.0)`,
 * `EMA(10)` and `EMA(50)` on price and three panels beneath -- volume,
 * `Full STO %K(14,3) %D(3)`, and `RSI(14)`. No StockCharts account is
 * involved at any point, on their side or ours.
 */

/**
 * The stored chart carrying this project's indicator settings.
 *
 * Built 2026-08-20 in StockCharts' own workbench and made permanent with
 * their "Reload with Link" button, which is available to anonymous
 * visitors. It draws, matching `core/config.py`:
 *
 * - `BB(20,2.0)` -- `IndicatorParams.bb_window`, `bb_std`
 * - `Full STO %K(14,3) %D(3)` -- `stoch_window`, `stoch_smooth_k`, `stoch_smooth_d`
 * - the stochastic panel's 20 / 80 gridlines -- `SignalParams.stoch_oversold`,
 *   `stoch_overbought`
 *
 * **Rebuilt 2026-08-21 to the user's specification.** Candlesticks over a
 * one-year predefined range with 6 extra bars, volume in its own panel, and
 * the oscillators beneath price: `EMA(10)`, `EMA(50)` and Bollinger on
 * price, `Full Stochastics` and `RSI(14)` below.
 *
 * `EMA(10)`, `EMA(50)` and `RSI(14)` correspond to nothing in
 * `core/config.py` -- this project computes none of them. They are a
 * reading preference and are deliberately not pinned by a test; only the
 * four settings above are, because only those can silently disagree with
 * what a signal was measured against.
 *
 * **`MA(200)` and the explicit 20/80 horizontal lines are gone.** The free
 * tier allows three overlays per chart and the three above use them; the
 * stochastic panel draws its own reference lines at 20, 50 and 80, so the
 * thresholds stayed legible. Superseded ids: `p18455069802` (OHLC bars,
 * panel above price), `p79286883726` (candlesticks, MA200, drawn lines).
 *
 * **A source constant, deliberately not an environment variable.** The id
 * decides which indicators a reader sees next to this project's signals, so
 * changing it is a change to what the page claims -- it belongs in a diff
 * that someone reads, not in a dashboard field that moves without one. It
 * also lets `stockcharts.test.ts` assert the settings above are the ones
 * being pointed at.
 *
 * **This is a reference to someone else's database.** If the chart is ever
 * edited or expires, these links keep resolving and quietly draw the wrong
 * indicators -- there is no error to catch. `p=D` below is pinned for that
 * reason, and re-checking the rendered chart is a manual step in any change
 * to the indicator config.
 */
export const CHART_ID = "p35839673656";

/** SharpCharts workbench. The interactive page, not the rendered PNG at
 * `c-sc/sc` -- a reader following one of these links wants to pan and zoom. */
const WORKBENCH = "https://stockcharts.com/sc3/ui";

/**
 * This project's ticker spelled the way StockCharts spells it.
 *
 * **Class shares differ, and they fail silently.** The database follows
 * Yahoo and writes `BRK-B`; StockCharts writes `BRK/B`. Asking for `BRK-B`
 * does not error -- it returns a 305x176 "symbol not found" image where a
 * 990x664 chart should be, which inside a new browser tab reads as a
 * StockCharts problem rather than ours. Two of 712 tickers are affected
 * (`BRK-B`, `BF-B`), which is few enough to never notice and enough to be
 * wrong.
 *
 * The hyphen is the only translation. It is applied before encoding,
 * because the `/` must survive as `%2F` rather than as a path separator.
 */
export function stockchartsSymbol(ticker: string): string {
  return ticker.trim().toUpperCase().replace(/-/g, "/");
}

/**
 * The chart for one ticker, with the indicators already on it.
 *
 * `p=D` is pinned rather than inherited from the stored chart. The stored
 * period is daily today, but it lives in an account this project does not
 * control, and a chart that silently became weekly would put a reader's eye
 * on bands computed over five times the window that produced the signal.
 * Naming it here costs nothing and removes that path.
 */
export function chartUrl(ticker: string): string {
  const params = new URLSearchParams({
    s: stockchartsSymbol(ticker),
    p: "D",
    id: CHART_ID,
  });
  return `${WORKBENCH}?${params.toString()}`;
}
