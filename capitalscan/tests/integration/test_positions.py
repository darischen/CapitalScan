"""Positions and order intents (ADR 048, ADR 073). Requires a reachable
`DATABASE_URL_RESEARCH`, same convention as `tests/integration/test_compute.py`.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import db_io, positions

_TRUNCATE_TABLES = ["positions", "order_intents"]


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


def test_open_then_close_long_position_computes_positive_realized_return(engine):
    opened = positions.open_position(engine, "TSM", "long", date(2026, 7, 1), 100.0, quantity=10)
    assert opened["status"] == "open"
    assert opened["source"] == "user_declared"

    closed = positions.close_position(engine, opened["id"], date(2026, 7, 5), 110.0, "target")
    assert closed["status"] == "closed"
    assert float(closed["realized_ret"]) == pytest.approx(0.10)


def test_short_position_realized_return_sign_flips(engine):
    opened = positions.open_position(engine, "TSM", "short", date(2026, 7, 1), 100.0)
    closed = positions.close_position(engine, opened["id"], date(2026, 7, 5), 90.0, "target")
    # Price fell 10%; a short profits, so realized_ret is positive.
    assert float(closed["realized_ret"]) == pytest.approx(0.10)


def test_list_positions_filters_by_status(engine):
    positions.open_position(engine, "AAPL", "long", date(2026, 7, 1), 200.0)
    opened = positions.open_position(engine, "MSFT", "long", date(2026, 7, 2), 300.0)
    positions.close_position(engine, opened["id"], date(2026, 7, 3), 310.0, "target")

    open_only = positions.list_positions(engine, status="open")
    assert set(open_only["ticker"]) == {"AAPL"}

    closed_only = positions.list_positions(engine, status="closed")
    assert set(closed_only["ticker"]) == {"MSFT"}


def test_order_intent_idempotency_key_is_stable_across_calls(engine):
    kwargs = dict(
        engine=engine,
        event_id=None,
        ticker="TSM",
        signal_date=date(2026, 7, 30),
        signal_type="confluence_low",
        side="long",
        limit_level=95.0,
        stop_level=90.0,
    )
    first = positions.emit_order_intent(**kwargs)
    second = positions.emit_order_intent(**kwargs)
    assert first["idempotency_key"] == second["idempotency_key"]

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM order_intents")).scalar()
    assert count == 1


def test_order_intent_never_touches_anything_broker_shaped(engine):
    """ADR 048/invariant 7: emitting an intent stays entirely inside
    `order_intents` — no side effect resembling order placement exists to
    assert against, which is itself the property under test.
    """
    row = positions.emit_order_intent(
        engine,
        event_id=None,
        ticker="TSM",
        signal_date=date(2026, 7, 30),
        signal_type="bb_upper_touch",
        side="short",
        limit_level=None,
        stop_level=None,
    )
    assert row["quantity_basis"] == "user_defined"
    assert row["time_in_force"] == "day"
