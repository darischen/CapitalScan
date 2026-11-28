"""`ingest.run_hourly_split_repair`: stored hourly bars catch up with a split.

The fetch adjusts against the splits it knows. SOXX split 3-for-1 on
2026-11-05, recorded after two years of its hourly bars were stored, and
they stayed at 3.000x the daily series. 15 tickers carried the same gap on
2026-11-28. A dry run against research that day re-adjusted 20,490 bars
across 30 tickers and every written day landed within 0.989x to 1.018x of
its daily high. DD's compounding splits and seven spin-off ratios near 1.0
(SPGI, HON, CMCSA and four more) stayed as stored.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from capitalscan.jobs import cli, ingest


def _hourly(day: str, high: float) -> list[dict]:
    return [
        {
            "ticker": "SOXX",
            "ts": pd.Timestamp(f"{day} {h}:30", tz="UTC"),
            "open": high - 1,
            "high": high,
            "low": high - 2,
            "close": high - 1,
        }
        for h in (14, 15, 16)
    ]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"written": [], "stored": [], "daily": [], "splits": []}

    @contextmanager
    def fake_run_job(engine: Any, job: str, params: dict) -> Any:
        state["job"] = job
        yield SimpleNamespace(run_id="r1", rows_written=0, notes=None)

    def fake_read_sql(sql: Any, con: Any, params: Any = None) -> pd.DataFrame:
        if "corporate_actions" in str(sql):
            return pd.DataFrame(state["splits"], columns=["ticker", "ex_date", "ratio"])
        return pd.DataFrame(state["stored"])

    def fake_daily(engine: Any, tickers: list[str], start: Any = None, end: Any = None):
        return pd.DataFrame(state["daily"], columns=["ticker", "d", "high", "low"])

    def fake_upsert(engine: Any, table: str, frame: pd.DataFrame, keys: list, **kw: Any) -> int:
        state["written"].append((table, frame.copy(), kw.get("update_columns")))
        return len(frame)

    class _Engine:
        def connect(self) -> Any:
            return self

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(ingest, "run_job", fake_run_job)
    monkeypatch.setattr(ingest.pd, "read_sql", fake_read_sql)
    monkeypatch.setattr(ingest, "_read_daily_range", fake_daily)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    state["engine"] = _Engine()
    return state


def _run(state: dict[str, Any]) -> Any:
    return ingest.run_hourly_split_repair(state["engine"])


def test_an_unadjusted_day_is_divided_by_the_split(wired: dict[str, Any]) -> None:
    wired["splits"] = [("SOXX", date(2026, 11, 5), 3.0)]
    wired["stored"] = _hourly("2026-10-30", 510.0)
    wired["daily"] = [("SOXX", date(2026, 10, 30), 170.0, 160.0)]
    report = _run(wired)
    assert wired["job"] == "hourly_split_repair"
    (table, frame, update_columns), *_ = wired["written"]
    assert table == "bars"
    assert frame["high"].tolist() == [170.0, 170.0, 170.0]
    assert (frame["adj_close"] == frame["close"]).all()
    assert set(frame["interval"]) == {"1h"}
    assert "volume" not in update_columns
    assert report.rows_written == 3


def test_a_day_already_adjusted_is_left_alone(wired: dict[str, Any]) -> None:
    """A second run finds every day near 1.0 and writes nothing."""
    wired["splits"] = [("SOXX", date(2026, 11, 5), 3.0)]
    wired["stored"] = _hourly("2026-10-30", 170.0)
    wired["daily"] = [("SOXX", date(2026, 10, 30), 170.0, 160.0)]
    assert _run(wired).rows_written == 0
    assert wired["written"] == []


def test_a_day_with_no_daily_bar_is_never_divided(wired: dict[str, Any]) -> None:
    """The fetch path divides such a day naively, which on a stored bar
    that is already adjusted would adjust it twice."""
    wired["splits"] = [("SOXX", date(2026, 11, 5), 3.0)]
    wired["stored"] = _hourly("2026-10-30", 170.0)
    wired["daily"] = []
    assert _run(wired).rows_written == 0


def test_a_result_that_disagrees_with_the_daily_bar_is_refused(wired: dict[str, Any]) -> None:
    """DD: the factor compounds two splits the daily series does not."""
    wired["splits"] = [("DD", date(2025, 11, 3), 2.39), ("DD", date(2026, 6, 24), 1 / 3)]
    wired["stored"] = [dict(r, ticker="DD") for r in _hourly("2025-10-30", 30.0)]
    wired["daily"] = [("DD", date(2025, 10, 30), 85.0, 80.0)]
    report = _run(wired)
    assert wired["written"] == []
    assert "1 day(s) left as stored" in report.notes


def test_nightly_repairs_after_actions_and_before_the_backtest() -> None:
    source = inspect.getsource(cli.nightly)
    actions = source.index("ingest.run_actions(")
    repair = source.index("ingest.run_hourly_split_repair(")
    backtest = source.index("backtest(")
    assert actions < repair < backtest
