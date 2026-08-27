"""The `poll` job (DESIGN §4.8) — the Phase 2 gate's core claims:

    - a live breach is recorded within one interval
    - `poller_sessions` records coverage
    - a restart mid-session does not re-fire an already-sent event

Requires a reachable `DATABASE_URL_RESEARCH`, same convention as
`tests/integration/test_compute.py`. Network fetchers (`fetch_quotes`,
the call overlay) are mocked; the Postgres writes are real.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.config import Config, SignalParams
from capitalscan.jobs import call_overlay, db_io, ingest, poll
from capitalscan.jobs.config import config_hash
from capitalscan.jobs.fetch import yahoo

TICKER = "TSM"
SIGNAL_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 10, 0, 0)

_TRUNCATE_TABLES = [
    "signal_reports",
    "order_intents",
    "quotes_live",
    "poller_sessions",
    "scheduled_runs",
    "events",
    "indicators",
    "market_days",
    "universe",
    "runs",
    "tickers",
]


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture()
def engine():
    eng = db_io.get_engine()
    with eng.begin() as conn:
        for table in _TRUNCATE_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    yield eng
    with eng.begin() as conn:
        for table in _TRUNCATE_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))


@pytest.fixture()
def seeded(engine, monkeypatch):
    """One in_trade ticker with bands set so a live price of 94.0 breaches
    the lower band while also oversold on %K — a confluence-low fire,
    mirroring the fixture shape `tests/integration/test_compute.py` uses
    for the same signal in the backtest path.
    """
    ingest.ensure_tickers([TICKER], engine=engine)
    # `config_hash` joined the primary key in `d4a17c93f60b`, and
    # `poll.py` selects membership `WHERE config_hash = :chash`. Both the
    # conflict key and the value have to carry it or this row is written
    # under a generation the poller never looks at.
    db_io.upsert(
        engine,
        "universe",
        [
            {
                "ticker": TICKER,
                "as_of": SIGNAL_DATE,
                "config_hash": config_hash(Config(signals=SignalParams())),
                "in_train": True,
                "in_trade": True,
            }
        ],
        ["ticker", "as_of", "config_hash"],
    )
    db_io.upsert(
        engine,
        "indicators",
        [
            {
                "ticker": TICKER,
                "ts": datetime(2026, 7, 30),
                "interval": "1d",
                "bb_lower": 95.0,
                "bb_mid": 100.0,
                "bb_upper": 105.0,
                "k_full": 15.0,
                "d_full": 15.0,
                "k_fast": 15.0,
                "atr_14": 2.0,
                "sma_200": 90.0,
                "dd_52w": 0.05,
            }
        ],
        ["ticker", "ts", "interval"],
    )

    def fake_quotes(tickers):
        return pd.DataFrame(
            {"ticker": tickers, "ts": [NOW] * len(tickers), "price": [94.0] * len(tickers)}
        )

    monkeypatch.setattr(yahoo, "fetch_quotes", fake_quotes)
    monkeypatch.setattr(call_overlay, "build_overlay", lambda *a, **k: None)
    return engine


def test_breach_is_recorded_as_a_holdout_event_within_one_tick(seeded):
    engine = seeded
    report = poll.run_poll(
        interval=1,
        tickers=[TICKER],
        engine=engine,
        notifiers=[],
        now_fn=lambda: NOW,
        sleep_fn=lambda s: None,
        max_ticks=1,
    )
    assert report.rows_written == 1

    with engine.connect() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM events WHERE ticker = :t AND signal_date = :d"),
                {"t": TICKER, "d": SIGNAL_DATE},
            )
            .mappings()
            .one()
        )
    assert row["side"] == "long"
    assert row["signal_type"] == "confluence_low"
    assert row["split_key"] == "holdout"  # invariant 5b: never train/validate for a live event
    assert row["entry_price"] == pytest.approx(94.0)


def test_quotes_live_and_poller_sessions_are_written(seeded):
    engine = seeded
    poll.run_poll(
        interval=1,
        tickers=[TICKER],
        engine=engine,
        notifiers=[],
        now_fn=lambda: NOW,
        sleep_fn=lambda s: None,
        max_ticks=1,
    )
    with engine.connect() as conn:
        quote = (
            conn.execute(text("SELECT * FROM quotes_live WHERE ticker = :t"), {"t": TICKER})
            .mappings()
            .one()
        )
        session = (
            conn.execute(
                text("SELECT * FROM poller_sessions WHERE session_date = :d"), {"d": SIGNAL_DATE}
            )
            .mappings()
            .one()
        )
    assert quote["breached"] == "lower"
    assert session["ticks_completed"] == 1


def test_restart_mid_session_does_not_refire_an_already_sent_event(seeded):
    """The Phase 2 gate's sharpest claim: debounce survives a fresh
    `run_poll` call — i.e. a poller restart — because it's keyed against
    Postgres (the `events` UNIQUE constraint), not process memory.
    """
    engine = seeded
    first = poll.run_poll(
        interval=1,
        tickers=[TICKER],
        engine=engine,
        notifiers=[],
        now_fn=lambda: NOW,
        sleep_fn=lambda s: None,
        max_ticks=1,
    )
    assert first.rows_written == 1

    # A brand-new call — simulating a process restart, no shared state.
    second = poll.run_poll(
        interval=1,
        tickers=[TICKER],
        engine=engine,
        notifiers=[],
        now_fn=lambda: NOW,
        sleep_fn=lambda s: None,
        max_ticks=1,
    )
    assert second.rows_written == 0

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM events WHERE ticker = :t AND signal_date = :d"),
            {"t": TICKER, "d": SIGNAL_DATE},
        ).scalar()
    assert count == 1
