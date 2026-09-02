/**
 * The `$10k` column: a hypothetical all-in compounding account, replayed
 * over one ticker's own confluence history. Cosmetic only -- it is not a
 * statistic, it feeds no cell, and nothing outside the ticker page's event
 * table reads it (user's request, 2026-09-02).
 *
 * The rule: start at `START_CAPITAL`, and at every confluence fire this
 * ticker actually traded (long or short, whichever the signal was), put the
 * whole balance on that trade and take `net_ret` -- no share count, no
 * position sizing, just the percentage. Losses and gains both compound.
 */

/** `$10,000.00`, always. Not `core/config.py`: this is a display-only
 * replay in the web layer, never read by anything that computes a
 * statistic, so it does not carry the "no magic numbers outside core"
 * invariant that binds the backtest's own parameters. */
export const EQUITY_START_CAPITAL = 10_000;

/** How far back the replay looks, in trading sessions rather than calendar
 * days -- the same reason `CHART_SQL` counts sessions from `trading_days`:
 * a calendar span drifts by the holiday count. ~500 sessions is ~2 years.
 *
 * A ticker with less history than this needs no special case: the SQL
 * filters `entry_date >= (500th most recent session)`, and if the ticker's
 * own history does not reach that far back, nothing before it exists to
 * filter out -- the replay starts at the ticker's first confluence event
 * on its own. */
export const EQUITY_LOOKBACK_SESSIONS = 500;

export interface EquityTrade {
  id: number;
  /** ISO `YYYY-MM-DD`, so plain string comparison orders correctly. */
  entryDate: string;
  exitDate: string;
  netRet: number;
}

/**
 * Compounds `EQUITY_START_CAPITAL` through a ticker's confluence trades, in
 * entry order, and returns the running balance keyed by event id -- only
 * for the trades actually taken.
 *
 * **A trade is skipped, not merged, when it overlaps the one still held**
 * (user's decision, 2026-09-02): the account is all-in on one position at a
 * time, so a confluence firing before the prior trade's `exitDate` cannot
 * be entered without a share count to split the balance across two open
 * positions, which this deliberately has none of. A skipped trade gets no
 * entry in the returned map -- it was never held, so it has no balance to
 * show, the same as an event this window excludes entirely.
 *
 * **Compounds on the exact running value, rounds only for callers.** Five
 * trades at +5% land on 12762.815625, and the caller rounds that to
 * `$12,762.82` for display -- rounding here first would drift the sixth
 * trade's input by a fraction of a cent for no reason a reader could see.
 */
export function compoundEquity(trades: readonly EquityTrade[]): Map<number, number> {
  const ordered = [...trades].sort(
    (a, b) => a.entryDate.localeCompare(b.entryDate) || a.id - b.id,
  );

  const balances = new Map<number, number>();
  let balance = EQUITY_START_CAPITAL;
  let heldUntil: string | null = null;

  for (const trade of ordered) {
    if (heldUntil !== null && trade.entryDate < heldUntil) continue;
    balance *= 1 + trade.netRet;
    balances.set(trade.id, balance);
    heldUntil = trade.exitDate;
  }

  return balances;
}
