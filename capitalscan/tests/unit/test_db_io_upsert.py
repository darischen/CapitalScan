"""Tests for `db_io.upsert`'s column-scoped update (Session 9 Task 9a, Ruling C4).

The defect: `upsert`'s `DO UPDATE SET` used to cover every non-key column on
the table, built from the table's own schema rather than from the keys
present in the row dicts being written. A caller that sends a row missing
some columns (e.g. `run_events` sending no exit columns) gets those absent
columns overwritten with `EXCLUDED.<col>` = NULL, because Postgres fills a
missing INSERT column with NULL before `EXCLUDED` ever sees it. That is
exactly how a plain `cscan nightly` run (`run_events`) would silently null
every column the backtest (`run_backtest`, Task 9b) had written onto the
same `events` row.

No live database anywhere in this file — a fake engine captures the
compiled `INSERT ... ON CONFLICT DO UPDATE` statement, and we inspect the
compiled SQL text for which columns appear on the left side of `SET`. That
is the actual, observable behavior `update_columns` is supposed to control,
so asserting on it (rather than on some SQLAlchemy-internal attribute) is
the least brittle way to pin this down.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.dialects import postgresql

from capitalscan.jobs import db_io


def _make_events_table() -> Table:
    """A stand-in for the real `events` table shape, trimmed to just enough
    columns to exercise both the signal side (`signal_strength`) and the
    exit/backtest side (`exit_price`, `realized_return`) plus one
    both-own column (`run_id`, Ruling C4) and one cluster column
    (`cluster_id`, Ruling C5)."""
    metadata = MetaData()
    return Table(
        "events",
        metadata,
        Column("config_hash", String, primary_key=True),
        Column("ticker", String, primary_key=True),
        Column("signal_date", String, primary_key=True),
        Column("signal_type", String, primary_key=True),
        Column("entry_kind", String, primary_key=True),
        Column("run_id", String),
        Column("signal_strength", Integer),
        Column("cluster_id", Integer),
        Column("exit_price", Integer),
        Column("realized_return", Integer),
    )


class _CapturingConn:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def execute(self, stmt) -> None:  # noqa: ANN001
        self._sink.append(stmt)


class _FakeEngine:
    """Stands in for a SQLAlchemy `Engine`: `db_io._table` is monkeypatched
    to hand back a real (unattached) `Table` object built above, and
    `.begin()` captures whatever statement `upsert` tries to execute instead
    of hitting a real connection."""

    def __init__(self) -> None:
        self.executed: list = []

    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _CapturingConn(self.executed)


@pytest.fixture()
def engine(monkeypatch) -> _FakeEngine:
    table = _make_events_table()
    fake = _FakeEngine()
    monkeypatch.setattr(db_io, "_table", lambda engine, name: table)
    return fake


CONFLICT_COLS = ["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"]

_ROW = {
    "config_hash": "abc123",
    "ticker": "TSM",
    "signal_date": "2026-07-30",
    "signal_type": "confluence_low",
    "entry_kind": "touch",
    "run_id": "run-1",
    "signal_strength": 1,
    "cluster_id": 42,
    "exit_price": 105,
    "realized_return": 5,
}


def _set_clause_columns(stmt) -> str:
    """Just the `DO UPDATE SET ...` portion of the compiled SQL text, so
    tests assert on the observable `SET` clause rather than a
    SQLAlchemy-internal attribute. Column names also appear earlier in the
    INSERT's column list and VALUES clause regardless of `update_columns`,
    so slicing off everything before `SET` is what makes the assertions
    below actually test the update scope rather than the insert scope."""
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    return compiled.split(" DO UPDATE SET ", 1)[1]


class TestColumnScopedUpdate:
    def test_run_events_shaped_write_does_not_null_exit_columns(self, engine):
        """A row carrying only signal-side keys, updated with only the
        signal-side `update_columns`, must not put the exit columns in the
        SET clause at all — the whole point being that Postgres never gets
        a chance to fill them with NULL from a missing EXCLUDED reference."""
        db_io.upsert(
            engine,
            "events",
            [_ROW],
            CONFLICT_COLS,
            update_columns=["run_id", "signal_strength"],
        )
        sql = _set_clause_columns(engine.executed[0])
        assert "signal_strength = excluded.signal_strength" in sql
        assert "run_id = excluded.run_id" in sql
        assert "exit_price" not in sql
        assert "realized_return" not in sql
        assert "cluster_id" not in sql

    def test_backtest_shaped_write_does_not_null_signal_columns(self, engine):
        """The mirror image: a backtest-style update naming only exit
        columns must leave `signal_strength` out of SET entirely."""
        db_io.upsert(
            engine,
            "events",
            [_ROW],
            CONFLICT_COLS,
            update_columns=["run_id", "exit_price", "realized_return"],
        )
        sql = _set_clause_columns(engine.executed[0])
        assert "exit_price = excluded.exit_price" in sql
        assert "realized_return = excluded.realized_return" in sql
        assert "run_id = excluded.run_id" in sql
        assert "signal_strength" not in sql
        assert "cluster_id" not in sql

    def test_update_columns_none_is_byte_identical_to_today(self, engine):
        """No `update_columns` argument must produce exactly the same SET
        clause as before this change: every non-key column on the table."""
        db_io.upsert(engine, "events", [_ROW], CONFLICT_COLS)
        sql = _set_clause_columns(engine.executed[0])
        for col in ("run_id", "signal_strength", "cluster_id", "exit_price", "realized_return"):
            assert f"{col} = excluded.{col}" in sql

    def test_unknown_column_name_raises(self, engine):
        with pytest.raises(ValueError, match="not_a_real_column"):
            db_io.upsert(
                engine,
                "events",
                [_ROW],
                CONFLICT_COLS,
                update_columns=["not_a_real_column"],
            )

    def test_conflict_column_in_update_columns_raises(self, engine):
        with pytest.raises(ValueError, match="ticker"):
            db_io.upsert(
                engine,
                "events",
                [_ROW],
                CONFLICT_COLS,
                update_columns=["ticker"],
            )

    def test_empty_update_columns_list_raises(self, engine):
        """`update_columns=[]` is distinct from `update_columns=None` — the
        former means "update nothing," a no-op DO UPDATE SET that is almost
        certainly a caller bug (e.g. a programmatically-built list that came
        out empty), not a deliberate request. Left unvalidated this reaches
        SQLAlchemy's own `on_conflict_do_update(set_={})`, which raises
        `set parameter dictionary must not be empty` — confirmed by review
        against a standalone repro. That message names a parameter
        (`set_`) the caller of `upsert()` never passed, so this function
        raises its own descriptive error first instead."""
        with pytest.raises(ValueError, match="empty list"):
            db_io.upsert(engine, "events", [_ROW], CONFLICT_COLS, update_columns=[])

    def test_duplicate_names_in_update_columns_are_harmless(self, engine):
        """A repeated name collapses naturally because `update_cols` is
        built as a dict comprehension keyed on the column name — pinned
        here so a future refactor to a list-based structure doesn't
        silently reintroduce ambiguity (e.g. two SET clauses for one
        column, which Postgres rejects)."""
        db_io.upsert(
            engine,
            "events",
            [_ROW],
            CONFLICT_COLS,
            update_columns=["run_id", "run_id", "signal_strength"],
        )
        sql = _set_clause_columns(engine.executed[0])
        assert sql.count("run_id = excluded.run_id") == 1
        assert "signal_strength = excluded.signal_strength" in sql

    def test_empty_data_short_circuits_before_validation(self, engine):
        """An empty write is a no-op today (line `if not rows: return 0`)
        regardless of `update_columns` — this must not regress into raising
        on a bad column name nobody is actually about to write with."""
        assert db_io.upsert(engine, "events", [], CONFLICT_COLS, update_columns=["nope"]) == 0
