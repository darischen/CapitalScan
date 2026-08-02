"""Unit tests for core/universe.py.

The five ADR 014 criteria come back **separately**, which is what makes the
ablation study a config change rather than a code change (DESIGN §3.10).
The sharp case is revenue growth: fewer than four reported quarters means
`None`, not `False`. `is_tradeable` treats both as failing, but the audit
log has to be able to tell them apart (DESIGN §4.6).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import universe as uni
from capitalscan.core.config import UniverseParams

UP = UniverseParams()

CRITERIA = {
    "crit_mcap",
    "crit_above_sma200",
    "crit_sma200_slope",
    "crit_rel_return",
    "crit_rev_growth",
}


def _ind(close=100.0, sma_200=90.0, sma200_slope_60=0.05, rel_return_756d=0.60):
    return pd.Series(
        {
            "close": close,
            "sma_200": sma_200,
            "sma200_slope_60": sma200_slope_60,
            "rel_return_756d": rel_return_756d,
        }
    )


def _healthy(**kw):
    args = dict(
        ind_row=_ind(),
        mcap=300e9,
        sector_median_return=0.40,
        rev_growth_positive=True,
        up=UP,
    )
    args.update(kw)
    return uni.evaluate_criteria(**args)


# ---------------------------------------------------------------------------
# evaluate_criteria — shape
# ---------------------------------------------------------------------------


def test_returns_all_five_criteria_separately():
    assert set(_healthy().keys()) == CRITERIA


def test_a_healthy_name_passes_every_criterion():
    assert all(v is True for v in _healthy().values())


def test_criteria_keys_match_the_universe_table_columns():
    # DESIGN §2.5 stores each crit_* column separately for ablation.
    assert set(_healthy()) == CRITERIA


# ---------------------------------------------------------------------------
# evaluate_criteria — the five criteria (ADR 014)
# ---------------------------------------------------------------------------


def test_mcap_criterion_uses_the_configured_floor():
    # Session 9 config-thresholds task, Change 2: UniverseParams.min_mcap_usd
    # moved 200e9 -> 100e9. These probe values move with it, straddling the
    # new $100B floor rather than the old $200B one.
    assert _healthy(mcap=101e9)["crit_mcap"] is True
    assert _healthy(mcap=99e9)["crit_mcap"] is False


def test_above_sma200_compares_close_to_the_200_day_sma():
    assert _healthy(ind_row=_ind(close=100.0, sma_200=90.0))["crit_above_sma200"] is True
    assert _healthy(ind_row=_ind(close=89.0, sma_200=90.0))["crit_above_sma200"] is False


def test_sma200_slope_criterion_requires_a_positive_slope():
    assert _healthy(ind_row=_ind(sma200_slope_60=0.001))["crit_sma200_slope"] is True
    assert _healthy(ind_row=_ind(sma200_slope_60=0.0))["crit_sma200_slope"] is False
    assert _healthy(ind_row=_ind(sma200_slope_60=-0.05))["crit_sma200_slope"] is False


def test_rel_return_criterion_compares_against_the_sector_median():
    assert _healthy(sector_median_return=0.40)["crit_rel_return"] is True
    assert _healthy(sector_median_return=0.80)["crit_rel_return"] is False


def test_rev_growth_criterion_passes_through_the_supplied_flag():
    assert _healthy(rev_growth_positive=True)["crit_rev_growth"] is True
    assert _healthy(rev_growth_positive=False)["crit_rev_growth"] is False


# ---------------------------------------------------------------------------
# evaluate_criteria — null is not False (DESIGN §4.6)
# ---------------------------------------------------------------------------


def test_rev_growth_is_null_when_fewer_than_four_quarters_are_available():
    assert _healthy(rev_growth_positive=None)["crit_rev_growth"] is None


def test_null_mcap_yields_a_null_criterion_not_a_false_one():
    assert _healthy(mcap=None)["crit_mcap"] is None


def test_null_indicator_yields_a_null_criterion():
    assert _healthy(ind_row=_ind(sma_200=np.nan))["crit_above_sma200"] is None


def test_null_sector_median_yields_a_null_rel_return_criterion():
    assert _healthy(sector_median_return=None)["crit_rel_return"] is None


# ---------------------------------------------------------------------------
# is_tradeable — ablation by subset (DESIGN §3.10)
# ---------------------------------------------------------------------------


def test_is_tradeable_true_when_every_criterion_passes():
    assert uni.is_tradeable(_healthy()) is True


def test_is_tradeable_false_when_any_criterion_fails():
    # Below the new $100B floor (Change 2: min_mcap_usd 200e9 -> 100e9).
    assert uni.is_tradeable(_healthy(mcap=50e9)) is False


def test_is_tradeable_treats_null_as_failing():
    assert uni.is_tradeable(_healthy(rev_growth_positive=None)) is False


def test_is_tradeable_ignores_criteria_outside_the_required_subset():
    # Ablating revenue growth: the failing criterion no longer matters.
    criteria = _healthy(rev_growth_positive=False)
    assert uni.is_tradeable(criteria) is False
    assert uni.is_tradeable(criteria, required=CRITERIA - {"crit_rev_growth"}) is True


def test_is_tradeable_with_a_single_required_criterion():
    # Below the new $100B floor (Change 2: min_mcap_usd 200e9 -> 100e9).
    criteria = _healthy(mcap=50e9)
    assert uni.is_tradeable(criteria, required={"crit_above_sma200"}) is True
    assert uni.is_tradeable(criteria, required={"crit_mcap"}) is False


def test_is_tradeable_with_an_empty_required_set_admits_everything():
    # The no-filter ablation arm: measures what the filter is worth.
    assert uni.is_tradeable(_healthy(mcap=1.0), required=set()) is True


def test_is_tradeable_rejects_an_unknown_required_criterion():
    # A typo in an ablation config must fail loudly, not silently pass.
    with pytest.raises(KeyError):
        uni.is_tradeable(_healthy(), required={"crit_typo"})
