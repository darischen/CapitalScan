"""A prediction is written once and never rewritten (DECISIONS.md, 2026-09-17).

Until this, `run_predict` upserted every column but `id` on `event_id`, and
`nightly` rescored a 45-day lookback. ADR 193's validate window ends at
`today - 5d`, so a weekly refit calibrated on events whose predictions were
then rewritten by that refit. Measured on `wivie`: 2,078 of 2,143 resolved
live-generation rows had been rewritten after their outcome existed. The
forward log is the one measurement ADR 174 calls uncontaminated, and it was
not.

Option A, chosen by the owner: first write wins. No real database here.
"""

from __future__ import annotations

import inspect
from typing import cast

from sqlalchemy import Column, Engine, Integer, MetaData, Table, Text
from sqlalchemy.dialects import postgresql

from capitalscan.jobs import db_io
from capitalscan.jobs import predict as jp

_META = MetaData()
_T = Table(
    "predictions",
    _META,
    Column("id", Integer, primary_key=True),
    Column("event_id", Integer, unique=True),
    Column("p_touch_3", Text),
)


class _Result:
    """Only `all()`: `insert_new` counts `RETURNING` rows. `rowcount` is left
    off on purpose, because the real driver reports -1 for these inserts and
    a fake that answered it would pass a count the database never gives."""

    def __init__(self, inserted: int) -> None:
        self._rows = [(i,) for i in range(inserted)]

    def all(self) -> list[tuple[int]]:
        return self._rows


class _Engine:
    """Captures each statement, compiled for Postgres, and reports that
    `inserted` rows of every batch were new."""

    def __init__(self, inserted: int) -> None:
        self.sql: list[str] = []
        self.inserted = inserted

    def begin(self):  # noqa: ANN201
        engine = self

        class _Ctx:
            def __enter__(self):  # noqa: ANN204
                return self

            def execute(self, stmt):  # noqa: ANN001, ANN202
                engine.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
                return _Result(engine.inserted)

            def __exit__(self, *exc):  # noqa: ANN002, ANN204
                return False

        return _Ctx()


def test_insert_new_does_nothing_on_conflict(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(db_io, "_table", lambda engine, name: _T)
    engine = _Engine(inserted=1)
    db_io.insert_new(
        cast(Engine, engine), "predictions", [{"event_id": 1, "p_touch_3": "0.5"}], ["event_id"]
    )
    assert "ON CONFLICT (event_id) DO NOTHING" in engine.sql[0]
    assert "RETURNING" in engine.sql[0]
    assert "DO UPDATE" not in engine.sql[0]


def test_insert_new_counts_rows_inserted_not_rows_sent(monkeypatch) -> None:  # noqa: ANN001
    """`upsert` returns rows *sent*. Here most rows already exist, and a
    report saying 13,000 written on a night that wrote 40 would hide the
    freeze working."""
    monkeypatch.setattr(db_io, "_table", lambda engine, name: _T)
    rows = [{"event_id": i, "p_touch_3": "0.5"} for i in range(3)]
    assert (
        db_io.insert_new(cast(Engine, _Engine(inserted=1)), "predictions", rows, ["event_id"]) == 1
    )


def test_insert_new_with_no_rows_touches_nothing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(db_io, "_table", lambda engine, name: _T)
    engine = _Engine(inserted=0)
    assert db_io.insert_new(cast(Engine, engine), "predictions", [], ["event_id"]) == 0
    assert engine.sql == []


def test_run_predict_writes_predictions_insert_only() -> None:
    """Structural, like the `breach_depth` guard: the predictions write must
    go through `insert_new`, and no `upsert` may target the table. A later
    edit that restores the upsert reintroduces the contamination silently,
    because every number it produces still looks fine."""
    src = inspect.getsource(jp.run_predict)
    assert 'db_io.insert_new(\n                engine,\n                "predictions"' in src
    assert "db_io.upsert" not in src


def test_the_report_says_how_many_were_kept() -> None:
    report = jp.PredictReport(rows_written=40, rows_kept=13000, tickers=5)
    assert "40 new predictions" in report.summary()
    assert "13000 kept as first written" in report.summary()
