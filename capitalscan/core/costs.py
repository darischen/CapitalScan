"""Transaction costs. Pure functions, no IO (DESIGN §3.1).

Two rules carry this module (DESIGN §3.9). Slippage applies on **both**
legs, entry and exit. Borrow applies to **shorts only**, at
`bps_annual x d/252` — roughly 0.8 bp over a 5-day hold at 40 bps
annualized. Negligible for mega-caps, and it belongs in the code so the
long-versus-short comparison (ADR 058) stays honest rather than flattering
the short by omission.
"""

from __future__ import annotations

from capitalscan.core.config import CostParams
from capitalscan.core.types import Side

_BPS = 1e4
_TRADING_DAYS_PER_YEAR = 252
_LEGS = 2  # entry and exit


def slippage(price: float, cp: CostParams) -> float:
    """Slippage on one leg, in price units."""
    return float(price) * cp.slippage_bps / _BPS


def borrow_cost(holding_days: int, cp: CostParams) -> float:
    """Borrow cost over the hold, as a fraction of notional. Shorts only.

    Prorated over trading days, matching the horizon everything else in the
    engine is measured in.
    """
    return cp.borrow_bps_annual / _BPS * holding_days / _TRADING_DAYS_PER_YEAR


def apply_costs(
    gross_ret: float,
    side: Side,
    holding_days: int,
    cp: CostParams,
    entry_price: float | None = None,
) -> float:
    """Net return after slippage, commission, and borrow.

    Costs always subtract from the position's return, so a loss gets worse
    rather than better regardless of side. Slippage is expressed directly as
    a fraction (bps of price on each leg is bps of return on each leg), so
    no price is needed for it.

    `entry_price` is only needed for per-share commission, which is 0.0 by
    default. Without a price the commission term is omitted rather than
    guessed — no fabricated value (DESIGN §3.11).
    """
    net = float(gross_ret)
    net -= _LEGS * cp.slippage_bps / _BPS
    if cp.commission_per_share and entry_price:
        net -= _LEGS * cp.commission_per_share / float(entry_price)
    if side is Side.SHORT:
        net -= borrow_cost(holding_days, cp)
    return net
