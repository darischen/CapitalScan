"""`get_indicators`, `predict`, `explain_signal`, `get_universe`.

Four handlers whose test surfaces are small enough to share a file. Each
section is independent; the grouping is a file-count decision, not a
coupling.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

from capitalscan.handlers.errors import DateOutOfWindow, InvalidEnum
from capitalscan.handlers.explain import SignalNotFound, explain_signal
from capitalscan.handlers.indicators import DEFAULT_FIELDS, get_indicators
from capitalscan.handlers.predict import NO_MODEL_REASON, predict
from capitalscan.handlers.types import NotFound, Prediction, is_probability_field
from capitalscan.handlers.universe import get_universe

# ---------------------------------------------------------------------------
# get_indicators
# ---------------------------------------------------------------------------

BAR = {
    "ts": date(2026, 8, 14),
    "close": 231.45,
    "bb_lower": 220.1,
    "bb_mid": 228.0,
    "bb_upper": 235.9,
    "k_fast": 14.2,
    "k_full": 17.8,
    "volume": 18_400_000,
}


@pytest.fixture
def series(fake_db):
    fake_db.on("FROM indicators i", [BAR])
    return fake_db


def test_the_default_fields_carry_both_k_series(series):
    """ADR 110 made the agreement between them part of the signal rule.

    The raw `k_fast` is the trigger and the smoothed `k_full` must agree
    within `fast_agreement_tol`. A stochastic panel drawing one of them is
    drawing half the rule, so both are in the chart default.
    """
    assert "k_fast" in DEFAULT_FIELDS and "k_full" in DEFAULT_FIELDS
    result = get_indicators("TSM", engine=object())
    assert result.fields == DEFAULT_FIELDS
    assert result.points[0].values["k_fast"] == 14.2
    assert result.points[0].values["k_full"] == 17.8


def test_an_unknown_field_raises_rather_than_reaching_the_query(series):
    """The only handler where a caller's string lands in the SQL text.

    An unchecked value would be an injection point, and a Postgres "column
    does not exist" is a worse error than one naming the valid fields.
    """
    with pytest.raises(InvalidEnum, match="not an indicator or bar column"):
        get_indicators("TSM", fields=["close; DROP TABLE bars"], engine=object())
    assert not series.sql_containing("FROM indicators i")


def test_an_empty_field_list_is_refused(series):
    with pytest.raises(InvalidEnum, match="selects no series"):
        get_indicators("TSM", fields=[], engine=object())


def test_repeated_fields_are_deduplicated_in_order(series):
    result = get_indicators("TSM", fields=["close", "k_full", "close"], engine=object())
    assert result.fields == ("close", "k_full")


def test_bar_columns_and_indicator_columns_are_qualified_separately(series):
    get_indicators("TSM", fields=["close", "bb_upper"], engine=object())
    sql, _ = next(c for c in series.calls if "FROM indicators i" in c[0])
    assert "b.close" in sql and "i.bb_upper" in sql


def test_a_boolean_indicator_becomes_a_number(series):
    """`IndicatorPoint.values` is numeric, and a chart plots a crossover as
    a marker at a level rather than as a second differently-keyed map."""
    series.on("FROM indicators i", [{"ts": date(2026, 8, 14), "k_cross_up": True}])
    point = get_indicators("TSM", fields=["k_cross_up"], engine=object()).points[0]
    assert point.values["k_cross_up"] == 1.0


def test_a_ticker_with_no_rows_returns_an_empty_series_not_an_error(fake_db):
    """A delisted name is the common case, and a raise turns it into an
    outage on the ticker page."""
    fake_db.on("FROM indicators i", [])
    result = get_indicators("DEAD", engine=object())
    assert result.points == ()
    assert result.meta.config_hash == "testhash"


def test_a_date_outside_the_window_raises(series):
    with pytest.raises(DateOutOfWindow):
        get_indicators("TSM", start=date(2009, 1, 1), engine=object())


def test_indicator_limit_caps_at_two_hundred(series):
    get_indicators("TSM", limit=10_000, engine=object())
    sql, _ = next(c for c in series.calls if "FROM indicators i" in c[0])
    assert "LIMIT 200" in sql


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ticker": "TSM"},
        {"ticker": "AVGO", "as_of": date(2026, 8, 14)},
        {"ticker": "nvda"},
    ],
)
def test_predict_returns_not_found_for_every_input(fake_db, kwargs):
    """**This test is meant to fail when Phase 6 changes it.**

    No model exists. ADR 093 is Provisional and ADR 113 opened Phase 6
    conditionally on ADR 112's negative result. When a model ships, this
    assertion breaks, and breaking it should be a deliberate edit that says
    why - not a stub quietly starting to return a plausible fan.
    """
    result = predict(engine=object(), **kwargs)
    assert isinstance(result, NotFound)
    assert result.reason == NO_MODEL_REASON


def test_the_not_found_still_carries_meta(fake_db):
    """Which config was queried and how stale it is are true and knowable
    regardless of whether a prediction exists."""
    result = predict("TSM", engine=object())
    assert result.meta.config_hash == "testhash"
    assert result.meta.as_of == date(2026, 8, 17)


def test_the_reason_points_somewhere_useful(fake_db):
    assert "get_stats" in NO_MODEL_REASON


def test_the_prediction_type_carries_the_invariant_eight_companions():
    """Phase 6 inherits a decided contract, not a negotiation.

    A model that cannot say how much data stands behind its fan cannot ship
    through this layer, because the type will not hold it.
    """
    names = {f.name for f in fields(Prediction)}
    assert {"n_eff", "ci_low", "ci_high", "q_value"} <= names
    assert [f.name for f in fields(Prediction) if is_probability_field(f.name)]


# ---------------------------------------------------------------------------
# explain_signal
# ---------------------------------------------------------------------------

EVENT = {
    "id": 4211,
    "ticker": "TSM",
    "signal_date": date(2026, 8, 14),
    "signal_type": "confluence_low",
    "signal_types_all": ["confluence_low", "bb_lower_touch", "stoch_oversold"],
    "signal_strength": 3,
    "side": "long",
    "entry_kind": "next_open",
    "touch_level": 220.1,
    "bb_pctb": 0.01,
    "k_fast": 9.0,
    "k_full": 12.0,
    "dd_bucket": "10-20",
    "cofire_count": 6,
    "vix_close": 18.4,
    "sector": "Technology",
    "era": "2024+",
    "split_key": "holdout",
}


@pytest.fixture
def event(fake_db):
    fake_db.on("FROM events", [EVENT])
    return fake_db


def test_the_features_are_the_stored_columns_not_a_recomputation(event):
    """Recomputing here would be a second implementation of the detector's
    inputs, and would drift the first time an indicator window moved."""
    result = explain_signal("TSM", date(2026, 8, 14), engine=object())
    assert result.features["bb_pctb"] == 0.01
    assert result.features["k_fast"] == 9.0
    assert result.features["cofire_count"] == 6


def test_there_is_no_shap_field(event):
    """DESIGN §10.1 lists one and it is a Phase 6 field.

    Absent rather than empty: an empty list reads as "nothing contributed",
    which is a claim, and a missing field reads as "no model", which is the
    fact.
    """
    from capitalscan.handlers.types import Explanation

    names = {f.name for f in fields(Explanation)}
    assert not {n for n in names if "shap" in n or "attribution" in n}


def test_no_cell_is_attached_without_being_asked_for(event):
    result = explain_signal("TSM", date(2026, 8, 14), engine=object())
    assert result.cell is None and result.cell_id is None
    assert not event.sql_containing("FROM cell_stats")


def test_half_a_cell_request_is_refused(event):
    """`split` and `target_pct` select a cell together.

    There is no sensible default for the other half, and inventing one
    would put a statistical choice in a default argument (invariant 9).
    """
    with pytest.raises(InvalidEnum, match="half-stated"):
        explain_signal("TSM", date(2026, 8, 14), split="train", engine=object())
    with pytest.raises(InvalidEnum, match="half-stated"):
        explain_signal("TSM", date(2026, 8, 14), target_pct=0.03, engine=object())


def test_nothing_fired_raises_rather_than_returning_an_empty_explanation(fake_db):
    """ "Explain what fired" when nothing fired has no answer.

    An `Explanation` with empty features would render as a page describing
    a signal that did not happen.
    """
    fake_db.on("FROM events", [])
    with pytest.raises(SignalNotFound, match="Nothing fired"):
        explain_signal("TSM", date(2026, 8, 14), engine=object())


def test_the_most_specific_signal_is_the_one_reported(event):
    """ADR 057's ranking, and the same one that selects the cell.

    `signal_types_all` carries the rest, so nothing is hidden; the choice
    is only about which single type names the row.
    """
    event.on(
        "FROM events",
        [
            dict(EVENT, signal_type="bb_lower_touch", signal_strength=1),
            dict(EVENT, signal_type="confluence_low", signal_strength=3),
        ],
    )
    result = explain_signal("TSM", date(2026, 8, 14), engine=object())
    assert result.signal_type == "confluence_low"


# ---------------------------------------------------------------------------
# get_universe
# ---------------------------------------------------------------------------

MEMBER = {
    "ticker": "TSM",
    "name": "Taiwan Semiconductor",
    "sector": "Technology",
    "industry": "Semiconductors",
    "as_of": date(2026, 6, 30),
    "in_train": True,
    "in_trade": True,
    "mcap_usd": 9.1e11,
    "mcap_rank": 8,
    "adv_20d_usd": 2.2e9,
    "crit_mcap": True,
    "crit_above_sma200": True,
    "crit_sma200_slope": True,
    "crit_rel_return": True,
    "crit_rev_growth": True,
    "is_active": True,
    "delisted_on": None,
}
TRAIN_ONLY = dict(MEMBER, ticker="INTC", in_trade=False, crit_above_sma200=False, mcap_rank=41)


@pytest.fixture
def members(fake_db):
    fake_db.on("v_universe", [MEMBER, TRAIN_ONLY])
    return fake_db


def test_the_criteria_travel_with_the_row(members):
    """ADR 003 makes membership a conjunction of checkable criteria.

    A row saying `in_trade = false` with no reason is a row nobody can
    audit.
    """
    row = get_universe(engine=object()).rows[1]
    assert row.in_trade is False
    assert row.criteria["crit_above_sma200"] is False
    assert row.criteria["crit_mcap"] is True


def test_the_counts_describe_what_was_returned(members):
    result = get_universe(engine=object())
    assert (result.n_train, result.n_trade) == (2, 1)


def test_filtering_moves_the_counts_with_it(members):
    """An `n_trade` that stayed at the unfiltered total while `rows` shrank
    would be a number a reader divides by."""
    result = get_universe(universe="trade", engine=object())
    assert len(result.rows) == 1
    assert result.n_trade == 1


def test_a_historical_as_of_uses_the_row_in_force_not_the_current_one(fake_db):
    """Answering a historical question from the current view is how a
    survivorship bias gets in (ADR 002)."""
    fake_db.on("FROM universe u", [MEMBER])
    get_universe(as_of=date(2018, 6, 30), engine=object())
    sql, params = next(c for c in fake_db.calls if "FROM universe u" in c[0])
    assert "DISTINCT ON (u.ticker)" in sql
    assert "u.as_of <= :as_of" in sql
    assert params["as_of"] == date(2018, 6, 30)


def test_the_universe_is_not_truncated_by_a_limit(members):
    """ADR 104 makes it the denominator of every breadth statistic.

    A truncated denominator silently changes what a percentage means, and
    the universe is bounded by construction anyway.
    """
    import inspect

    assert "limit" not in inspect.signature(get_universe).parameters


def test_an_empty_universe_returns_an_empty_result(fake_db):
    fake_db.on("v_universe", [])
    result = get_universe(engine=object())
    assert result.rows == () and result.n_train == 0
    assert result.meta.config_hash == "testhash"
