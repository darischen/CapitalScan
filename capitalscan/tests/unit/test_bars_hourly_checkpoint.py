"""`run_bars_hourly` must checkpoint per ticker (BUILD.md §5.4).

The full hourly backfill is a ~4.6 hour job: 725 days walked in 60-day
windows across ~630 tickers at 0.5 req/s. CLAUDE.md requires a checkpoint
on anything over 10 minutes, and BUILD.md §5.4 says "checkpoint per
ticker" specifically. Accumulating every frame in memory and writing once
at the end means an interrupt at ticker 600 of 630 discards all 600.

No database here on purpose. These tests stub the engine and the fetcher
so they can run while a real backfill is in flight —
`tests/integration/test_ingest.py` truncates `bars` and `tickers`, so it
is not safe to run concurrently with one.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import ingest

START = date(2024, 1, 1)
END = date(2024, 3, 1)


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _FakeEngine:
    """Satisfies `_finish_run`'s `engine.begin()` without a real connection."""

    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()


def _hourly_frame(ticker: str) -> pd.DataFrame:
    ts = pd.date_range("2024-01-02 09:30", periods=6, freq="h")
    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": ts,
            "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "high": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "low": [99.5, 100.5, 101.5, 102.5, 103.5, 104.5],
            "close": [100.2, 101.2, 102.2, 103.2, 104.2, 105.2],
            "volume": [1_000, 1_100, 1_200, 1_300, 1_400, 1_500],
        }
    )


@pytest.fixture()
def upserted(monkeypatch) -> list[str]:
    """Records the ticker of every batch reaching `db_io.upsert`, in order."""
    seen: list[str] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "bars" and len(data):
            seen.extend(sorted(set(data["ticker"])))
        return len(data)

    monkeypatch.setattr(ingest.db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    return seen


def test_completed_tickers_are_persisted_when_a_later_fetch_raises(monkeypatch, upserted):
    """An interrupt must not discard tickers that already came back clean."""

    def fake_fetch(ticker: str, start: date, end: date) -> pd.DataFrame:
        if ticker == "CCC":
            raise RuntimeError("interrupted")
        return _hourly_frame(ticker)

    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", fake_fetch)

    with pytest.raises(RuntimeError, match="interrupted"):
        ingest.run_bars_hourly(["AAA", "BBB", "CCC"], START, END, engine=_FakeEngine())

    assert upserted == ["AAA", "BBB"]


def test_rows_written_accumulates_across_tickers(monkeypatch, upserted):
    """Per-ticker writes must still report one total, not just the last batch."""
    monkeypatch.setattr(
        ingest.yahoo, "fetch_bars_hourly", lambda ticker, start, end: _hourly_frame(ticker)
    )

    report = ingest.run_bars_hourly(["AAA", "BBB"], START, END, engine=_FakeEngine())

    assert upserted == ["AAA", "BBB"]
    assert report.rows_written == 12
    assert report.tickers == ["AAA", "BBB"]


def test_a_ticker_with_no_hourly_data_does_not_abort_the_rest(monkeypatch, upserted):
    """Delisted names return empty inside the 725-day window; skip, don't stop."""

    def fake_fetch(ticker: str, start: date, end: date) -> pd.DataFrame:
        if ticker == "DEAD":
            return pd.DataFrame(columns=["ticker", "ts", "open", "high", "low", "close", "volume"])
        return _hourly_frame(ticker)

    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", fake_fetch)

    report = ingest.run_bars_hourly(["AAA", "DEAD", "BBB"], START, END, engine=_FakeEngine())

    assert upserted == ["AAA", "BBB"]
    assert report.tickers == ["AAA", "BBB"]
