"""`db_io.drop_superseded_slot_events`: a write keeps one type per slot.

`signal_type` is in the `events` upsert key and the debounce slot is
`(ticker, signal_date, side)` per entry kind, so a slot whose inputs moved
between runs gained a second row instead of replacing the first. Traced
2026-11-17: HWM 2026-10-08 (a bar revised the morning after) and 411 CTVA
slots (a history re-adjusted on 2026-10-08). Verified against a real
database the same day, inside a rolled-back transaction: the CTVA slot lost
its stale `confluence_high` row and kept the other side's row, and HWM's
stale row stayed because a prediction references it.
"""

from __future__ import annotations

import inspect

import pandas as pd

from capitalscan.jobs import compute, db_io
from capitalscan.research import backtest

SQL = db_io.DROP_SUPERSEDED_SLOT_SQL


def test_an_empty_write_touches_nothing():
    """`engine` is a bare object: any attempt to connect would raise."""
    assert db_io.drop_superseded_slot_events(object(), pd.DataFrame()) == 0


def test_the_slot_is_config_ticker_date_side_and_entry_kind():
    for column in ("config_hash", "ticker", "signal_date", "side", "entry_kind"):
        assert f"e.{column} = w.{column}" in SQL


def test_only_a_different_type_in_the_slot_is_deleted():
    """The row the caller just wrote has the same type and must survive."""
    assert "e.signal_type <> w.signal_type" in SQL


def test_every_referenced_row_is_kept():
    """The forward log and the poller's records point at `events.id`. A
    deleted event would leave them pointing at nothing (ADR 207 kept EQR's
    and PSKY's predicted events for the same reason)."""
    assert set(db_io.EVENT_REFERENCING_TABLES) == {
        "predictions",
        "signal_reports",
        "order_intents",
        "quotes_live",
    }
    for table in db_io.EVENT_REFERENCING_TABLES:
        assert f"NOT EXISTS (SELECT 1 FROM {table} r WHERE r.event_id = e.id)" in SQL


def test_the_parameters_come_from_the_written_frame():
    captured: dict = {}

    class _Result:
        rowcount = 1

    class _Conn:
        def execute(self, statement, params):
            captured.update(params)
            return _Result()

    class _Begin:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *exc):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

    written = pd.DataFrame(
        [
            {
                "config_hash": "h",
                "ticker": "CTVA",
                "signal_date": pd.Timestamp("2022-08-15"),
                "side": "short",
                "entry_kind": "next_open",
                "signal_type": "stoch_overbought",
            }
        ]
    )
    assert db_io.drop_superseded_slot_events(_Engine(), written) == 1
    assert captured["hashes"] == ["h"]
    assert captured["tickers"] == ["CTVA"]
    assert captured["dates"] == [pd.Timestamp("2022-08-15").date()]
    assert captured["sides"] == ["short"]
    assert captured["kinds"] == ["next_open"]
    assert captured["types"] == ["stoch_overbought"]


def test_both_event_writers_reconcile_after_their_write():
    events_src = inspect.getsource(compute.run_events)
    backtest_src = inspect.getsource(backtest.run_backtest)
    assert "db_io.drop_superseded_slot_events(engine, pd.DataFrame(deduped))" in events_src
    assert "db_io.drop_superseded_slot_events(engine, events)" in backtest_src
    # After the write, or the reconcile would compare against the old rows.
    assert events_src.index("drop_superseded_slot_events") > events_src.index('"events",')
    assert backtest_src.index("drop_superseded_slot_events") > backtest_src.index("copy_upsert(")
