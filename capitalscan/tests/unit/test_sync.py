"""`cscan sync`: the serving subset, and the ways a one-way copy goes wrong.

The interesting properties are structural, so they are testable without a
database: which tables ship, in what order, scoped by what, and what
happens when the destination is not configured.

The one thing a unit test cannot check is that the copy lands. That is
`tests/integration/`, which needs two live databases.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from capitalscan.core.config import Config, ServingParams
from capitalscan.jobs import sync
from capitalscan.jobs.config import config_hash

CUTOFF = date(2023, 8, 21)
TABLES = sync._tables(CUTOFF, "86e91448a65aa40b")
NAMES = [t.name for t in TABLES]


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def test_the_default_window_is_three_years():
    """393MB against Neon's 512MB free tier, measured 2026-08-20.

    1y is 139MB, 2y 266MB, 5y 638MB and full history 2,149MB. Three is the
    largest that fits with headroom.
    """
    assert ServingParams().history_years == 3


def test_the_cutoff_counts_back_from_today():
    assert sync.cutoff_date(today=date(2026, 8, 20)) == date(2023, 8, 21)


def test_a_wider_window_reaches_further_back():
    wide = sync.cutoff_date(ServingParams(history_years=5), today=date(2026, 8, 20))
    assert wide < sync.cutoff_date(today=date(2026, 8, 20))


def test_serving_params_is_not_part_of_config():
    """**The whole reason it is a separate dataclass.**

    `jobs.config.config_hash` hashes `dataclasses.asdict(Config)`. Folding
    a deployment constant in would move the hash for every config already
    written to `events`, invalidating measured rows to name a number the
    backtest never reads. Invariant 9 is still satisfied — the value lives
    in `core/config.py`.
    """
    assert not hasattr(Config(), "serving")
    assert config_hash(Config()) == "86e91448a65aa40b"


# ---------------------------------------------------------------------------
# What ships
# ---------------------------------------------------------------------------


def test_the_live_session_is_not_shipped():
    """`bars_live` stays local, and this is the interesting exclusion.

    It holds today's partial candle, rewritten every five minutes by a
    poller running on the workstation. A nightly copy would give the
    deployed site a price frozen at the last sync and label it live — the
    exact failure ADR 131 and ADR 134 fixed, and worse remotely because
    nobody there can see the poller is not running.

    So the deployed chart stops at yesterday's close and the header shows
    the close alone. Live data is local because the poller is local.
    `test_bars_live_isolation.py` caught the first version of `sync.py`
    shipping it.
    """
    assert "bars_live" not in NAMES


def test_local_only_tables_are_never_shipped():
    """`bar_rejects`, `runs` and `quotes_live` are local diagnostics.

    A table appearing in a serving view is not consent to publish it, which
    is why the list is written down rather than discovered from `pg_depend`.
    """
    for local in ("bar_rejects", "runs", "quotes_live", "shares_outstanding", "earnings"):
        assert local not in NAMES


def test_both_screener_grains_ship():
    """`v_screen` reads `next_open` and `v_screen_live` reads `touch`.

    Shipping one would leave a route silently empty rather than broken —
    the page renders, with nothing on it.
    """
    events = next(t for t in TABLES if t.name == "events")
    assert "next_open" in events.sql
    assert "touch" in events.sql


def test_only_the_live_config_ships():
    """22 other config hashes are the ADR 059 sweep and stay local."""
    events = next(t for t in TABLES if t.name == "events")
    assert ":config_hash" in events.sql


def test_bars_ship_because_the_chart_needs_them():
    """ADR 053 said "the serving layer never queries a raw bar".

    True when written and false since Session 17: `v_chart` reads `FROM
    bars`, because indicators cannot draw a candle. ADR 137 records the
    correction, and this asserts the subset follows it.
    """
    assert "bars" in NAMES
    bars = next(t for t in TABLES if t.name == "bars")
    assert "interval = '1d'" in bars.sql
    assert ":cutoff" in bars.sql


def test_the_large_tables_are_bounded_and_the_small_ones_are_not():
    """Reference data ships whole.

    Trimming `tickers` or the calendar would trade a rounding error in size
    for a class of bug where a chart resolves a date the calendar no longer
    knows.
    """
    bounded = {t.name for t in TABLES if ":cutoff" in t.sql}
    assert bounded == {"bars", "indicators", "events", "signal_reports"}
    for whole in ("tickers", "trading_days", "universe", "cell_stats", "benchmarks"):
        assert ":cutoff" not in next(t for t in TABLES if t.name == whole).sql


def test_measured_results_ship_whole():
    """The cut is in dates, never in answers.

    `cell_stats` and `benchmarks` are computed locally against full history
    and shipped entire, so a reader of the deployed site sees fewer
    sessions on a chart and never a different hit rate.
    """
    for table in ("cell_stats", "benchmarks"):
        assert next(t for t in TABLES if t.name == table).sql == f"SELECT * FROM {table}"


# ---------------------------------------------------------------------------
# Ordering and keys
# ---------------------------------------------------------------------------


def test_tickers_ships_before_everything_that_references_it():
    """Foreign-key order, not preference. `universe`, `bars`, `indicators`
    and `events` all carry a `ticker` FK."""
    for dependent in ("universe", "bars", "indicators", "events"):
        assert NAMES.index("tickers") < NAMES.index(dependent)


def test_universe_ships_before_the_tables_scoped_by_it():
    """`bars` and `indicators` are scoped by trade-universe membership, so
    a target with no `universe` rows would sync them as empty."""
    for scoped in ("bars", "indicators"):
        assert NAMES.index("universe") < NAMES.index(scoped)
        assert "universe" in next(t for t in TABLES if t.name == scoped).sql


def test_every_table_has_a_conflict_key():
    """Upsert, not insert. A sync must be re-runnable — it runs nightly."""
    for table in TABLES:
        assert table.key, f"{table.name} has no conflict key"


def test_the_events_key_is_the_natural_key():
    """`(config_hash, ticker, signal_date, signal_type, entry_kind)` is the
    unique constraint on `events`. A shorter key would silently collapse
    the two grains onto one row."""
    events = next(t for t in TABLES if t.name == "events")
    assert set(events.key) == {
        "config_hash",
        "ticker",
        "signal_date",
        "signal_type",
        "entry_kind",
    }


# ---------------------------------------------------------------------------
# The destination
# ---------------------------------------------------------------------------


def test_an_unset_serving_url_raises_rather_than_falling_back(monkeypatch):
    """**The most dangerous failure this job could have.**

    Every other resolver here falls back to the research URL with a
    warning, which is right for a read. A write that fell back would upsert
    the serving subset into the research store, on top of the rows it had
    just read from it.
    """
    monkeypatch.setattr(sync.db_io, "_load_env", lambda: None)
    monkeypatch.delenv("DATABASE_URL_SERVING", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL_SERVING"):
        sync.serving_engine()


def test_the_error_says_why_it_will_not_fall_back(monkeypatch):
    monkeypatch.setattr(sync.db_io, "_load_env", lambda: None)
    monkeypatch.delenv("DATABASE_URL_SERVING", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        sync.serving_engine()
    assert "research" in str(excinfo.value)


def test_no_sql_interpolates_a_value():
    """Bound parameters only. Nothing here formats a date into SQL text."""
    for table in TABLES:
        assert "%" not in table.sql
        assert not re.search(r"\{|\}|f\"", table.sql)


def test_describe_reports_the_plan_without_connecting():
    plan = sync.describe(today=date(2026, 8, 20))
    assert plan["cutoff"] == date(2023, 8, 21)
    assert plan["tables"] == NAMES
