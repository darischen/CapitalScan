"""Live call overlay: ADR 050, DESIGN §4.8/§8.

On a long signal, price candidate long calls from the **current** option
chain — cost at the real ask, breakeven, payoff at each reachability rung,
max loss. Long-side only (ADR 017: the upper-band signal drives a trim
recommendation, not an options play). Explicitly not backtested: every
result this module returns must carry `priced_from: "live_quotes"` and
`backtested: False`, and it never reports a historical hit rate for the
option itself — all statistical claims come from the shares model.

This is `jobs/`, not `core/`, because it fetches network state (invariant 1).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from capitalscan.jobs.fetch import yahoo

MIN_HOLD_CALENDAR_DAYS = 7  # covers ExitParams.max_hold_days=5 trading days
NUM_STRIKES = 3  # ATM plus the next two OTM strikes


def _pick_expiration(expirations: list[str], as_of: date) -> str | None:
    cutoff = as_of + timedelta(days=MIN_HOLD_CALENDAR_DAYS)
    for exp in sorted(expirations):
        if date.fromisoformat(exp) >= cutoff:
            return exp
    return None


def _candidate_strikes(calls: pd.DataFrame, entry_price: float) -> pd.DataFrame:
    """ATM plus the next `NUM_STRIKES - 1` out-of-the-money strikes."""
    otm = calls.loc[calls["strike"] >= entry_price].sort_values("strike")
    return otm.head(NUM_STRIKES)


def build_overlay(
    ticker: str,
    entry_price: float,
    reach_targets: tuple[float, ...],
    as_of: date | None = None,
) -> dict | None:
    """`None` when the name has no listed options or no chain far enough
    out — the poller then simply omits `call_overlay_json` rather than
    fabricating a row (DESIGN §3.11's null-not-fabricated rule, applied
    here to a recommendation rather than a data point).
    """
    as_of = as_of or date.today()
    expirations = yahoo.fetch_option_expirations(ticker)
    if not expirations:
        return None
    expiration = _pick_expiration(expirations, as_of)
    if expiration is None:
        return None

    calls = yahoo.fetch_chain(ticker, expiration)
    if calls.empty or "strike" not in calls.columns or "ask" not in calls.columns:
        return None

    strikes = _candidate_strikes(calls, entry_price)
    if strikes.empty:
        return None

    rungs = []
    for strike_row in strikes.itertuples():
        strike = float(strike_row.strike)  # type: ignore[arg-type]
        ask = float(strike_row.ask)  # type: ignore[arg-type]
        if ask <= 0:
            continue
        payoffs = {
            f"reach_{int(rung * 100)}pct": round(
                max(0.0, entry_price * (1 + rung) - strike) - ask, 4
            )
            for rung in reach_targets
        }
        rungs.append(
            {
                "strike": strike,
                "cost": round(ask, 4),
                "breakeven": round(strike + ask, 4),
                "max_loss": round(ask, 4),
                "payoff_at_reach": payoffs,
            }
        )
    if not rungs:
        return None

    return {
        "ticker": ticker,
        "expiration": expiration,
        "entry_price": entry_price,
        "strikes": rungs,
        "priced_from": "live_quotes",
        "backtested": False,
    }
