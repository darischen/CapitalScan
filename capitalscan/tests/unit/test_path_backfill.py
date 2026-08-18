from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import DEFAULT_CONFIG
from capitalscan.core.returns import entry_offset_for
from capitalscan.core.types import EntryKind, Side
from capitalscan.jobs import db_io
from capitalscan.jobs.config import config_hash as jobs_config_hash
from capitalscan.research import path_backfill as path_backfill_mod
from capitalscan.research.path_backfill import (
    _compute_rows_for_ticker,
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


def test_compute_rows_for_ticker_skips_event_whose_signal_bar_is_not_yet_ingested():
    # Reproduces the real crash from the first live `cscan path backfill`
    # run: a poller-created live event with signal_date=today, filed
    # before that day's `1d` bar has landed. Must be skipped, not raise.
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    events = pd.DataFrame(
        {
            "id": [1, 2],
            "entry_price": [100.0, 105.0],
            "side": [Side.LONG.value, Side.LONG.value],
            "signal_date": [date(2024, 1, 3), date(2024, 6, 1)],  # 2nd date has no bar
        }
    )
    combined, window_updates, processed, skipped_unfilled, skipped_no_bar = (
        _compute_rows_for_ticker(events, bars, window_days=10)
    )
    assert processed == 2
    assert skipped_unfilled == 0
    assert skipped_no_bar == 1
    assert list(combined["event_id"].unique()) == [1]
    assert [u["id"] for u in window_updates] == [1]


def test_compute_rows_for_ticker_still_skips_nan_entry_price_as_unfilled():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    events = pd.DataFrame(
        {
            "id": [1],
            "entry_price": [float("nan")],
            "side": [Side.LONG.value],
            "signal_date": [date(2024, 1, 3)],
        }
    )
    combined, window_updates, processed, skipped_unfilled, skipped_no_bar = (
        _compute_rows_for_ticker(events, bars, window_days=10)
    )
    assert processed == 1
    assert skipped_unfilled == 1
    assert skipped_no_bar == 0
    assert combined.empty
    assert window_updates == []


def test_incremental_capture_matches_one_shot_backfill_once_window_is_complete():
    # Task 10.6 acceptance criterion #2, made an actual assertion rather
    # than a docstring claim: an event captured incrementally, night after
    # night, across several partial-window calls must land on the exact
    # same rows a single one-shot backfill call would produce once bars
    # cover the full window. Both `run_path_backfill` and `run_path_capture`
    # funnel through `_compute_rows_for_ticker` — this exercises that
    # shared function directly, holding the underlying bars fixed (a
    # `bars`-revision between calls is a separate, already-documented
    # class of drift; see `path_reconcile.RECENT_BARS_REVISION_DAYS`).
    dates = [date(2024, 1, i) for i in range(1, 20)]
    full_bars = _ticker_bars(dates)
    events = pd.DataFrame(
        {
            "id": [1],
            "entry_price": [100.0],
            "side": [Side.LONG.value],
            "signal_date": [date(2024, 1, 1)],
        }
    )
    window_days = 11

    # "Capture" simulated as successive nightly calls, each seeing more of
    # the bars history than the last, exactly like the real job re-reading
    # `bars` after another trading day lands. Only the final (window-complete)
    # call's output matters for this comparison.
    incremental_result = None
    for n_days_available in (5, 8, 11, 19):
        partial_bars = full_bars.iloc[:n_days_available]
        incremental_result, _, _, _, _ = _compute_rows_for_ticker(events, partial_bars, window_days)

    backfill_result, _, _, _, _ = _compute_rows_for_ticker(events, full_bars, window_days)

    pd.testing.assert_frame_equal(
        incremental_result.reset_index(drop=True), backfill_result.reset_index(drop=True)
    )


class _FakeConn:
    def __init__(self, ticker_rows, update_calls, engine_url, ticker_query_calls=None):
        self._ticker_rows = ticker_rows
        self._update_calls = update_calls
        self._engine_url = engine_url
        self._ticker_query_calls = ticker_query_calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        text_ = str(stmt)
        if "SELECT DISTINCT ticker FROM events" in text_:
            if self._ticker_query_calls is not None:
                self._ticker_query_calls.append((text_, params))
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
        self.ticker_query_calls: list = []
        self.url = _FakeURL()

    def connect(self):
        return _FakeConn(self._ticker_rows, self.update_calls, self.url, self.ticker_query_calls)

    def begin(self):
        return _FakeConn(self._ticker_rows, self.update_calls, self.url, self.ticker_query_calls)


def test_run_path_backfill_serial_aggregates_across_tickers(monkeypatch):
    # No real database: `_compute_ticker_path` (the only function that
    # opens a connection to `bars`/`events`) is monkeypatched to return
    # canned per-ticker results, matching this test file's established
    # no-real-IO convention.
    fake_engine = _FakeEngine(ticker_rows=["AAA", "BBB", "CCC"])
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
            0,
        ),
        "BBB": (
            "BBB",
            pd.DataFrame(columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]),
            [],
            1,
            1,
            0,
        ),
        "CCC": (
            # A live event whose signal_date has no `1d` bar yet — must be
            # counted separately, not conflated with "unfilled" (findings
            # from the first real `cscan path backfill` run).
            "CCC",
            pd.DataFrame(columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]),
            [],
            1,
            0,
            1,
        ),
    }

    def fake_compute_ticker_path(
        ticker, window_days, database_url, incomplete_only=False, config_hash=None
    ):
        return canned[ticker]

    upsert_calls: list = []

    def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
        upsert_calls.append((table_name, len(data), conflict_cols))
        return len(data)

    monkeypatch.setattr(path_backfill_mod, "_compute_ticker_path", fake_compute_ticker_path)
    monkeypatch.setattr(db_io, "upsert", fake_upsert)

    report = run_path_backfill(fake_engine, DEFAULT_CONFIG, "run-test", quiet=True, max_workers=1)

    assert report.events_processed == 3
    assert report.events_skipped_unfilled == 1
    assert report.events_skipped_no_signal_bar == 1
    assert report.rows_written == 2
    assert sorted(report.tickers) == ["AAA", "BBB", "CCC"]
    assert upsert_calls == [("path", 2, ["event_id", "day_offset"])]
    assert fake_engine.update_calls == [[{"id": 1, "n": 2}]]
    assert "fwd_window_days" not in fake_engine.ticker_query_calls[0][0]


