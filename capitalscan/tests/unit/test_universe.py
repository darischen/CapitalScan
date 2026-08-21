"""Unit tests for core/universe.py.

The five ADR 014 criteria come back **separately**, which is what makes the
ablation study a config change rather than a code change (DESIGN §3.10).
The sharp case is revenue growth: fewer than four reported quarters means
`None`, not `False`. `is_tradeable` treats both as failing, but the audit
log has to be able to tell them apart (DESIGN §4.6).
"""

from __future__ import annotations

from datetime import date, timedelta

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
    """**States its own floor rather than reading the default.**

    This test twice had to be rewritten because its probe values were
    pinned to whatever `min_mcap_usd` happened to be -- 100e9 in Session 9,
    30e9 in Session 10, 20e9 on 2026-08-21 -- so a config change turned a
    rule test into a failing assertion about a number nobody had asked it
    about. Straddling an explicit floor makes it test the comparison, which
    is its actual subject, and it now passes under any default.
    """
    up = UniverseParams(min_mcap_usd=30e9)
    assert _healthy(mcap=31e9, up=up)["crit_mcap"] is True
    assert _healthy(mcap=29e9, up=up)["crit_mcap"] is False
    # And again at a different floor, so the test cannot pass by coincidence.
    low = UniverseParams(min_mcap_usd=20e9)
    assert _healthy(mcap=21e9, up=low)["crit_mcap"] is True
    assert _healthy(mcap=19e9, up=low)["crit_mcap"] is False


def test_the_default_floor_is_twenty_billion():
    """The default itself, asserted once and in one place, so moving it is a
    one-line change rather than a four-test change."""
    assert UniverseParams().min_mcap_usd == 20e9


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
    # The floor is stated, not inherited: `mcap=20e9` read as "failing" only
    # while the default was 30e9, and became exactly the floor when the
    # default moved there on 2026-08-21.
    up = UniverseParams(min_mcap_usd=30e9)
    assert uni.is_tradeable(_healthy(mcap=20e9, up=up)) is False


def test_is_tradeable_treats_null_as_failing():
    assert uni.is_tradeable(_healthy(rev_growth_positive=None)) is False


def test_is_tradeable_ignores_criteria_outside_the_required_subset():
    # Ablating revenue growth: the failing criterion no longer matters.
    criteria = _healthy(rev_growth_positive=False)
    assert uni.is_tradeable(criteria) is False
    assert uni.is_tradeable(criteria, required=CRITERIA - {"crit_rev_growth"}) is True


def test_is_tradeable_with_a_single_required_criterion():
    # Same reason as above: the failing criterion has to be made to fail
    # explicitly rather than by relying on where the default sits.
    criteria = _healthy(mcap=20e9, up=UniverseParams(min_mcap_usd=30e9))
    assert uni.is_tradeable(criteria, required={"crit_above_sma200"}) is True
    assert uni.is_tradeable(criteria, required={"crit_mcap"}) is False


def test_is_tradeable_with_an_empty_required_set_admits_everything():
    # The no-filter ablation arm: measures what the filter is worth.
    assert uni.is_tradeable(_healthy(mcap=1.0), required=set()) is True


def test_is_tradeable_rejects_an_unknown_required_criterion():
    # A typo in an ablation config must fail loudly, not silently pass.
    with pytest.raises(KeyError):
        uni.is_tradeable(_healthy(), required={"crit_typo"})


# ---------------------------------------------------------------------------
# `in_trade` fails closed (ADR 129)
# ---------------------------------------------------------------------------


def _flags(rows: list[tuple[str, date, bool]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "as_of", "in_trade"])


def test_no_evaluation_means_not_tradeable():
    """The whole of ADR 129, in one assertion.

    This returned True until 2026-08-19 — a v1 simplification so
    `run_events` worked before `run_universe` had ever run for a name. It
    admitted 18,805 training events on 566 tickers to the trade population
    without ever evaluating them for it, 11.9% of the split.

    Membership is a claim that a name passed four criteria. Absent evidence
    is not that claim.
    """
    assert uni.in_trade(_flags([]), "TSLA", date(2015, 6, 30)) is False


