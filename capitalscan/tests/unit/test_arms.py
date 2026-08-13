"""Contract for `core/arms.py` — the capital simulation Session 13 rests on.

Written before the implementation (CLAUDE.md testing rule). Every case here
is either hand-computable or an invariant the session brief names as an
acceptance criterion, because the failure mode this module has is a
plausible number rather than an exception.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import arms
from capitalscan.core.config import BenchmarkParams
from capitalscan.core.types import Side

BM = BenchmarkParams()


def _pos(entry_idx, exit_idx, returns, ticker="AAA", side=Side.LONG):
    return arms.Position(
        ticker=ticker,
        side=side,
        entry_idx=entry_idx,
        exit_idx=exit_idx,
        entry_price=100.0,
        exit_price=100.0 * float(np.prod([1 + r for r in returns])),
        returns=tuple(returns),
    )


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_max_drawdown_is_a_positive_fraction():
    """Matches `core.indicators.drawdown_from_high`, which reports drawdown
    positive so it lines up with `StatsParams.dd_buckets`."""
    equity = pd.Series([1.0, 1.25, 1.0, 1.5])
    assert arms.max_drawdown(equity) == pytest.approx(0.20)


def test_max_drawdown_of_a_monotone_curve_is_zero():
    assert arms.max_drawdown(pd.Series([1.0, 1.1, 1.2])) == 0.0


def test_annualized_return_over_exactly_one_year_is_the_total_return():
    got = arms.annualized_return(0.30, BM.trading_days_per_year, BM)
    assert got == pytest.approx(0.30)


def test_annualized_return_compounds_a_two_year_window():
    """A 69% total return over two years is 30% annualized: 1.30^2 = 1.69."""
    got = arms.annualized_return(0.69, 2 * BM.trading_days_per_year, BM)
    assert got == pytest.approx(0.30, abs=1e-9)


def test_sharpe_of_a_constant_return_series_is_not_finite_and_returns_none():
    """Zero dispersion has no Sharpe. None, never a divide-by-zero and never
    a substituted value (invariant 4)."""
    assert arms.sharpe(pd.Series([0.001] * 50), BM) is None


def test_sharpe_uses_the_configured_risk_free_rate():
    """Two risk-free rates on the same return series must give different
    Sharpes, which is what proves the rate is read rather than ignored."""
    rets = pd.Series(np.linspace(-0.01, 0.02, 100))
    at_zero_rf = arms.sharpe(rets, BenchmarkParams(risk_free_annual=0.0))
    at_high_rf = arms.sharpe(rets, BenchmarkParams(risk_free_annual=0.10))
    assert at_zero_rf is not None and at_high_rf is not None
    assert at_zero_rf > at_high_rf


def test_capital_efficiency_divides_total_return_by_deployment():
    assert arms.capital_efficiency(0.10, 0.20) == pytest.approx(0.50)


def test_capital_efficiency_is_zero_when_nothing_was_ever_deployed():
    """13.1 acceptance: an arm holding cash for the whole window returns 0.0
    rather than dividing by zero."""
    assert arms.capital_efficiency(0.0, 0.0) == 0.0


# --------------------------------------------------------------------------
# Position return path
# --------------------------------------------------------------------------


def test_position_returns_on_a_dividend_free_series_reproduce_the_price_path():
    close = np.array([100.0, 110.0, 121.0, 121.0])
    adj = close.copy()
    got = arms.position_returns(close, adj, 1, 2, 100.0, 121.0, Side.LONG)
    # Entry at 100 on bar 1 whose close is 110 -> +10%; bar 2 exits at 121
    # from a 110 close -> +10%.
    assert got == pytest.approx((0.10, 0.10))


def test_position_returns_pick_up_the_dividend_stream():
    """`adj_close` carries the dividend and `close` does not, so a day with
    a distribution returns more than the price move alone (CLAUDE.md price
    series table: returns are measured on total-return adjusted close)."""
    close = np.array([100.0, 100.0, 100.0])
    adj = np.array([98.0, 99.0, 100.0])
    got = arms.position_returns(close, adj, 1, 2, 100.0, 100.0, Side.LONG)
    assert got[1] == pytest.approx(100.0 / 99.0 - 1)


def test_position_returns_negate_for_a_short():
    close = np.array([100.0, 90.0])
    adj = close.copy()
    got = arms.position_returns(close, adj, 1, 1, 100.0, 90.0, Side.SHORT)
    assert got == pytest.approx((0.10,))


def test_position_returns_compound_to_the_realized_return():
    close = np.array([50.0, 52.0, 49.0, 55.0])
    adj = close.copy()
    got = arms.position_returns(close, adj, 1, 3, 51.0, 54.0, Side.LONG)
    assert float(np.prod([1 + r for r in got])) == pytest.approx(54.0 / 51.0)


# --------------------------------------------------------------------------
# Portfolio simulation
# --------------------------------------------------------------------------


def test_a_single_position_drives_the_whole_curve():
    result = arms.simulate_portfolio(4, [_pos(1, 2, [0.10, 0.10])], BM)
    # Day 0 and day 3 are cash at the risk-free rate.
    rf = arms.daily_risk_free(BM)
    expected = 1.0 * (1 + rf) * 1.10 * 1.10 * (1 + rf)
    assert result.equity.iloc[-1] == pytest.approx(expected)
    assert result.frac_deployed == pytest.approx(0.5)


def test_two_overlapping_positions_are_equal_weighted_that_day():
    a = _pos(0, 0, [0.10], ticker="AAA")
    b = _pos(0, 0, [-0.10], ticker="BBB")
    result = arms.simulate_portfolio(1, [a, b], BM)
    assert result.equity.iloc[-1] == pytest.approx(1.0)
    assert result.frac_deployed == pytest.approx(1.0)


def test_an_empty_arm_earns_the_risk_free_rate_and_reports_zero_deployment():
    result = arms.simulate_portfolio(3, [], BM)
    rf = arms.daily_risk_free(BM)
    assert result.equity.iloc[-1] == pytest.approx((1 + rf) ** 3)
    assert result.frac_deployed == 0.0
    assert result.trades == ()


def test_trade_pnl_sums_to_the_deployed_part_of_the_terminal_value():
    """Attribution has to close: every dollar the curve moved while
    deployed belongs to some trade. `pnl` is in dollars, so it carries the
    `initial_capital` scale the unit-normalized curve does not."""
    positions = [_pos(0, 1, [0.05, -0.02]), _pos(1, 2, [0.03, 0.04], ticker="BBB")]
    result = arms.simulate_portfolio(3, positions, BM)
    total_pnl = sum(t.pnl for t in result.trades)
    growth = (result.equity.iloc[-1] - 1.0) * BM.initial_capital
    assert total_pnl == pytest.approx(growth)


def test_win_rate_counts_trades_with_a_positive_realized_return():
    positions = [_pos(0, 0, [0.05]), _pos(1, 1, [-0.05], ticker="BBB")]
    result = arms.simulate_portfolio(2, positions, BM)
    assert result.win_rate == pytest.approx(0.5)
    assert result.n_trades == 2


def test_win_rate_is_none_with_no_trades():
    """A rate over zero trades is not 0.0 — that reads as "never won"."""
    assert arms.simulate_portfolio(2, [], BM).win_rate is None


def test_simulation_is_deterministic():
    positions = [_pos(0, 2, [0.01, -0.02, 0.03])]
    first = arms.simulate_portfolio(3, positions, BM)
    second = arms.simulate_portfolio(3, positions, BM)
    assert first.equity.equals(second.equity)


# --------------------------------------------------------------------------
# Buy and hold
# --------------------------------------------------------------------------


def test_buy_and_hold_is_deployed_every_single_day():
    """13.1 acceptance, and gate item 5. `frac_deployed` is 1.0 by
    construction, so this is a structural assertion, not a measurement."""
    prices = {
        "AAA": np.array([100.0, 110.0, 120.0]),
        "BBB": np.array([50.0, 50.0, 55.0]),
    }
    members = [("AAA", "BBB"), ("AAA", "BBB"), ("AAA", "BBB")]
    result = arms.simulate_buy_hold(prices, members, BM)
    assert result.frac_deployed == 1.0


def test_buy_and_hold_equal_weights_its_members():
    prices = {
        "AAA": np.array([100.0, 120.0]),
        "BBB": np.array([100.0, 100.0]),
    }
    members = [("AAA", "BBB"), ("AAA", "BBB")]
    result = arms.simulate_buy_hold(prices, members, BM)
    assert result.equity.iloc[-1] == pytest.approx(1.10)


def test_buy_and_hold_sells_a_departing_member_at_the_next_rebalance():
    """13.1 acceptance: a ticker leaving the trade universe is sold at the
    next rebalance, not on the day it leaves."""
    prices = {
        "AAA": np.array([100.0, 100.0, 100.0]),
        "BBB": np.array([100.0, 200.0, 400.0]),
    }
    # BBB is a member on day 1 (its doubling counts) and gone from day 2.
    members = [("AAA", "BBB"), ("AAA", "BBB"), ("AAA",)]
    result = arms.simulate_buy_hold(prices, members, BM)
    # Day 1: mean(0%, +100%) = +50%. Day 2: AAA alone, flat.
    assert result.equity.iloc[-1] == pytest.approx(1.50)


def test_buy_and_hold_ignores_a_ticker_with_no_price_that_day():
    """A delisted or not-yet-listed name carries NaN and must drop out of
    the weighting rather than poison the mean."""
    prices = {
        "AAA": np.array([100.0, 110.0]),
        "BBB": np.array([np.nan, np.nan]),
    }
    members = [("AAA", "BBB"), ("AAA", "BBB")]
    result = arms.simulate_buy_hold(prices, members, BM)
    assert result.equity.iloc[-1] == pytest.approx(1.10)


def test_buy_and_hold_holds_cash_when_no_member_has_a_price():
    prices = {"AAA": np.array([np.nan, np.nan])}
    members = [("AAA",), ("AAA",)]
    result = arms.simulate_buy_hold(prices, members, BM)
    assert result.frac_deployed == 0.0


def test_buy_and_hold_stint_pnl_sums_to_the_curve():
    """A stint closing when a name leaves the universe is a real disposal,
    so ADR 032's tax pass needs dollars on it. Without this the buy-and-hold
    arm reports `post_tax_ret == pre_tax_ret` for the wrong reason: not
    because it owed nothing, but because it had no numbers."""
    prices = {
        "AAA": np.array([100.0, 110.0, 121.0]),
        "BBB": np.array([100.0, 90.0, 90.0]),
    }
    members = [("AAA", "BBB"), ("AAA", "BBB"), ("AAA",)]
    result = arms.simulate_buy_hold(prices, members, BM)
    total_pnl = sum(t.pnl for t in result.trades)
    growth = (result.equity.iloc[-1] - 1.0) * BM.initial_capital
    assert total_pnl == pytest.approx(growth)


def test_a_departing_member_becomes_a_closed_stint_with_a_realized_return():
    prices = {"AAA": np.array([100.0, 100.0, 100.0]), "BBB": np.array([100.0, 80.0, 80.0])}
    members = [("AAA", "BBB"), ("AAA", "BBB"), ("AAA",)]
    result = arms.simulate_buy_hold(prices, members, BM)
    closed = [t for t in result.trades if t.ticker == "BBB"]
    assert len(closed) == 1
    assert closed[0].realized_return == pytest.approx(-0.20)
    assert closed[0].pnl < 0


# --------------------------------------------------------------------------
# Trim and redeploy (13.3)
# --------------------------------------------------------------------------


def test_a_trim_moves_the_configured_fraction_into_cash():
    prices = {"AAA": np.array([100.0, 100.0, 200.0])}
    signals = {"AAA": [(0, "trim")]}
    bm = BenchmarkParams(trim_fraction=0.20, risk_free_annual=0.0)
    result = arms.simulate_trim(prices, _members(3, prices), signals, bm)
    # 20% parked in cash at 100, 80% rides the doubling: 0.20 + 0.80 * 2.
    assert result.equity.iloc[-1] == pytest.approx(1.80)


def test_a_second_trim_takes_the_fraction_of_what_remains():
    """13.3 rule, stated and tested: 20% of the *remaining* position, so two
    trims leave 0.64 invested rather than 0.60."""
    prices = {"AAA": np.array([100.0, 100.0, 100.0])}
    signals = {"AAA": [(0, "trim"), (1, "trim")]}
    bm = BenchmarkParams(trim_fraction=0.20, risk_free_annual=0.0)
    result = arms.simulate_trim(prices, _members(3, prices), signals, bm)
    assert result.invested_fraction_by_ticker["AAA"] == pytest.approx(0.64)


def test_two_trims_and_one_redeploy_is_one_round_trip():
    """13.3 acceptance: `n_round_trips` counts completed pairs, not trims. A
    redeploy closes the whole outstanding cash position however many trims
    opened it."""
    prices = {"AAA": np.array([100.0] * 5)}
    signals = {"AAA": [(0, "trim"), (1, "trim"), (3, "redeploy")]}
    result = arms.simulate_trim(prices, _members(5, prices), signals, BM)
    assert result.n_trims == 2
    assert result.n_round_trips == 1


def test_days_in_cash_runs_from_the_first_unredeployed_trim():
    prices = {"AAA": np.array([100.0] * 6)}
    signals = {"AAA": [(1, "trim"), (2, "trim"), (5, "redeploy")]}
    result = arms.simulate_trim(prices, _members(6, prices), signals, BM)
    assert result.avg_days_in_cash == pytest.approx(4.0)


def test_a_trim_with_no_following_redeploy_stays_in_cash_and_is_reported():
    """13.3 acceptance: report it rather than force-closing."""
    prices = {"AAA": np.array([100.0, 100.0, 100.0])}
    signals = {"AAA": [(0, "trim")]}
    result = arms.simulate_trim(prices, _members(3, prices), signals, BM)
    assert result.n_round_trips == 0
    assert result.n_open_cash_positions == 1


def test_idle_cash_accrues_at_the_risk_free_rate_over_a_known_span():
    """13.3 acceptance: verified against a fixture spanning a known number
    of days. Everything is trimmed, so the whole book is cash for 10 days."""
    prices = {"AAA": np.array([100.0] * 11)}
    bm = BenchmarkParams(trim_fraction=1.0, risk_free_annual=0.05)
    result = arms.simulate_trim(prices, _members(11, prices), {"AAA": [(0, "trim")]}, bm)
    rf = arms.daily_risk_free(bm)
    assert result.equity.iloc[-1] == pytest.approx((1 + rf) ** 10)


def test_never_trimming_reproduces_buy_and_hold():
    """The comparison ADR 017 asks for only means something if the no-trim
    case is the same curve buy-and-hold produces."""
    prices = {"AAA": np.array([100.0, 120.0]), "BBB": np.array([100.0, 100.0])}
    trim = arms.simulate_trim(prices, _members(2, prices), {}, BM)
    hold = arms.simulate_buy_hold(prices, [("AAA", "BBB")] * 2, BM)
    assert trim.equity.iloc[-1] == pytest.approx(hold.equity.iloc[-1])


def test_never_trimming_reproduces_buy_and_hold_under_changing_membership():
    """The version that actually catches the defect.

    A static-membership fixture passes even when the two arms hold different
    books: one a fixed slice of every ticker bought at first appearance, the
    other rebalanced to the current members. Measured on the train split
    that gap was +725% against +413%, entirely from the two arms not holding
    the same names — which is exactly the comparison ADR 017 asks for.
    """
    prices = {
        "AAA": np.array([100.0, 110.0, 120.0, 130.0]),
        "BBB": np.array([50.0, 55.0, 40.0, 44.0]),
        "CCC": np.array([np.nan, 20.0, 22.0, 30.0]),
    }
    members = [("AAA", "BBB"), ("AAA", "BBB", "CCC"), ("AAA", "CCC"), ("AAA", "CCC")]
    trim = arms.simulate_trim(prices, members, {}, BM)
    hold = arms.simulate_buy_hold(prices, members, BM)
    pd.testing.assert_series_equal(trim.equity, hold.equity)


def test_the_book_rebalances_to_equal_weight_when_a_member_joins():
    """DESIGN §6.4: equal-weight, rebalance on universe changes. AAA doubles
    alone, then BBB joins and the two are re-weighted 50/50, so AAA's
    subsequent doubling only moves half the book."""
    prices = {
        "AAA": np.array([100.0, 200.0, 400.0]),
        "BBB": np.array([10.0, 10.0, 10.0]),
    }
    members = [("AAA",), ("AAA", "BBB"), ("AAA", "BBB")]
    hold = arms.simulate_buy_hold(prices, members, BM)
    # Day 1: AAA doubles the whole book to 2.0, then rebalances 50/50.
    # Day 2: AAA doubles half of it -> 2.0 * (0.5 * 2 + 0.5) = 3.0.
    assert hold.equity.iloc[-1] == pytest.approx(3.0)


def test_a_name_that_stops_trading_holds_its_last_price_until_the_rebalance():
    """13.1 rule, stated: a position in a delisted name must resolve rather
    than silently persist. It is valued at its last observed price — no
    gain, no loss — and sold at the next rebalance. Dropping it from the
    valuation instead would book its whole value as a loss on the day the
    bars stopped."""
    prices = {
        "AAA": np.array([100.0, 100.0, 100.0]),
        "BBB": np.array([100.0, np.nan, np.nan]),
    }
    members = [("AAA", "BBB"), ("AAA", "BBB"), ("AAA",)]
    hold = arms.simulate_buy_hold(prices, members, BM)
    assert hold.equity.iloc[2] == pytest.approx(1.0)
    assert hold.equity.iloc[-1] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# DCA (13.4)
# --------------------------------------------------------------------------


def _members(n_days, prices):
    """Static membership over `n_days`, so a trim fixture exercises the trim
    overlay rather than the rebalance path."""
    return [tuple(sorted(prices))] * n_days


def _flat_index(n):
    return np.ones(n, dtype="float64")


def test_every_dca_variant_deploys_exactly_the_full_capital():
    """13.4 acceptance, asserted to the cent."""
    index = np.array([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    months = [0, 2, 4]
    signals = [1, 3]
    results = arms.simulate_dca_family(index, months, signals, n_expected_signals=4, bm=BM)
    for name, result in results.items():
        assert result.capital_deployed == pytest.approx(BM.initial_capital, abs=0.01), name


def test_underfiring_is_reported_and_the_final_day_closes_the_gap():
    """13.4 acceptance: `capital_undeployed` non-zero, and the final-day
    sweep still brings the total to C."""
    index = _flat_index(4)
    results = arms.simulate_dca_family(index, [0], [1], n_expected_signals=4, bm=BM)
    signal_arm = results["dca_signal"]
    # One signal fired where four were expected: three tranches idle.
    assert signal_arm.capital_undeployed == pytest.approx(BM.initial_capital * 0.75)
    assert signal_arm.capital_deployed == pytest.approx(BM.initial_capital, abs=0.01)


def test_a_fully_firing_signal_arm_leaves_nothing_undeployed():
    index = _flat_index(5)
    results = arms.simulate_dca_family(index, [0], [1, 2, 3, 4], n_expected_signals=4, bm=BM)
    assert results["dca_signal"].capital_undeployed == pytest.approx(0.0, abs=0.01)


def test_lump_sum_terminal_value_equals_the_index_growth():
    index = np.array([1.0, 2.0])
    results = arms.simulate_dca_family(index, [0], [], n_expected_signals=1, bm=BM)
    assert results["dca_lump"].terminal_value == pytest.approx(2 * BM.initial_capital)


def test_lump_sum_agrees_with_buy_and_hold_on_a_single_ticker():
    """13.4 acceptance: the cross-arm consistency check. Lump sum is
    buy-and-hold with a dollar amount attached, so if these disagree one of
    the two simulators is wrong."""
    prices = {"AAA": np.array([100.0, 130.0, 125.0])}
    hold = arms.simulate_buy_hold(prices, [("AAA",)] * 3, BM)
    results = arms.simulate_dca_family(
        hold.equity.to_numpy()[1:], [0], [], n_expected_signals=1, bm=BM
    )
    assert results["dca_lump"].terminal_value == pytest.approx(
        BM.initial_capital * hold.equity.iloc[-1]
    )


def test_hybrid_doubles_the_tranche_on_a_signal_day():
    index = _flat_index(4)
    plain = arms.simulate_dca_family(index, [0, 1], [], n_expected_signals=1, bm=BM)
    both = arms.simulate_dca_family(index, [0, 1], [1], n_expected_signals=1, bm=BM)
    assert both["dca_hybrid"].n_deployments <= plain["dca_hybrid"].n_deployments
    assert both["dca_hybrid"].avg_cost_basis == pytest.approx(1.0)


def test_average_cost_basis_is_capital_weighted():
    index = np.array([1.0, 3.0, 3.0])
    # Two equal tranches, one at index 1.0 and one at 3.0.
    results = arms.simulate_dca_family(
        index, [0, 1], [], n_expected_signals=1, bm=BenchmarkParams(dca_tranches=2)
    )
    fixed = results["dca_fixed"]
    # 0.5C buys 0.5C units at 1.0; 0.5C buys C/6 units at 3.0.
    expected = 1.0 / (0.5 + 1.0 / 6.0)
    assert fixed.avg_cost_basis == pytest.approx(expected)


def test_a_signal_variant_that_waits_reports_less_than_full_deployment():
    """`dca_signal` holds cash until its first signal. Assuming 1.0 would
    overstate deployment and understate capital efficiency."""
    index = _flat_index(10)
    results = arms.simulate_dca_family(index, [0], [8], n_expected_signals=1, bm=BM)
    assert results["dca_signal"].frac_deployed == pytest.approx(0.2)
    assert results["dca_lump"].frac_deployed == pytest.approx(1.0)


def test_a_partly_deployed_variant_draws_down_less_than_the_basket():
    """Cash does not fall with the basket, so a variant still holding it
    through a decline has a shallower drawdown than the index."""
    index = np.array([1.0, 1.0, 0.5, 0.5])
    results = arms.simulate_dca_family(index, [0], [3], n_expected_signals=1, bm=BM)
    assert results["dca_lump"].max_drawdown == pytest.approx(0.5)
    assert results["dca_signal"].max_drawdown == pytest.approx(0.0)


def test_cash_drag_is_zero_for_lump_sum_and_positive_for_a_laggard():
    index = np.array([1.0, 2.0, 4.0])
    results = arms.simulate_dca_family(index, [0, 2], [], n_expected_signals=1, bm=BM)
    assert results["dca_lump"].cash_drag == pytest.approx(0.0)
    assert results["dca_fixed"].cash_drag > 0.0


# --------------------------------------------------------------------------
# IRR (13.4)
# --------------------------------------------------------------------------


def test_irr_of_a_hand_computed_three_flow_case():
    """13.4 acceptance. -100 today, -100 in a year, +220 in two years.

    At exactly annual spacing the discount factor `x = 1/(1+r)` solves
    `220x^2 - 100x - 100 = 0`, giving `x = 0.93875` and `r = 6.53%`. The
    dates below span a leap year, so the measured answer sits marginally
    below that closed form, which is why the NPV check carries the
    correctness load and the rate check carries the sanity load.
    """
    flows = [
        (date(2020, 1, 1), -100.0),
        (date(2021, 1, 1), -100.0),
        (date(2022, 1, 1), 220.0),
    ]
    rate = arms.irr(flows, BM)
    assert rate is not None
    npv = sum(
        amount / (1 + rate) ** ((when - flows[0][0]).days / BM.irr_days_per_year)
        for when, amount in flows
    )
    assert npv == pytest.approx(0.0, abs=1e-6)
    assert rate == pytest.approx(0.0652, abs=5e-4)


def test_irr_of_a_doubling_over_one_year_is_one_hundred_percent():
    """2021 is not a leap year, so this span is exactly `irr_days_per_year`
    and the answer is exactly 100%."""
    flows = [(date(2021, 1, 1), -100.0), (date(2022, 1, 1), 200.0)]
    assert arms.irr(flows, BM) == pytest.approx(1.0, abs=1e-6)


def test_irr_is_none_when_no_sign_change_exists():
    """No root, so no rate. None rather than a bracket endpoint."""
    flows = [(date(2020, 1, 1), -100.0), (date(2021, 1, 1), -100.0)]
    assert arms.irr(flows, BM) is None


# --------------------------------------------------------------------------
# Tax and wash sales (13.5)
# --------------------------------------------------------------------------


def _trade(ticker, entry, exit_, pnl):
    return arms.Trade(
        ticker=ticker,
        side=Side.LONG,
        entry_date=entry,
        exit_date=exit_,
        entry_price=100.0,
        exit_price=100.0,
        realized_return=pnl / 1000.0,
        notional=1000.0,
        pnl=pnl,
    )


def test_a_loss_closed_twenty_nine_days_after_a_purchase_flags():
    """13.5 acceptance, the earlier side of the symmetric window."""
    loss = _trade("AAA", date(2020, 3, 1), date(2020, 3, 30), -50.0)
    purchases = [("AAA", date(2020, 3, 1)), ("AAA", date(2020, 3, 2))]
    flags, any_flag = arms.wash_sale_flags([loss], purchases, BM)
    assert flags == (True,)
    assert any_flag is True


def test_a_loss_closed_thirty_one_days_after_the_only_purchase_does_not_flag():
    loss = _trade("AAA", date(2020, 1, 1), date(2020, 3, 30), -50.0)
    purchases = [("AAA", date(2020, 1, 1)), ("AAA", date(2020, 2, 27))]
    flags, _ = arms.wash_sale_flags([loss], purchases, BM)
    assert flags == (False,)


def test_a_purchase_thirty_one_days_after_the_loss_does_not_flag():
    """The other side. Only testing the earlier direction would pass a
    one-sided implementation."""
    loss = _trade("AAA", date(2020, 1, 1), date(2020, 3, 1), -50.0)
    purchases = [("AAA", date(2020, 1, 1)), ("AAA", date(2020, 4, 1))]
    flags, _ = arms.wash_sale_flags([loss], purchases, BM)
    assert flags == (False,)


def test_a_purchase_twenty_nine_days_after_the_loss_flags():
    loss = _trade("AAA", date(2020, 1, 1), date(2020, 3, 1), -50.0)
    purchases = [("AAA", date(2020, 1, 1)), ("AAA", date(2020, 3, 30))]
    flags, _ = arms.wash_sale_flags([loss], purchases, BM)
    assert flags == (True,)


def test_the_window_is_calendar_days_not_trading_days():
    """13.5: the most likely place to be quietly wrong. 30 calendar days
    after 2020-03-01 is 2020-03-31; the 21st trading day is not."""
    loss = _trade("AAA", date(2020, 1, 1), date(2020, 3, 1), -50.0)
    inside = arms.wash_sale_flags([loss], [("AAA", date(2020, 3, 31))], BM)[0]
    outside = arms.wash_sale_flags([loss], [("AAA", date(2020, 4, 1))], BM)[0]
    assert inside == (True,)
    assert outside == (False,)


def test_the_trades_own_entry_never_triggers_its_own_flag():
    """A five-day hold would otherwise flag every losing trade in the
    system, which is a statement about the holding period, not a wash
    sale. Replacement shares are what the rule is about."""
    loss = _trade("AAA", date(2020, 3, 1), date(2020, 3, 6), -50.0)
    flags, _ = arms.wash_sale_flags([loss], [("AAA", date(2020, 3, 1))], BM)
    assert flags == (False,)


def test_a_core_position_purchase_alone_triggers_the_flag():
    """13.5 acceptance: the sleeve on its own would not flag, and the core
    holding is what makes it a wash sale."""
    loss = _trade("AAA", date(2020, 3, 1), date(2020, 3, 6), -50.0)
    sleeve_only = arms.wash_sale_flags([loss], [("AAA", date(2020, 3, 1))], BM)[0]
    with_core = arms.wash_sale_flags(
        [loss], [("AAA", date(2020, 3, 1)), ("AAA", date(2020, 3, 20))], BM
    )[0]
    assert sleeve_only == (False,)
    assert with_core == (True,)


def test_a_different_ticker_never_triggers_the_flag():
    loss = _trade("AAA", date(2020, 3, 1), date(2020, 3, 6), -50.0)
    flags, _ = arms.wash_sale_flags([loss], [("BBB", date(2020, 3, 5))], BM)
    assert flags == (False,)


def test_a_winning_trade_is_never_a_wash_sale():
    win = _trade("AAA", date(2020, 3, 1), date(2020, 3, 6), 50.0)
    flags, _ = arms.wash_sale_flags([win], [("AAA", date(2020, 3, 5))], BM)
    assert flags == (False,)


def test_post_tax_is_never_above_pre_tax_when_the_arm_has_net_gains():
    """13.5 acceptance, asserted directly."""
    trades = [_trade("AAA", date(2020, 1, 1), date(2020, 1, 6), 500.0)]
    pre, post, _ = arms.tax_summary(trades, [], 0.10, BM)
    assert pre == 0.10
    assert post <= pre


def test_short_term_tax_takes_the_configured_rate_off_the_net_gain():
    trades = [_trade("AAA", date(2020, 1, 1), date(2020, 1, 6), 1000.0)]
    bm = BenchmarkParams(initial_capital=10_000.0, short_term_tax_rate=0.30)
    pre, post, _ = arms.tax_summary(trades, [], 0.10, bm)
    # 1000 of gain, 300 of tax, on 10,000 of capital: 3 points of return.
    assert post == pytest.approx(0.07)


def test_a_disallowed_loss_moves_the_reported_number_not_only_the_flag():
    """13.5 acceptance: a flag with no numeric consequence would pass a
    careless test. Disallowing the loss raises taxable income, so post-tax
    return falls."""
    trades = [
        _trade("AAA", date(2020, 1, 1), date(2020, 1, 6), 1000.0),
        _trade("AAA", date(2020, 2, 1), date(2020, 2, 6), -400.0),
    ]
    bm = BenchmarkParams(initial_capital=10_000.0, short_term_tax_rate=0.30)
    clean = arms.tax_summary(trades, [], 0.06, bm)
    washed = arms.tax_summary(trades, [("AAA", date(2020, 2, 20))], 0.06, bm)
    assert washed[2] is True
    assert clean[2] is False
    assert washed[1] < clean[1]
    # 600 taxable becomes 1000 taxable: 120 more tax on 10,000 of capital.
    assert clean[1] - washed[1] == pytest.approx(0.012)


def test_a_loss_cannot_offset_a_gain_from_a_different_tax_year():
    """Pooling every year into one net figure would let a 2021 loss offset a
    2011 gain, which no tax year permits. On a twelve-year window that is
    the difference between a plausible number and a nonsense one."""
    trades = [
        _trade("AAA", date(2011, 1, 1), date(2011, 1, 6), 1000.0),
        _trade("AAA", date(2021, 1, 1), date(2021, 1, 6), -1000.0),
    ]
    bm = BenchmarkParams(initial_capital=10_000.0, short_term_tax_rate=0.30)
    _, post, _ = arms.tax_summary(trades, [], 0.0, bm)
    # 2011 owes 300 on its gain; 2021's loss is not a refund.
    assert post == pytest.approx(-0.03)


def test_a_wash_sale_loss_is_deferred_into_the_next_year_not_destroyed():
    """The rule adds the disallowed loss to the replacement lot's basis, so
    it comes back. Treating it as permanently gone produced a tax bill
    several times the account over a twelve-year window."""
    trades = [
        _trade("AAA", date(2020, 1, 1), date(2020, 6, 1), -1000.0),
        _trade("AAA", date(2021, 3, 1), date(2021, 6, 1), 1000.0),
    ]
    bm = BenchmarkParams(initial_capital=10_000.0, short_term_tax_rate=0.30)
    # A purchase 10 days after the 2020 loss makes it a wash sale.
    washed = arms.tax_summary(trades, [("AAA", date(2020, 6, 11))], 0.0, bm)
    assert washed[2] is True
    # The deferred 1,000 offsets 2021's 1,000 gain, so nothing is owed.
    assert washed[1] == pytest.approx(0.0)


def test_deferral_still_costs_when_it_lands_in_a_lossmaking_year():
    """The flag has to have a numeric consequence (13.5 acceptance).
    Deferring a loss out of a profitable year into one with no gains to
    offset means it never offsets anything."""
    trades = [
        _trade("AAA", date(2020, 1, 1), date(2020, 6, 1), 1000.0),
        _trade("AAA", date(2020, 7, 1), date(2020, 8, 1), -600.0),
    ]
    bm = BenchmarkParams(initial_capital=10_000.0, short_term_tax_rate=0.30)
    clean = arms.tax_summary(trades, [], 0.04, bm)
    washed = arms.tax_summary(trades, [("AAA", date(2020, 8, 20))], 0.04, bm)
    assert clean[2] is False
    assert washed[2] is True
    # 400 taxable becomes 1,000 taxable: 180 more tax on 10,000 of capital.
    assert clean[1] - washed[1] == pytest.approx(0.018)


def test_a_net_loss_arm_owes_no_tax_and_reports_equal_returns():
    """No carry-forward is modeled, so a net loss is not a refund."""
    trades = [_trade("AAA", date(2020, 1, 1), date(2020, 1, 6), -1000.0)]
    pre, post, _ = arms.tax_summary(trades, [], -0.10, BM)
    assert post == pytest.approx(pre)


# --------------------------------------------------------------------------
# Percentiles (13.2)
# --------------------------------------------------------------------------


def test_the_null_percentile_is_read_off_the_stored_values():
    values = [float(i) for i in range(1, 201)]
    assert arms.null_percentile(values, 97.5) == pytest.approx(
        float(np.percentile(np.array(values), 97.5))
    )


def test_a_signal_above_the_percentile_is_significant():
    null = [float(i) for i in range(200)]
    threshold = arms.null_percentile(null, 97.5)
    assert threshold == pytest.approx(194.025)
    assert arms.exceeds_null(195.0, null, 97.5) is True
    # Inside the null's own range but below its upper tail: the whole point
    # of a 97.5th-percentile criterion is that beating the median is not it.
    assert arms.exceeds_null(150.0, null, 97.5) is False


def test_percentile_of_an_empty_null_is_none():
    assert arms.null_percentile([], 97.5) is None
    assert arms.exceeds_null(1.0, [], 97.5) is None
