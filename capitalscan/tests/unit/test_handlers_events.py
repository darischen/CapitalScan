"""`get_events`: cluster heads by default, and holdout out by construction.

Two properties carry this handler.

**Clustering.** ADR 054 counts a run of consecutive touches once.
`cluster_head_only=True` is the default because a caller who gets all
members sees five consecutive `bb_lower_touch` days as five independent
observations, which is the double counting the clustering exists to remove.

**The split predicate.** `v_events` exposes every split including holdout,
because it is also the batch layer's read path. So the *handler* bounds it,
and the bound is written as membership in `enums.SPLITS` rather than as
`<> 'holdout'` - an inequality admits whatever a later migration adds to the
check constraint, and membership admits only what this layer decided.
"""

from __future__ import annotations

from datetime import date

import pytest

from capitalscan.handlers import enums
from capitalscan.handlers.errors import HoldoutRequested
from capitalscan.handlers.events import get_events, last_fire

ROW = {
    "id": 4211,
    "ticker": "TSM",
    "signal_date": date(2021, 3, 9),
    "signal_type": "confluence_low",
    "signal_types_all": ["confluence_low", "bb_lower_touch", "stoch_oversold"],
    "signal_strength": 3,
    "cluster_id": 99,
    "seq_in_cluster": 0,
    "is_cluster_head": True,
    "bb_pctb": 0.01,
    "k_full": 12.0,
    "k_fast": 9.0,
    "dd_52w": -0.18,
    "dd_bucket": "10-20",
    "above_sma200": False,
    "entry_kind": "next_open",
    "entry_date": date(2021, 3, 10),
    "entry_price": 118.4,
    "exit_date": date(2021, 3, 17),
    "exit_price": 122.9,
    "exit_reason": "target",
    "holding_days": 5,
    "gross_ret": 0.038,
    "net_ret": 0.0374,
    "mfe": 0.041,
    "mae": -0.012,
    "era": "2019-2023",
    "split_key": "train",
}


@pytest.fixture
def events(fake_db):
    fake_db.on("FROM v_events", [ROW]).on("count(*)", [{"n": 1}])
    return fake_db


def _call(**over):
    kwargs = dict(ticker="TSM", engine=object())
    kwargs.update(over)
    return get_events(**kwargs)


def _row_sql(db):
    return next(c for c in db.calls if "ORDER BY signal_date" in c[0])


# ---------------------------------------------------------------------------
# Holdout
# ---------------------------------------------------------------------------


def test_no_split_still_excludes_holdout(events):
    """The default is train plus validate, never "everything"."""
    _call()
    _, params = _row_sql(events)
    assert params["splits"] == list(enums.SPLITS)
    assert "holdout" not in params["splits"]


def test_the_predicate_is_membership_not_an_inequality(events):
    _call()
    sql, _ = _row_sql(events)
    assert "split_key = ANY(:splits)" in sql
    assert "<>" not in sql and "!=" not in sql


def test_asking_for_holdout_raises_before_any_query(events):
    with pytest.raises(HoldoutRequested):
        _call(split="holdout")
    assert not events.sql_containing("FROM v_events")


def test_a_named_split_also_bounds_the_dates(events):
    """Label and dates together, as `test_split_leakage.py` pairs them.

    One mislabelled row then produces an empty result rather than crossing
    a boundary on its own.
    """
    _call(split="validate")
    sql, params = _row_sql(events)
    assert "signal_date BETWEEN" in sql
    assert params["split_low"] == date(2022, 1, 1)
    assert params["split_high"] == date(2023, 12, 31)


# ---------------------------------------------------------------------------
# Clustering and grain
# ---------------------------------------------------------------------------


def test_cluster_heads_only_by_default(events):
    _call()
    sql, _ = _row_sql(events)
    # `AND is_cluster_head`, not the bare name: the column is also in the
    # SELECT list, so an unqualified substring check passes either way and
    # would have proved nothing about the predicate.
    assert "AND is_cluster_head" in sql


def test_the_toggle_actually_widens_it(events):
    _call(cluster_head_only=False)
    sql, _ = _row_sql(events)
    assert "AND is_cluster_head" not in sql


def test_one_entry_kind_is_pinned(events):
    """The `events` grain includes `entry_kind`, so omitting it returns one
    signal four times - once per kind the backtest wrote."""
    _call()
    sql, params = _row_sql(events)
    assert "entry_kind = :entry_kind" in sql
    assert params["entry_kind"] == "next_open"


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_a_row_maps_to_a_typed_event(events):
    row = _call().rows[0]
    assert row.id == 4211
    assert row.signal_types_all == ("confluence_low", "bb_lower_touch", "stoch_oversold")
    assert row.side == "long"
    assert row.exit_reason == "target"
    assert row.net_ret == 0.0374


def test_the_ticker_is_normalised(events):
    _call(ticker="tsm")
    _, params = _row_sql(events)
    assert params["ticker"] == "TSM"


def test_an_event_row_carries_no_probability_field():
    """Events are observations, not estimates.

    A `p_hit` on an event row would be a cell statistic wearing a row's
    clothes, and one row is not a sample.
    """
    from dataclasses import fields

    from capitalscan.handlers.types import EventRow, is_probability_field

    assert not [f.name for f in fields(EventRow) if is_probability_field(f.name)]


# ---------------------------------------------------------------------------
# Empty and limits
# ---------------------------------------------------------------------------


def test_a_ticker_with_no_events_returns_an_empty_list_not_an_error(fake_db):
    fake_db.on("FROM v_events", []).on("count(*)", [{"n": 0}])
    result = _call(ticker="NONE")
    assert result.rows == ()
    assert result.meta.config_hash == "testhash"


def test_limit_caps_at_two_hundred(events):
    assert _call(limit=10_000).limit == 200


def test_last_fire_returns_one_row_for_the_empty_state(events):
    """DESIGN §11.2's `No signals today. Last fire: TSM, 3 days ago`.

    It lives in the handler rather than in a route so the query decision
    does not end up in a template.
    """
    row = last_fire(engine=object())
    assert row is not None and row.ticker == "TSM"


def test_last_fire_is_none_when_nothing_has_ever_fired(fake_db):
    fake_db.on("FROM v_events", []).on("count(*)", [{"n": 0}])
    assert last_fire(engine=object()) is None


def test_two_identical_calls_return_equal_results(events):
    assert _call() == _call()
