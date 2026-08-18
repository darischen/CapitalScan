"""`screen_signals`: the event feed by default, statistics on request (ADR 114).

The default matters more than it looks. ADR 112 measured zero cells
surviving FDR correction and 100 of 224 train cells suppressed, so a
screener that put the statistical fields in every row would show four blank
or near-blank columns on nearly every row, every day. Four always-empty
columns teach a reader to skip the row, and the row is the part that carries
information.
"""

from __future__ import annotations

from datetime import date

import pytest

from capitalscan.handlers.errors import DateOutOfWindow, InvalidEnum
from capitalscan.handlers.screen import screen_signals
from capitalscan.handlers.types import CellStats, Suppressed
from capitalscan.tests.unit.test_handlers_stats import LIVE_CELL, SUPPRESSED_CELL

CELL_ID = "confluence_low|long|0-10|all|next_open|validate|pooled|h5|t0.03"

FEED = [
    {
        "ticker": "TSM",
        "signal_date": date(2026, 8, 14),
        "signal_type": "confluence_low",
        "signal_types_all": ["confluence_low", "bb_lower_touch", "stoch_oversold"],
        "signal_strength": 3,
        "bb_pctb": 0.02,
        "k_full": 18.0,
        "k_fast": 15.0,
        "k_cross_up": True,
        "dd_52w": -0.14,
        "dd_bucket": "10-20",
        "above_sma200": False,
        "cofire_count": 6,
        "sector": "Technology",
        "cell_id": CELL_ID,
    },
    {
        "ticker": "AVGO",
        "signal_date": date(2026, 8, 14),
        "signal_type": "bb_upper_touch",
        "signal_types_all": ["bb_upper_touch"],
        "signal_strength": 1,
        "bb_pctb": 0.99,
        "k_full": 88.0,
        "k_fast": 91.0,
        "k_cross_up": False,
        "dd_52w": -0.02,
        "dd_bucket": "0-10",
        "above_sma200": True,
        "cofire_count": 2,
        "sector": "Technology",
        "cell_id": None,
    },
]


@pytest.fixture
def feed(fake_db):
    fake_db.on("FROM v_screen", FEED).on("count(*)", [{"n": len(FEED)}])
    return fake_db


def _call(**over):
    kwargs = dict(date_=date(2026, 8, 14), engine=object())
    kwargs.update(over)
    return screen_signals(**kwargs)


# ---------------------------------------------------------------------------
# The default
# ---------------------------------------------------------------------------


def test_the_default_is_the_event_feed_with_no_statistics(feed):
    result = _call()
    assert result.with_stats is False
    assert all(row.stats is None for row in result.rows)


def test_the_default_does_not_even_query_cell_stats(feed):
    """Not merely blank - not fetched.

    A default that queried and then hid the result would still pay for the
    join on every page load, and would be one edit away from rendering it.
    """
    _call()
    assert not feed.sql_containing("FROM cell_stats")


def test_the_feed_carries_what_the_reader_actually_reads(feed):
    row = _call().rows[0]
    assert row.ticker == "TSM"
    assert row.signal_types_all == ("confluence_low", "bb_lower_touch", "stoch_oversold")
    assert row.signal_strength == 3
    assert row.cofire_count == 6
    assert row.dd_bucket == "10-20"


def test_the_side_is_derived_from_the_signal_type(feed):
    """`v_screen` has no `side` column, and `detect` emits one hit per side.

    So the type determines the side exactly, and reading it off the type is
    not a shortcut but the same fact stated once.
    """
    rows = _call().rows
    assert rows[0].side == "long"
    assert rows[1].side == "short"


# ---------------------------------------------------------------------------
# Statistics, on request and whole
# ---------------------------------------------------------------------------


def test_with_stats_attaches_a_cell_stats_object(feed):
    feed.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=CELL_ID, split_key="validate")])
    row = _call(with_stats=True).rows[0]
    assert isinstance(row.stats, CellStats)
    assert row.stats.p_hit == 0.51
    assert row.stats.n_eff == 93
    assert row.stats.q_value == 0.8492
    assert row.stats.survives_fdr is False


def test_with_stats_attaches_suppressed_for_a_suppressed_cell(feed):
    """A reason, never a blank number.

    `v_screen` nulls `p_hit` on a suppressed cell, which cannot express the
    difference between "suppressed" and "not measured". The union can, so
    the handler reads `cell_stats` rather than the view's nulled columns.
    """
    feed.on("FROM cell_stats", [dict(SUPPRESSED_CELL, cell_id=CELL_ID, split_key="validate")])
    row = _call(with_stats=True).rows[0]
    assert isinstance(row.stats, Suppressed)
    assert "below min_n_eff" in row.stats.reason


