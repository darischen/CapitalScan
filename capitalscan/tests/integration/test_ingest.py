"""Integration test for the ingest layer (BUILD session 5 acceptance).

Requires a reachable `DATABASE_URL_RESEARCH` (the local Docker Postgres in
dev, the CI service container in the slow tier). Network fetchers are
mocked at the module boundary, same pattern as
`tests/integration/test_fetchers.py` — deterministic, no dependence on
live Yahoo/SEC data.

What this exercises end to end, matching BUILD.md's acceptance criterion:
`cscan backfill --tickers TSM,NVDA,AAPL --start ... --through-validate`,
`cscan validate --report` clean at 'reject' severity, and an identical
rerun leaving row counts unchanged.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import db_io, ingest
from capitalscan.jobs.fetch import yahoo

TICKERS = ["TSM", "NVDA", "AAPL"]
START = date(2020, 1, 2)
END = date(2020, 2, 1)

_TRUNCATE_TABLES = ["bar_rejects", "bars", "corporate_actions", "market_days", "runs", "tickers"]


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


def _synthetic_daily(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    offsets = np.arange(len(idx), dtype=float) * 0.1
    frames = []
    for i, ticker in enumerate(tickers):
        base = 100.0 + i * 10
        frames.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "ts": idx,
                    "open": base + offsets,
                    "high": base + 1 + offsets,
                    "low": base - 1 + offsets,
                    "close": base + 0.5 + offsets,
                    "adj_close": base + 0.5 + offsets,
                    "volume": 1_000_000,
                    "adj_factor": 1.0,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _empty_actions(ticker: str) -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "ts", "action_type", "value"])


@pytest.fixture(autouse=True)
def _mock_fetchers(monkeypatch):
    monkeypatch.setattr(yahoo, "fetch_bars_daily", _synthetic_daily)
    monkeypatch.setattr(yahoo, "fetch_actions", _empty_actions)


def test_backfill_writes_bars_and_reports_clean_validation(engine):
    result = ingest.run_backfill(TICKERS, START, through_validate=True, engine=engine)

    bars_step = next(s for s in result.steps if s.job == "bars_daily")
    assert bars_step.rows_written > 0
    assert bars_step.rows_rejected == 0

    assert result.validation.clean
    assert result.validation.reject_counts.empty
    assert set(result.validation.coverage["ticker"]) == set(TICKERS)


def test_backfill_rerun_is_idempotent(engine):
    ingest.run_backfill(TICKERS, START, through_validate=True, engine=engine)
    with engine.connect() as conn:
        n_bars_first = conn.execute(text("SELECT count(*) FROM bars")).scalar()
        n_tickers_first = conn.execute(text("SELECT count(*) FROM tickers")).scalar()

    ingest.run_backfill(TICKERS, START, through_validate=True, engine=engine)
    with engine.connect() as conn:
        n_bars_second = conn.execute(text("SELECT count(*) FROM bars")).scalar()
        n_tickers_second = conn.execute(text("SELECT count(*) FROM tickers")).scalar()

    assert n_bars_first == n_bars_second
    assert n_tickers_first == n_tickers_second


def test_bars_daily_rejects_go_to_bar_rejects_not_bars(engine):
    def bad_daily(tickers, start, end):
        good = _synthetic_daily(tickers, start, end)
        bad_row = good.iloc[[0]].copy()
        bad_row["high"] = bad_row["low"] - 1.0  # structurally invalid
        bad_row["ts"] = pd.Timestamp(start) - pd.Timedelta(days=1)
        return pd.concat([good, bad_row], ignore_index=True)

    yahoo.fetch_bars_daily = bad_daily
    ingest.ensure_tickers(TICKERS, engine=engine)
    report = ingest.run_bars_daily(TICKERS, START, END, engine=engine)

    assert report.rows_rejected == 1
    with engine.connect() as conn:
        n_rejects = conn.execute(
            text("SELECT count(*) FROM bar_rejects WHERE severity = 'reject'")
        ).scalar()
    assert n_rejects == 1


def test_run_id_and_git_sha_recorded_on_runs_row(engine):
    ingest.ensure_tickers(TICKERS, engine=engine)
    report = ingest.run_bars_daily(TICKERS, START, END, engine=engine)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, git_sha, rows_written FROM runs WHERE run_id = :rid"),
            {"rid": report.run_id},
        ).one()
    assert row.status == "ok"
    assert row.git_sha  # non-empty
    assert row.rows_written == report.rows_written


def test_upsert_is_conflict_safe_for_bars_primary_key(engine):
    ingest.ensure_tickers(TICKERS, engine=engine)
    first = ingest.run_bars_daily(TICKERS, START, END, engine=engine)
    second = ingest.run_bars_daily(TICKERS, START, END, engine=engine)

    assert first.rows_written == second.rows_written
    with engine.connect() as conn:
        n_bars = conn.execute(text("SELECT count(*) FROM bars")).scalar()
    assert n_bars == first.rows_written
