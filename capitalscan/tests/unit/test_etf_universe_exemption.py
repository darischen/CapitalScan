"""An ETF is in the trade universe unconditionally (ADR 154).

The four criteria in `UniverseParams.required_criteria` ask a
company-shaped question. `crit_mcap` is shares times price, which for a
fund is net assets rather than capitalisation, and `crit_rel_return` wants
757 sessions a fund launched last year cannot have.

Applied to funds, they produced an outcome decided by *data availability*:
QQQ and SPY qualified because Yahoo happens to serve their share counts,
while VOO -- passing SMA200, its slope and relative return -- was excluded
on the missing number alone.

The exemption is safe precisely because `ETF_TICKERS` answers a different
question from the criteria: "is this an instrument rather than a company".
ADR 147 keeps the same list out of training.
"""

from __future__ import annotations

import pytest

from capitalscan.core import universe as uni
from capitalscan.core.config import UniverseParams
from capitalscan.core.training import ETF_TICKERS, is_etf

REQUIRED = set(UniverseParams().required_criteria)


def _failing() -> dict[str, bool | None]:
    """VOO's real 2026Q2 shape: everything passes except market cap, which
    could not be computed at all."""
    return {
        "crit_mcap": None,
        "crit_above_sma200": True,
        "crit_sma200_slope": True,
        "crit_rel_return": True,
        "crit_rev_growth": None,
    }


def _passing() -> dict[str, bool | None]:
    return dict.fromkeys(REQUIRED, True) | {"crit_rev_growth": None}


# ---------------------------------------------------------------------------
# The exemption
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ticker", sorted(ETF_TICKERS))
def test_every_etf_is_admitted_regardless_of_criteria(ticker: str):
    """Including the ones that fail every criterion.

    IBIT fails market cap, sits below its SMA200 with a negative slope, and
    has 656 bars against the 757 `crit_rel_return` needs. It is still
    admitted, because the criteria were never asking it a question it could
    answer.
    """
    nothing_passes = dict.fromkeys(REQUIRED, False) | {"crit_rev_growth": None}
    assert uni.is_tradeable_instrument(ticker, nothing_passes, REQUIRED) is True


def test_voo_is_admitted_on_the_one_missing_input():
    """The case that motivated this. A ~$600B S&P 500 tracker excluded by a
    share count Yahoo does not serve."""
    assert uni.is_tradeable_instrument("VOO", _failing(), REQUIRED) is True


def test_an_equity_still_has_to_pass():
    """The exemption is for funds and nothing else. If this ever admits an
    operating company on a missing market cap, ADR 129's failure -- 18,805
    events admitted without evaluation -- has returned by another route.
    """
    assert uni.is_tradeable_instrument("AAPL", _failing(), REQUIRED) is False


def test_an_equity_that_passes_is_still_admitted():
    assert uni.is_tradeable_instrument("AAPL", _passing(), REQUIRED) is True


def test_the_ticker_match_is_case_insensitive():
    """`is_etf` upper-cases, and a lower-cased ticker reaching this from a
    hand-written query should not silently take the equity path."""
    assert uni.is_tradeable_instrument("voo", _failing(), REQUIRED) is True


def test_a_missing_ticker_takes_the_criteria_path():
    """`None` is not an ETF. Failing open on an absent identifier is the
    ADR 129 failure exactly."""
    assert uni.is_tradeable_instrument(None, _failing(), REQUIRED) is False


# ---------------------------------------------------------------------------
# What the exemption must not touch
# ---------------------------------------------------------------------------


def test_is_tradeable_itself_is_unchanged():
    """The criteria function stays company-shaped and ticker-blind.

    The ablation arm (DESIGN §3.10) passes criteria subsets through it, and
    an exemption living inside it would silently widen every ablation.
    """
    assert uni.is_tradeable(_failing(), REQUIRED) is False
    assert uni.is_tradeable(_passing(), REQUIRED) is True


@pytest.mark.parametrize("ticker", sorted(ETF_TICKERS))
def test_every_admitted_etf_is_still_excluded_from_training(ticker: str):
    """The two halves of ADR 154 in one assertion.

    Admitting a fund to the trade universe while it trains the model on
    fund behaviour is the failure this pair exists to prevent. ADR 147
    already answers the training half; this checks the lists have not
    drifted apart, which is what BACKLOG predicted would happen.
    """
    assert is_etf(ticker) is True


def test_the_two_lists_agree_today():
    """They are deliberately separate and currently identical.

    A divergence is allowed by design -- an ETF sponsor could in principle
    file with SEC -- but it should be a decision someone made, not a
    ticker added to one list and forgotten in the other.
    """
    from capitalscan.jobs.ingest import SEC_NON_FILER_TICKERS

    assert ETF_TICKERS == SEC_NON_FILER_TICKERS


def test_spy_is_on_the_list():
    """It was on neither, in a universe seeded from S&P 500 membership."""
    assert "SPY" in ETF_TICKERS