def test_run_path_capture_scopes_ticker_query_to_incomplete_windows(monkeypatch):
    # Task 10.6: `run_path_capture` must select only tickers with at least
    # one event whose forward window is still incomplete — the SQL text
    # itself is the contract here, since a real database is what actually
    # enforces the filter.
    fake_engine = _FakeEngine(ticker_rows=["AAA"])

    def fake_compute_ticker_path(
        ticker, window_days, database_url, incomplete_only=False, config_hash=None
    ):
        assert incomplete_only is True
        return (
            ticker,
            pd.DataFrame(columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]),
            [],
            0,
            0,
            0,
        )

    monkeypatch.setattr(path_backfill_mod, "_compute_ticker_path", fake_compute_ticker_path)

    report = path_backfill_mod.run_path_capture(
        fake_engine, DEFAULT_CONFIG, "run-test", quiet=True, max_workers=1
    )

    assert report.tickers == ["AAA"]
    query_text, params = fake_engine.ticker_query_calls[0]
    assert "fwd_window_days IS NULL OR fwd_window_days < :window_days" in query_text
    # `config_hash` joined the ticker selection on 2026-08-17. Unscoped,
    # this walked every generation ever backtested — 23 of them, 5,568,263
    # events, 2h56m on the last full run — rebuilding forward paths for
    # populations nothing reads. Asserted as exact equality rather than
    # membership so a future filter cannot be added here unnoticed.
    assert "config_hash = :chash" in query_text
    assert params == {
        "window_days": window_days_for_config(DEFAULT_CONFIG),
        "chash": jobs_config_hash(DEFAULT_CONFIG),
    }


def test_events_query_for_ticker_adds_incompleteness_filter_only_when_requested():
    # `_compute_ticker_path`'s events read must add the same incompleteness
    # filter regardless of which caller (backfill vs. capture) invoked
    # it — this is what makes a capture run touch only accumulating events
    # rather than every event on the ticker, and leaves backfill's own
    # full-recompute query untouched.
    from capitalscan.research.path_backfill import _events_query_for_ticker

    full_query, full_params = _events_query_for_ticker("AAA", window_days=11, incomplete_only=False)
    assert "fwd_window_days" not in full_query
    assert full_params == {"ticker": "AAA"}

    scoped_query, scoped_params = _events_query_for_ticker(
        "AAA", window_days=11, incomplete_only=True
    )
    assert "fwd_window_days IS NULL OR fwd_window_days < :window_days" in scoped_query
    assert scoped_params == {"ticker": "AAA", "window_days": 11}
