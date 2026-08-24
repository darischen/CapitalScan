"""ADR 149: the watch universe, a sibling of `in_trade` rather than a variant.

`in_trade` requires all four of `UniverseParams.required_criteria`. Two
populations sit just outside it for reasons that are not "the filter judged
this name and rejected it":

- **Want of history.** `crit_rel_return` needs 757 daily bars. GE Vernova
  was spun out of GE in April 2024 and has 603, so its trailing three-year
  return is *undefined*, not bad. Six names, $1.18T, including GEV at
  $315.7B and ARM at $377.3B.
- **A pullback inside an intact uptrend.** `crit_sma200_slope` true with
  `crit_above_sma200` false means the 200-day average is still **rising**
  while price sits below it. Twenty-two names, $2.75T — WMT, COST, WFC,
  TJX, GILD — not falling knives.

**Why those two and not "any three of four".** Measured 2026-08-24: three
passing with one NULL is 6 tickers and $1.18T; three passing with one
*False* is 247 tickers and $14.85T. The second is not a wider watchlist, it
is the universe with the filter switched off, and it admits names *because*
they failed the test that would have excluded them.

The pullback carve-out survives that objection because it is not "any
failure". It is one specific failure conditioned on the trend gate still
passing, and TSLA shows the discrimination working in both directions:

    2024-06-30  above_sma F, slope F  -> real downtrend, stays out
    2026-03-31  above_sma F, slope T  -> $1.4T dip in an uptrend, admitted

`crit_mcap` is never waived. It is a designed floor, and it is what already
keeps the genuinely dangerous names out — CHGG has no `universe` row at all,
because $20B filters it before any criterion runs.
"""

from __future__ import annotations

import pytest

from capitalscan.core.universe import WATCH_HISTORY, WATCH_PULLBACK, watch_reason

FULL_HISTORY = 757
SHORT_HISTORY = 603  # GEV's actual bar count


def _crit(mcap=True, above=True, slope=True, rel=True):
    return {
        "crit_mcap": mcap,
        "crit_above_sma200": above,
        "crit_sma200_slope": slope,
        "crit_rel_return": rel,
    }


class TestWantOfHistory:
    def test_gev_shape_is_admitted(self):
        """All judgeable criteria pass; rel_return is unknowable."""
        assert watch_reason(_crit(rel=None), bars=SHORT_HISTORY, stale=False) == WATCH_HISTORY

    def test_a_null_rel_return_with_full_history_is_not_a_history_case(self):
        """The clause that keeps the badge honest.

        `crit_rel_return` is `_cmp(rel_return_756d, sector_median)` and is
        NULL when **either** side is missing. A sector-median failure on a
        fifteen-year-old ticker is also NULL, and calling that "insufficient
        history" would be a false statement about the data. Without the bar
        count this is indistinguishable from GEV.
        """
        assert watch_reason(_crit(rel=None), bars=FULL_HISTORY, stale=False) is None


class TestPullbackInAnUptrend:
    def test_tsla_2026_03_31_is_admitted(self):
        """Price below a *rising* 200-day average."""
        assert watch_reason(_crit(above=False), bars=FULL_HISTORY, stale=False) == WATCH_PULLBACK

    def test_tsla_2024_06_30_is_not(self):
        """The same name in a real downtrend: the slope gate is false, so the
        pullback reading is unavailable and it stays out."""
        assert (
            watch_reason(
                _crit(above=False, slope=False, rel=False),
                bars=FULL_HISTORY,
                stale=False,
            )
            is None
        )

    def test_a_falling_average_is_never_a_pullback(self):
        assert watch_reason(_crit(above=False, slope=False), bars=FULL_HISTORY, stale=False) is None


class TestTheHardRequirements:
    def test_market_cap_is_never_waived(self):
        """A designed floor, not a signal. It is what already excludes the
        dangerous names before any other criterion runs."""
        assert watch_reason(_crit(mcap=False, rel=None), bars=SHORT_HISTORY, stale=False) is None
        assert watch_reason(_crit(mcap=False, above=False), bars=FULL_HISTORY, stale=False) is None

    def test_the_slope_gate_is_never_waived(self):
        assert watch_reason(_crit(slope=False, rel=None), bars=SHORT_HISTORY, stale=False) is None

    def test_a_stale_evaluation_is_never_admitted(self):
        """ADR 135, or AET comes back.

        Aetna was acquired 2018-11-29 and its criteria still compute TRUE at
        2026-06-30 off a frozen November 2018 indicator row — that defect
        produced **31 consecutive quarters `in_trade` with no bars behind
        any of them**. `in_watch` is a sibling universe and inherits every
        safeguard `in_trade` has; without this clause the watchlist fills
        with delisted companies that look like discoveries.
        """
        assert watch_reason(_crit(rel=None), bars=SHORT_HISTORY, stale=True) is None
        assert watch_reason(_crit(above=False), bars=FULL_HISTORY, stale=True) is None


class TestDisjointFromInTrade:
    def test_a_fully_qualified_name_is_not_watched(self):
        """`in_trade` and `in_watch` are mutually exclusive.

        A name graduates from watch to trade at 757 bars and never holds
        both, so "which population is this row in" always has one answer.
        """
        assert watch_reason(_crit(), bars=FULL_HISTORY, stale=False) is None

    def test_the_two_reasons_are_distinguishable(self):
        history = watch_reason(_crit(rel=None), bars=SHORT_HISTORY, stale=False)
        pullback = watch_reason(_crit(above=False), bars=FULL_HISTORY, stale=False)
        assert history != pullback
        assert {history, pullback} == {WATCH_HISTORY, WATCH_PULLBACK}


class TestNotAnyThreeOfFour:
    """The measurement that bounded the feature: 6 tickers versus 247."""

    @pytest.mark.parametrize(
        "criteria",
        [
            _crit(rel=False),  # judged on three years and failed
            _crit(above=False, rel=False),  # pullback *and* weak relative
            _crit(slope=False),  # trend gate failed
            _crit(mcap=False),  # under the floor
        ],
    )
    def test_a_failed_criterion_is_not_a_watch_reason(self, criteria):
        """`False` is evidence; `None` is the absence of it. Admitting on a
        failure would put a name in the watchlist *because* it failed the
        test that would have kept it out."""
        assert watch_reason(criteria, bars=FULL_HISTORY, stale=False) is None


class TestTwoShortfallsIsNotThreeOfFour:
    """A new name that is *also* below its 200-day average is not watched.

    GEV in a dip has `crit_rel_return` unknowable **and**
    `crit_above_sma200` false, so only two of the four hold. The rule is
    "three of four with the fourth unjudgeable-or-a-pullback", not "at most
    two shortfalls", and this is the row that shows the difference.

    Deliberately conservative: one thing unproven is a candidate, two is a
    different name. Whichever reason were returned would also be a claim the
    row does not support -- `history` would imply the trend test passed, and
    `pullback` would imply the relative-strength test did.
    """

    def test_new_and_below_the_average_is_excluded(self):
        assert (
            watch_reason(
                {
                    "crit_mcap": True,
                    "crit_sma200_slope": True,
                    "crit_above_sma200": False,
                    "crit_rel_return": None,
                },
                bars=SHORT_HISTORY,
                stale=False,
            )
            is None
        )
