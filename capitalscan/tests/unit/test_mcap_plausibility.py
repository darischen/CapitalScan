"""A market cap no company could have does not reach `crit_mcap`.

`SharesPlausibility` guards the *filing*, and documents why it cannot close
the x1,000 class: "a x1,000 error on a company with real shares in the tens
of millions now lands inside `[min_shares, max_shares]` and is accepted
undetected". It names the tickers — AAP, GRMN, PKG, ALK, FTNT, SWKS, MAA,
AIZ, CNX, EOG, PNR, REG — and accepts them deliberately, because rejecting
a genuine filing freezes that ticker's share count forever and silently,
while a bad one surfaces as an absurd market cap.

This guard sits at the other end, on the derived value rather than the
input, where neither of those properties holds: nulling one quarter's
`mcap_usd` excludes that ticker for that quarter only, and the next quarter
recomputes from scratch.

**It logs rather than merely nulling, and that is the whole point.** The
absurd market cap *is* the detection mechanism — it is how the original
32B-ceiling defect was found and how this one was. A guard that silently
replaced a loud wrong number with a quiet missing one would repeat the
error `SharesPlausibility` warns against, one layer up. Logging makes it
strictly more visible than today: an explicit `bar_rejects` row instead of
something you have to go looking for.
"""

from __future__ import annotations

import pytest

from capitalscan.core.config import McapPlausibility
from capitalscan.core.universe import implausible_mcap_reason
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH

BOUNDS = McapPlausibility()


class TestTheBound:
    def test_a_normal_mega_cap_passes(self):
        # AAPL sat at $4.25T on 2026-06-30 in this database. The bound has
        # to clear the largest real company by a comfortable margin or it
        # rejects the truth.
        assert implausible_mcap_reason(4.25e12, BOUNDS) is None

    def test_the_bound_clears_the_largest_real_company(self):
        assert BOUNDS.max_mcap_usd > 5e12

    @pytest.mark.parametrize(
        ("ticker", "mcap"),
        [("BNTX", 65.9e12), ("PKG", 18.9e12), ("GRMN", 13.9e12), ("AAP", 6.5e12)],
    )
    def test_impossible_values_are_caught(self, ticker, mcap):
        assert implausible_mcap_reason(mcap, BOUNDS) == "mcap_above_plausible_ceiling"

    def test_none_passes_through(self):
        """ "No shares on file" is already `None` and must stay `None`, not
        become a rejection — nothing was measured, so nothing is wrong."""
        assert implausible_mcap_reason(None, BOUNDS) is None

    def test_zero_and_negative_are_not_silently_accepted(self):
        # A non-positive market cap is not a small company, it is a bad
        # computation. `crit_mcap` would fail it anyway; naming it makes the
        # cause visible in `bar_rejects` instead of looking like a real miss.
        assert implausible_mcap_reason(0.0, BOUNDS) == "mcap_not_positive"
        assert implausible_mcap_reason(-1.0, BOUNDS) == "mcap_not_positive"


class TestTheDocumentedGap:
    """**What this does not catch, stated rather than implied.**

    The ceiling is a backstop against the *impossible*, not a fix for the
    merely wrong. ALK carried $2,453B — plainly wrong for Alaska Air, and
    comfortably inside any bound that also has to clear AAPL at $4.25T.
    Tightening far enough to catch ALK would start rejecting real mega-caps,
    which is the trade `SharesPlausibility` already refused at the ingest
    end for the same reason.
    """

    def test_a_wrong_but_possible_value_is_not_caught(self):
        assert implausible_mcap_reason(2.453e12, BOUNDS) is None

    def test_the_gap_is_bounded_by_the_largest_real_company(self):
        # Any value between the largest real company and the ceiling is
        # unreachable by this guard, and that interval is the known gap.
        assert BOUNDS.max_mcap_usd < 20e12, (
            "a ceiling this high stops catching the x1000 class it exists for"
        )


class TestItIsNotAConfigField:
    """Changing `config_hash` for a value that cannot change a backtest
    result would invalidate every existing config, the same reasoning
    `SweepParams` and `SharesPlausibility` already carry."""

    def test_config_hash_is_unmoved(self):
        from capitalscan.core.config import Config
        from capitalscan.jobs.config import config_hash

        assert config_hash(Config()) == DEFAULT_CONFIG_HASH
