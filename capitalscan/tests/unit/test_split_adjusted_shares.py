"""Market cap must multiply price and shares on the **same** split basis.

Found 2026-08-21 while explaining why CHRW had only two months of events.
It did not: CHRW is genuinely in-trade for one quarter. But the criterion
that decides that, `crit_mcap`, was reading a market cap understated by the
cumulative split factor for 446 of ~929 tickers.

    mcap = shares * float(ind_row["close"])     # compute.py:599

`close` is split-adjusted, and Yahoo re-adjusts the *entire* history every
time a new split lands. `shares` is the count as filed that quarter. The
two agree only when no split has happened since the filing.

Measured, AAPL: $11.1B at 2011-06-30 against a real ~$310B. The ratio is
28 = 7 (2014) x 4 (2020), exactly the splits between the filing and today,
and it falls to 4x by 2016 and to 1x by 2021 as those splits are absorbed.

This is the same defect class as `adr_adjusted_shares` (TSM, 5x) one layer
deeper: a share count on a different basis than the price it multiplies.
"""

from __future__ import annotations

import pytest

from capitalscan.core.universe import split_adjusted_shares


class TestTheAdjustmentItself:
    def test_no_splits_leaves_the_count_alone(self):
        assert split_adjusted_shares(1_000.0, ()) == 1_000.0

    def test_one_split_scales_by_its_ratio(self):
        assert split_adjusted_shares(1_000.0, (4.0,)) == 4_000.0

    def test_several_splits_compound(self):
        assert split_adjusted_shares(1_000.0, (7.0, 4.0)) == 28_000.0

    def test_fractional_ratios_are_honoured(self):
        # 3-for-2 splits are real and common: KLAC 1984, NVDA 2007.
        assert split_adjusted_shares(1_000.0, (1.5,)) == 1_500.0

    def test_none_passes_through_rather_than_becoming_zero(self):
        # Invariant 4's shape, and `adr_adjusted_shares`'s own rule: "no
        # filing yet" is not "no shares". A 0.0 here would price the company
        # at nothing and fail `crit_mcap` as though it had been measured.
        assert split_adjusted_shares(None, (4.0,)) is None

    def test_order_does_not_matter(self):
        # Multiplication commutes, so the caller need not sort by ex_date.
        assert split_adjusted_shares(10.0, (7.0, 4.0)) == split_adjusted_shares(10.0, (4.0, 7.0))


class TestTheRealCases:
    """The numbers that exposed this, reproduced.

    Tolerances are wide on purpose: these assert the *order of magnitude* is
    now right, not that a scraped reference price matches to the cent.
    """

    def test_aapl_2011_prices_at_roughly_310B_not_11B(self):
        shares_filed = 924_754_561.0  # 10-Q filed 2011-04-21
        adjusted_close = 11.9882  # bars.close at 2011-06-30, today's basis
        splits_since = (7.0, 4.0)  # 2014-06-09, 2020-08-31

        broken = shares_filed * adjusted_close
        fixed = split_adjusted_shares(shares_filed, splits_since) * adjusted_close

        assert broken == pytest.approx(11.1e9, rel=0.05)
        assert fixed == pytest.approx(310e9, rel=0.05)

    def test_aapl_2021_was_already_correct(self):
        """The control. No split after the filing means no correction, so a
        fix that moved this number would be wrong in the other direction."""
        shares_filed = 16_687_631_000.0  # filed 2021-04-29
        adjusted_close = 136.96
        assert split_adjusted_shares(shares_filed, ()) * adjusted_close == pytest.approx(
            2285e9, rel=0.05
        )

    def test_klac_2026_absorbs_its_ten_for_one(self):
        shares_filed = 130_627_521.0  # filed 2026-04-30, before the split
        adjusted_close = 301.71  # 2026-06-30
        splits_since = (10.0,)  # 2026-06-12

        broken = shares_filed * adjusted_close
        fixed = split_adjusted_shares(shares_filed, splits_since) * adjusted_close
        assert fixed == pytest.approx(broken * 10, rel=1e-9)


class TestWhichSplitsCount:
    """**Every split after the filing, including ones after `as_of`.**

    That reads like look-ahead and is not. The price series is expressed in
    *today's* basis, so a 2011 close already embeds the 2014 and 2020
    splits. Market cap is split-invariant -- the factor cancels -- so the
    only requirement is that both sides use one basis. Filtering to splits
    on or before `as_of` would leave price adjusted further than shares,
    which is precisely the bug.
    """

    def test_a_split_after_as_of_still_counts(self):
        # AAPL at as_of 2011-06-30 must absorb the 2014 and 2020 splits,
        # because bars.close already has.
        assert split_adjusted_shares(924_754_561.0, (7.0, 4.0)) == pytest.approx(25.89e9, rel=0.01)

    def test_the_product_is_invariant_to_the_basis_chosen(self):
        """Price x shares is the same number on any consistent basis.

        This is *why* using future splits is safe, stated as a property
        rather than as prose: adjust both sides by the same factor and the
        market cap does not move.
        """
        shares_then, price_then = 924_754_561.0, 335.67
        factor = 28.0
        on_today_basis = split_adjusted_shares(shares_then, (factor,)) * (price_then / factor)
        assert on_today_basis == pytest.approx(shares_then * price_then, rel=1e-9)