def test_an_evaluation_after_the_signal_does_not_count():
    """The check is `as_of <= signal_date`, so a later evaluation is not
    evidence about an earlier bar.

    **This is why the population was 18,805 and not a few hundred.** The
    first measurement assumed the gap was the pre-2010 window; it is per
    *ticker*, so a name that entered the universe in 2021 failed open
    across every one of its earlier signals.
    """
    flags = _flags([("TSLA", date(2021, 3, 31), True)])
    assert uni.in_trade(flags, "TSLA", date(2015, 6, 30)) is False
    assert uni.in_trade(flags, "TSLA", date(2021, 6, 30)) is True


def test_another_tickers_evaluation_is_not_evidence():
    flags = _flags([("AAPL", date(2015, 3, 31), True)])
    assert uni.in_trade(flags, "TSLA", date(2015, 6, 30)) is False


def test_the_most_recent_evaluation_on_or_before_the_signal_decides():
    """Not the newest row overall — the newest one that had happened yet.

    TSLA is the live example: in the trade universe at 2026-06-30 and out
    of it for all of 2024 and 2025 on `crit_rel_return`. Reading the wrong
    row would backdate a membership it did not have.
    """
    flags = _flags(
        [
            ("TSLA", date(2024, 12, 31), False),
            ("TSLA", date(2025, 12, 31), True),
            ("TSLA", date(2026, 6, 30), True),
        ]
    )
    assert uni.in_trade(flags, "TSLA", date(2025, 6, 30)) is False
    assert uni.in_trade(flags, "TSLA", date(2026, 1, 15)) is True


def test_an_evaluation_exactly_on_the_signal_date_counts():
    """`<=`, not `<`. A quarter-end evaluation governs that day's signals."""
    flags = _flags([("TSLA", date(2026, 6, 30), True)])
    assert uni.in_trade(flags, "TSLA", date(2026, 6, 30)) is True


# ---------------------------------------------------------------------------
# `evaluation_max_age_days` — the recency floor (backlog item 3, 2026-08-20)
# ---------------------------------------------------------------------------


def test_a_quarter_is_one_rebalance_period():
    assert uni.evaluation_max_age_days("Q") == 92


def test_the_frequency_is_read_case_insensitively_and_trimmed():
    """`rebalance_freq` is a hand-written config string, not an enum."""
    assert uni.evaluation_max_age_days(" q ") == 92
    assert uni.evaluation_max_age_days("M") == uni.evaluation_max_age_days("m")


def test_an_unknown_frequency_raises_rather_than_defaulting():
    """A silent fallback would restore the unbounded behaviour this closes.

    That is the whole defect: `_latest_indicator_row` filtered `ts <= as_of`
    with no lower bound, so AET passed every criterion at 2026-06-30 on
    data frozen in November 2018.
    """
    with pytest.raises(ValueError, match="no known period"):
        uni.evaluation_max_age_days("fortnightly")


def test_every_frequency_is_a_positive_number_of_days():
    for freq in ("D", "W", "M", "Q", "A", "Y"):
        assert uni.evaluation_max_age_days(freq) > 0


def test_the_default_config_frequency_resolves():
    """`UniverseParams.rebalance_freq` had no consumer until 2026-08-20, so
    nothing checked that its default was a value anything understood."""
    assert uni.evaluation_max_age_days(UniverseParams().rebalance_freq) == 92


def test_the_floor_admits_aets_final_quarter_and_rejects_the_next():
    """The boundary the fix turns on, stated as dates.

    AET's last bar is 2018-11-29. At `as_of` 2018-12-31 it traded inside
    the quarter and stays; at 2019-03-31 it did not and drops. One final
    quarter of membership is correct — `core/arms.py` sells at the next
    rebalance, not immediately.
    """
    max_age = uni.evaluation_max_age_days("Q")
    last_bar = date(2018, 11, 29)

    assert last_bar > date(2018, 12, 31) - timedelta(days=max_age)
    assert last_bar <= date(2019, 3, 31) - timedelta(days=max_age)
