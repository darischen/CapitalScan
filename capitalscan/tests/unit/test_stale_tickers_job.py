"""The nightly's stale-ticker report: its queries, its `runs` row, its place.

A report, never an action. These tests pin that it reads only, that it
records one `runs` row with the list in `notes`, and that a failure inside
it cannot fail the nightly.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import date, datetime

import pytest

from capitalscan.core.config import StaleTickerParams
from capitalscan.jobs import cli, ingest
from capitalscan.jobs import stale_tickers as job


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return iter([r[0] for r in self._rows])


class _Conn:
    def __init__(self, answers):
        self.answers = answers
        self.sql: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        for needle, rows in self.answers.items():
            if needle in sql:
                return _Result(rows)
        raise AssertionError(f"unexpected query: {sql}")


class _Engine:
    def __init__(self, answers):
        self.conn = _Conn(answers)

    @contextmanager
    def connect(self):
        yield self.conn

    def begin(self):
        raise AssertionError("the report must not open a write transaction")


class TestQueries:
    def test_last_bars_is_one_statement_over_active_tickers(self) -> None:
        sql = job.LAST_BARS_SQL
        assert "t.is_active" in sql
        assert "b.interval = '1d'" in sql
        assert "max(b.ts)" in sql
        for verb in ("UPDATE", "INSERT", "DELETE"):
            assert verb not in sql.upper()

    def test_fetch_last_bars_maps_rows(self) -> None:
        eng = _Engine({"FROM tickers": [("EA", "712515", date(2026, 8, 4)), ("NEW", None, None)]})
        rows = job.fetch_last_bars(eng)  # type: ignore[arg-type]
        assert [(r.ticker, r.cik, r.last_bar) for r in rows] == [
            ("EA", "712515", date(2026, 8, 4)),
            ("NEW", None, None),
        ]
        assert len(eng.conn.sql) == 1

    def test_fetch_last_bars_accepts_a_datetime(self) -> None:
        eng = _Engine({"FROM tickers": [("EA", None, datetime(2026, 8, 4))]})
        (r,) = job.fetch_last_bars(eng)  # type: ignore[arg-type]
        assert r.last_bar == date(2026, 8, 4)

    def test_sessions_come_from_trading_days(self) -> None:
        eng = _Engine({"FROM trading_days": [(date(2026, 11, 16),), (date(2026, 11, 17),)]})
        got = job.fetch_sessions(eng, date(2026, 11, 13), date(2026, 11, 17))  # type: ignore[arg-type]
        assert got == [date(2026, 11, 16), date(2026, 11, 17)]
        assert "trading_days" in eng.conn.sql[0]


@pytest.fixture
def _fake_runs(monkeypatch):
    recorded: list[ingest.IngestReport] = []

    @contextmanager
    def _run_job(engine, name, params):
        report = ingest.IngestReport(job=name, run_id="test-run-id")
        recorded.append(report)
        yield report

    monkeypatch.setattr(ingest, "run_job", _run_job)
    return recorded


class TestRun:
    def test_records_one_runs_row_with_notes(self, _fake_runs) -> None:
        eng = _Engine(
            {
                "FROM tickers": [
                    ("PSKY", "813828", date(2026, 10, 1)),
                    ("SKYD", "813828", date(2026, 11, 17)),
                    ("AAPL", "320193", date(2026, 11, 17)),
                ],
                "FROM trading_days": [(date(2026, 10, d),) for d in range(2, 31)]
                + [(date(2026, 11, d),) for d in range(2, 18)],
            }
        )
        lines: list[str] = []
        stale = job.run_stale_ticker_report(
            eng,  # type: ignore[arg-type]
            date(2026, 11, 17),
            StaleTickerParams(),
            echo=lines.append,
        )
        assert [s.ticker for s in stale] == ["PSKY"]
        (report,) = _fake_runs
        assert report.job == "stale_tickers"
        assert report.rows_written == 0
        assert report.notes is not None
        assert report.notes.startswith("1 stale active ticker(s)")
        assert "PSKY" in report.notes
        assert any("possible symbol change" in ln for ln in lines)

    def test_nothing_stale_prints_one_line(self, _fake_runs) -> None:
        eng = _Engine(
            {
                "FROM tickers": [("AAPL", "320193", date(2026, 11, 17))],
                "FROM trading_days": [(date(2026, 11, 17),)],
            }
        )
        lines: list[str] = []
        assert job.run_stale_ticker_report(eng, date(2026, 11, 17), echo=lines.append) == []  # type: ignore[arg-type]
        assert len(lines) == 1
        assert "0 stale" in lines[0]


class TestNightlyPlacement:
    def _src(self) -> str:
        return inspect.getsource(cli.nightly)

    def test_it_runs_after_the_session_bar_catchup(self) -> None:
        src = self._src()
        assert src.index("tickers_missing_session(") < src.index("run_stale_ticker_report(")

    def test_it_runs_before_indicators(self) -> None:
        src = self._src()
        assert src.index("run_stale_ticker_report(") < src.index("compute.run_indicators(")

    def test_a_failure_is_reported_not_fatal(self) -> None:
        lines = self._src().splitlines()
        call = next(i for i, ln in enumerate(lines) if "run_stale_ticker_report(" in ln)
        assert any(ln.strip() == "try:" for ln in lines[call - 4 : call])
        after = "\n".join(lines[call : call + 12])
        assert "except Exception" in after

    def test_the_job_module_never_writes(self) -> None:
        src = inspect.getsource(job)
        for verb in ("UPDATE ", "INSERT ", "DELETE ", "db_io.upsert", "db_io.append"):
            assert verb not in src
