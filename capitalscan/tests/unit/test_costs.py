"""Unit tests for core/costs.py.

Small module, but it decides whether the long-vs-short comparison is honest
(DESIGN §3.9). Two rules carry it: slippage applies on *both* legs, and
borrow applies to shorts only.
"""

from __future__ import annotations

import pytest

from capitalscan.core import costs
from capitalscan.core.config import CostParams
from capitalscan.core.types import Side

CP = CostParams()


# ---------------------------------------------------------------------------
# slippage
# ---------------------------------------------------------------------------


def test_slippage_is_bps_of_price():
    assert costs.slippage(100.0, CP) == pytest.approx(100.0 * 3.0 / 1e4)


def test_slippage_scales_with_price():
    assert costs.slippage(200.0, CP) == pytest.approx(2 * costs.slippage(100.0, CP))


def test_slippage_is_zero_when_configured_away():
    assert costs.slippage(100.0, CostParams(slippage_bps=0.0)) == 0.0


# ---------------------------------------------------------------------------
# borrow_cost
# ---------------------------------------------------------------------------


def test_borrow_cost_is_annual_bps_prorated_over_trading_days():
    # 40 bps annual over a 5-day hold: 0.0040 * 5/252 ~ 0.8 bp.
    assert costs.borrow_cost(5, CP) == pytest.approx(0.0040 * 5 / 252)


def test_borrow_cost_is_zero_for_a_zero_day_hold():
    assert costs.borrow_cost(0, CP) == 0.0


def test_borrow_cost_grows_with_holding_days():
    assert costs.borrow_cost(10, CP) > costs.borrow_cost(5, CP)


# ---------------------------------------------------------------------------
# apply_costs
# ---------------------------------------------------------------------------


def test_apply_costs_reduces_a_winning_long():
    net = costs.apply_costs(0.04, Side.LONG, 5, CP)
    assert net < 0.04


def test_apply_costs_charges_slippage_on_both_legs():
    # Two legs at 3 bps each: 6 bps total off a long's gross return.
    net = costs.apply_costs(0.04, Side.LONG, 5, CP)
    assert net == pytest.approx(0.04 - 2 * 3.0 / 1e4)


def test_apply_costs_charges_no_borrow_on_a_long():
    long_net = costs.apply_costs(0.04, Side.LONG, 5, CP)
    assert long_net == pytest.approx(0.04 - 2 * 3.0 / 1e4)


def test_apply_costs_charges_borrow_on_a_short():
    short_net = costs.apply_costs(0.04, Side.SHORT, 5, CP)
    long_net = costs.apply_costs(0.04, Side.LONG, 5, CP)
    assert short_net == pytest.approx(long_net - costs.borrow_cost(5, CP))


def test_apply_costs_makes_a_loss_worse_not_better():
    # Costs always subtract from the position's return, whichever side.
    assert costs.apply_costs(-0.04, Side.LONG, 5, CP) < -0.04
    assert costs.apply_costs(-0.04, Side.SHORT, 5, CP) < -0.04


def test_apply_costs_is_a_no_op_with_zeroed_params():
    free = CostParams(slippage_bps=0.0, commission_per_share=0.0, borrow_bps_annual=0.0)
    assert costs.apply_costs(0.04, Side.SHORT, 5, free) == pytest.approx(0.04)


def test_apply_costs_charges_commission_on_both_legs():
    cp = CostParams(slippage_bps=0.0, commission_per_share=0.005, borrow_bps_annual=0.0)
    net = costs.apply_costs(0.04, Side.LONG, 5, cp, entry_price=100.0)
    assert net == pytest.approx(0.04 - 2 * 0.005 / 100.0)


def test_commission_is_skipped_without_an_entry_price():
    # Commission is per share, so it needs a price to become a return.
    # Without one it is omitted rather than guessed.
    cp = CostParams(slippage_bps=0.0, commission_per_share=0.005, borrow_bps_annual=0.0)
    assert costs.apply_costs(0.04, Side.LONG, 5, cp) == pytest.approx(0.04)
