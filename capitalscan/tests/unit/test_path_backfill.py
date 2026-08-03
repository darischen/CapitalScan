from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import DEFAULT_CONFIG
from capitalscan.core.returns import entry_offset_for
from capitalscan.core.types import EntryKind, Side
from capitalscan.jobs import db_io
from capitalscan.research import path_backfill as path_backfill_mod
from capitalscan.research.path_backfill import (
    fwd_window_for_signal,
    rows_for_event,
    run_path_backfill,
    window_days_for_config,
)


def _ticker_bars(dates):
    # One row per calendar date given, high/low/close all equal to a
    # simple ramp so tests can assert on exact values.
    data = [
        {"ts": pd.Timestamp(d), "high": 100 + i, "low": 90 + i, "close": 95 + i}
        for i, d in enumerate(dates)
    ]
    frame = pd.DataFrame(data)
    return frame.set_index(frame["ts"], drop=False)


def test_fwd_window_for_signal_returns_up_to_window_days_after_signal():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 10
    assert window.index[0] == pd.Timestamp(date(2024, 1, 2))


def test_fwd_window_for_signal_truncates_near_end_of_history_never_pads():
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 3  # only 3 trading days exist after signal_date


def test_fwd_window_for_signal_raises_if_signal_date_not_in_bars():
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    with pytest.raises(ValueError):
        fwd_window_for_signal(bars, date(2024, 6, 1), window_days=10)


def test_rows_for_event_skips_unfilled_entries():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    rows, n = rows_for_event(
        event_id=1,
        entry_price=float("nan"),
        side=Side.LONG,
        signal_date=date(2024, 1, 1),
        ticker_bars=bars,
        window_days=10,
    )
    assert rows.empty
    assert n is None


def test_window_days_for_config_pads_by_max_entry_offset():
    # Default config: max(fwd_ret_horizons)=10, max entry_offset (NEXT_OPEN)=1.
    # A NEXT_OPEN event's fwd_ret_10d needs day_offset=11, so the window
    # must be 11, not 10 (finding #2 of the final review).
    assert max(entry_offset_for(k) for k in EntryKind) == 1
    assert window_days_for_config(DEFAULT_CONFIG) == 11


def test_rows_for_event_full_window_sets_fwd_window_days():
    dates = [date(2024, 1, i) for i in range(1, 20)]
    bars = _ticker_bars(dates)
    rows, n = rows_for_event(
        event_id=7,
        entry_price=100.0,
        side=Side.LONG,
        signal_date=date(2024, 1, 1),
        ticker_bars=bars,
        window_days=10,
    )
    assert n == 10
    assert list(rows["event_id"].unique()) == [7]
    assert list(rows["day_offset"]) == list(range(1, 11))


class _FakeConn:
    def __init__(self, ticker_rows, update_calls, engine_url):
        self._ticker_rows = ticker_rows
        self._update_calls = update_calls
        self._engine_url = engine_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        text_ = str(stmt)
        if "SELECT DISTINCT ticker FROM events" in text_:
            return [(t,) for t in self._ticker_rows]
        if "UPDATE events SET fwd_window_days" in text_:
            self._update_calls.append(params)
            return None
        raise AssertionError(f"unexpected statement in fake engine: {text_}")


class _FakeURL:
    def render_as_string(self, hide_password=False):
        return "postgresql://fake/fake"


class _FakeEngine:
    """Just enough of SQLAlchemy's `Engine` surface for `run_path_backfill`'s
    serial (`max_workers=1`) path: ticker-list query, `fwd_window_days`
    UPDATE, and `.url.render_as_string`. No real database involved.
    """

    def __init__(self, ticker_rows):
        self._ticker_rows = ticker_rows
        self.update_calls: list = []
        self.url = _FakeURL()

    def connect(self):
        return _FakeConn(self._ticker_rows, self.update_calls, self.url)

    def begin(self):
        return _FakeConn(self._ticker_rows, self.update_calls, self.url)


def test_run_path_backfill_serial_aggregates_across_tickers(monkeypatch):
    # No real database: `_compute_ticker_path` (the only function that
    # opens a connection to `bars`/`events`) is monkeypatched to return
    # canned per-ticker results, matching this test file's established
    # no-real-IO convention.
    fake_engine = _FakeEngine(ticker_rows=["AAA", "BBB"])
    canned = {
        "AAA": (
            "AAA",
            pd.DataFrame(
                {
                    "event_id": [1, 1],
                    "day_offset": [1, 2],
                    "favorable": [0.01, 0.02],
                    "adverse": [-0.01, -0.02],
                    "terminal": [0.005, 0.015],
                }
            ),
            [{"id": 1, "n": 2}],
            1,
            0,
        ),
        "BBB": (
            "BBB",
            pd.DataFrame(columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]),
            [],
            1,
            1,
        ),
    }

    def fake_compute_ticker_path(ticker, window_days, database_url):
        return canned[ticker]

    upsert_calls: list = []

    def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
        upsert_calls.append((table_name, len(data), conflict_cols))
        return len(data)

    monkeypatch.setattr(path_backfill_mod, "_compute_ticker_path", fake_compute_ticker_path)
    monkeypatch.setattr(db_io, "upsert", fake_upsert)

    report = run_path_backfill(fake_engine, DEFAULT_CONFIG, quiet=True, max_workers=1)

    assert report.events_processed == 2
    assert report.events_skipped_unfilled == 1
    assert report.rows_written == 2
    assert sorted(report.tickers) == ["AAA", "BBB"]
    assert upsert_calls == [("path", 2, ["event_id", "day_offset"])]
    assert fake_engine.update_calls == [[{"id": 1, "n": 2}]]