def test_a_row_whose_cell_was_never_computed_gets_no_statistics(feed):
    feed.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=CELL_ID, split_key="validate")])
    rows = _call(with_stats=True).rows
    assert rows[1].cell_id is None
    assert rows[1].stats is None


def test_the_cells_are_fetched_in_one_query_not_one_per_row(feed):
    """Fifty rows in one bucket share a cell. A per-row lookup is fifty
    round trips for one answer."""
    feed.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=CELL_ID, split_key="validate")])
    _call(with_stats=True)
    assert len(feed.sql_containing("FROM cell_stats")) == 1


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_the_type_filter_reads_signal_types_all_not_signal_type(feed):
    """ADR 057's ranking means `signal_type` carries only the most specific.

    A filter on it drops every `confluence_high` bar that also closed above
    the band - the exact class of row a caller asking for `confluence_high`
    wants to see.
    """
    _call(signal_types=["confluence_high"])
    sql, params = next(c for c in feed.calls if "ORDER BY s.signal_date" in c[0])
    assert "signal_types_all &&" in sql
    assert params["signal_types"] == ["confluence_high"]


def test_the_universe_selects_the_membership_flag(feed):
    _call(universe="train")
    sql, _ = next(c for c in feed.calls if "FROM v_screen" in c[0])
    assert "u.in_train" in sql and "u.in_trade" not in sql


def test_an_unknown_universe_raises_before_querying(feed):
    with pytest.raises(InvalidEnum):
        _call(universe="everything")
    assert not feed.sql_containing("FROM v_screen")


def test_a_date_outside_the_window_raises_and_names_it(feed):
    with pytest.raises(DateOutOfWindow, match="2010-01-04..2026-08-17"):
        _call(date_=date(2026, 9, 1))


def test_limit_caps_at_two_hundred(feed):
    result = _call(limit=10_000)
    assert result.limit == 200
    sql, _ = next(c for c in feed.calls if "ORDER BY s.signal_date" in c[0])
    assert "LIMIT 200" in sql


# ---------------------------------------------------------------------------
# The empty state
# ---------------------------------------------------------------------------


def test_a_quiet_day_returns_an_empty_result_with_populated_meta(fake_db):
    """DESIGN §11.2: most days nothing fires, so this is the common path.

    An empty result still has to say which config it queried and how stale
    the database is, or the reader cannot tell "nothing fired" from
    "nothing was ingested".
    """
    fake_db.on("FROM v_screen", []).on("count(*)", [{"n": 0}])
    result = _call()
    assert result.rows == ()
    assert result.total_matched == 0
    assert result.meta.config_hash == "testhash"
    assert result.meta.as_of == date(2026, 8, 17)


def test_total_matched_reports_the_pre_limit_count(feed):
    """A page showing 200 of 640 and saying only "200" lies about the day."""
    feed.on("count(*)", [{"n": 640}])
    result = _call(limit=1)
    assert result.total_matched == 640
    assert result.limit == 1
    # The truncation itself is Postgres'; the fixture returns whatever it
    # was handed. What is asserted here is that the count query and the row
    # query are separate, so the count cannot inherit the LIMIT.
    row_sql = next(c[0] for c in feed.calls if "ORDER BY s.signal_date" in c[0])
    count_sql = next(c[0] for c in feed.calls if "count(*)" in c[0])
    assert "LIMIT" in row_sql and "LIMIT" not in count_sql


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def test_staleness_is_measured_in_trading_days(feed):
    """A Monday query against Friday's close is zero sessions stale.

    Counting calendar days would raise the banner every Monday and over
    every holiday, and a banner that is always on is a banner that is off.
    """
    feed.on("FROM trading_days", [{"n": 0}])
    assert _call().meta.staleness_days == 0
    assert _call().meta.stale is False


def test_the_stale_flag_trips_above_the_configured_threshold(feed):
    from capitalscan.core.config import MonitoringThresholds

    feed.on("FROM trading_days", [{"n": MonitoringThresholds().stale_after_days + 1}])
    assert _call().meta.stale is True


def test_two_identical_calls_return_equal_results(feed):
    assert _call() == _call()
