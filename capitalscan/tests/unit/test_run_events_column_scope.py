"""`run_events` must upsert `events` with the column-scoped update from
Ruling C4 (Session 9 Task 9a), not the old blind "every column" update.

No live database: `_read_bars_range`, `_read_indicators_range`,
`_read_market_days`, `_read_universe_flags` are monkeypatched directly (they
are `compute`'s own module functions, called by name from `run_events`), and
`db_io.upsert`/`db_io.append` are stubbed so `run_job`'s bookkeeping writes
go nowhere. Only `db_io.upsert`'s call for the `events` table is captured.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import compute, db_io


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _FakeEngine:
    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()

    @contextmanager
    def connect(self):  # noqa: ANN201
        yield _FakeConn()


def _bars() -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "ticker": "TSM",
                "ts": pd.Timestamp("2026-07-30"),
                "open": 96.0,
                "high": 96.0,
                "low": 94.0,
                "close": 95.0,
                "volume": 1_000_000,
            }
        ]
    )
    return df


def _indicators() -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "ticker": "TSM",
                "ts": pd.Timestamp("2026-07-29"),
                "bb_lower": 95.0,
                "bb_upper": 999.0,
                "k_full": 15.0,
            }
        ]
    )
    return df


@pytest.fixture()
def captured_upsert(monkeypatch):
    calls: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
        calls.append(
            {
                "table_name": table_name,
                "data": data,
                "conflict_cols": conflict_cols,
                "update_columns": update_columns,
            }
        )
        return len(data) if hasattr(data, "__len__") else 0

    monkeypatch.setattr(db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(db_io, "upsert", fake_upsert)
    monkeypatch.setattr(compute, "_read_bars_range", lambda engine, tickers, start, end: _bars())
    monkeypatch.setattr(
        compute, "_read_indicators_range", lambda engine, tickers, start, end: _indicators()
    )
    monkeypatch.setattr(
        compute,
        "_read_market_days",
        lambda engine, start, end: pd.DataFrame(columns=["ts", "vix_close", "spx_ret_1d"]),
    )
    monkeypatch.setattr(
        compute,
        "_read_universe_flags",
        # `*a` absorbs the config_hash `run_events` now passes: this stub
        # stands in for a real read, and pinning its arity here just makes
        # an unrelated signature change look like a failure of this test.
        lambda engine, tickers, *a: pd.DataFrame(columns=["ticker", "as_of", "in_trade"]),
    )
    return calls


class TestRunEventsColumnScopedUpsert:
    def test_run_events_upserts_events_with_the_owned_column_list(self, captured_upsert):
        compute.run_events(["TSM"], date(2026, 7, 30), date(2026, 7, 30), engine=_FakeEngine())

        events_calls = [c for c in captured_upsert if c["table_name"] == "events"]
        assert len(events_calls) == 1
        assert events_calls[0]["update_columns"] == compute._RUN_EVENTS_UPDATE_COLUMNS

    def test_owned_column_list_excludes_the_backtest_owned_cluster_columns(self):
        """Ruling C5: the backtest owns cluster columns on UPDATE. `_tag_clusters`
        still populates them on the row dict (see `run_events`'s call site),
        but they must never appear in the update-scope list, or a plain
        `run_events` re-run would clobber whatever the backtest last wrote."""
        for col in compute._CLUSTER_COLUMNS:
            assert col not in compute._RUN_EVENTS_UPDATE_COLUMNS

    def test_owned_column_list_matches_build_event_row_minus_keys(self):
        """`_RUN_EVENTS_UPDATE_COLUMNS` should be exactly the signal-side
        columns `_build_event_row` computes, minus the natural-key columns
        (which are conflict_cols, never update targets)."""
        conflict_cols = {"config_hash", "ticker", "signal_date", "signal_type", "entry_kind"}
        sample_row = compute._build_event_row(
            hit=type(
                "Hit",
                (),
                {
                    "ticker": "TSM",
                    "ts": date(2026, 7, 30),
                    "signal_type": type("T", (), {"value": "confluence_low"})(),
                    "signal_types_all": [],
                    "signal_strength": 1,
                    "side": type("S", (), {"value": "long"})(),
                    "touch_level": None,
                    "pctb": 0.1,
                    "k_full": 15.0,
                },
            )(),
            bar=pd.Series({"open": 96.0, "close": 95.0}),
            ind_row=pd.Series({}),
            market_row=None,
            chash="abc",
            run_id="run-1",
        )
        expected = set(sample_row.keys()) - conflict_cols
        assert set(compute._RUN_EVENTS_UPDATE_COLUMNS) == expected
