"""Tests for `research/backtest.py`'s per-ticker worker, dispatch, and
cofire post-pass (Session 9, Task 9b).

No live database: `research.backtest._read_bars`, `_read_indicators`,
`_read_market_days`, and `_read_universe_flags` are monkeypatched directly,
matching the established pattern in `test_run_events_column_scope.py`
(`compute`'s own read helpers are stubbed the same way there).

The fixture below produces exactly one signal (`bb_lower_touch`, long) on
one ticker: bar index 5 dips to a `low` of 94.0 against a constant
`bb_lower` of 95.0 read from bar index 4's indicator row (t-1, Ruling C3).
Every other bar's `low` (98.0) stays clear of the band, so nothing else
fires and cluster/debounce behavior is not exercised here (that is
`test_backtest_clusters.py`'s job). `k_full` is held at 50.0 (`stoch_
oversold=20`, `stoch_overbought=80`), so only `bb_lower_touch` fires, never
`confluence_low`. With `stop_mode="atr"` and no `atr_14` supplied, no stop
is placed and `bb_upper=999.0` stays out of reach, so the position times
out on day 5 of the exit window — a clean, fully-resolved TIMEOUT trade.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import Config
from capitalscan.research import backtest

TICKER = "TSM"
N_BARS = 25
SIGNAL_IDX = 5  # bars.iloc[5] is the only bar whose low breaches bb_lower


def _bars() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=N_BARS)
    rows = []
    for i, ts in enumerate(dates):
        # `low` dips to 94.0 (below bb_lower=95.0) only on the signal bar;
        # `high` stays at 96.5 on every bar, well clear of the 4% target
        # (95 * 1.04 = 98.8) so nothing but a TIMEOUT can end the trade.
        low = 94.0 if i == SIGNAL_IDX else 95.5
        rows.append(
            {
                "ticker": TICKER,
                "ts": ts,
                "open": 96.0,
                "high": 96.5,
                "low": low,
                "close": 96.0,
                "adj_close": 96.0,
                "volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def _indicators() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=N_BARS)
    rows = []
    for ts in dates:
        rows.append(
            {
                "ticker": TICKER,
                "ts": ts,
                "bb_lower": 95.0,
                "bb_mid": 500.0,
                "bb_upper": 999.0,
                "bb_pctb": 0.1,
                "bb_width_pct": 0.2,
                "k_full": 50.0,
                "d_full": 50.0,
                "k_fast": 50.0,
                "k_cross_up": False,
                "k_cross_down": False,
                "atr_14": None,
                "rv_pct_252d": 0.5,
                "dd_52w": 0.05,
                "sma200_slope_60": 0.01,
                "vol_z_20d": 0.0,
                "days_to_earnings": None,
            }
        )
    return pd.DataFrame(rows)


def _empty_market() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts", "vix_close", "spx_ret_1d"])


def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "as_of", "in_trade"])


def _empty_hourly() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["ticker", "ts", "open", "high", "low", "close", "adj_close", "volume"]
    )


@pytest.fixture()
def stub_reads(monkeypatch):
    def fake_read_bars(engine, ticker, start, interval):
        return _bars() if interval == "1d" else _empty_hourly()

    monkeypatch.setattr(backtest, "_read_bars", fake_read_bars)
    monkeypatch.setattr(backtest, "_read_indicators", lambda engine, ticker, start: _indicators())
    monkeypatch.setattr(backtest, "_read_market_days", lambda engine: _empty_market())
    monkeypatch.setattr(backtest, "_read_universe_flags", lambda engine, ticker: _empty_universe())


class _FakeEngine:
    class url:  # noqa: N801 - mimics sqlalchemy.Engine.url's interface
        @staticmethod
        def render_as_string(hide_password=False):
            return "postgresql://fake/db"


class TestBacktestOneTicker:
    def test_produces_one_row_per_entry_kind(self, stub_reads):
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        assert set(out["entry_kind"]) == {"touch", "touch_5m", "touch_30m", "next_open"}
        assert len(out) == 4

    def test_every_row_carries_run_id_and_config_hash(self, stub_reads):
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        assert (out["run_id"] == "run-1").all()
        assert out["config_hash"].nunique() == 1

    def test_touch_entry_resolves_a_real_exit(self, stub_reads):
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        touch = out.loc[out["entry_kind"] == "touch"].iloc[0]
        # Nothing in the fixture triggers a stop, target, band, or stoch
        # exit, so the position times out on the 5th forward bar.
        assert touch["exit_reason"] == "timeout"
        assert touch["holding_days"] == 5
        assert pd.notna(touch["gross_ret"])
        assert pd.notna(touch["net_ret"])
        assert pd.notna(touch["mfe"])

    def test_hourly_entry_kinds_are_written_unfilled_not_dropped(self, stub_reads):
        """DESIGN §5.4 / invariant 4: `hourly is None` yields a NaN entry
        price for TOUCH_5M/TOUCH_30M, but the row still gets written."""
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        for kind in ("touch_5m", "touch_30m"):
            row = out.loc[out["entry_kind"] == kind].iloc[0]
            assert pd.isna(row["entry_price"])
            assert pd.isna(row["exit_reason"])
            assert pd.isna(row["gross_ret"])

    def test_row_carries_cluster_columns(self, stub_reads):
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        row = out.iloc[0]
        assert row["is_cluster_head"] in (True, False)
        assert row["seq_in_cluster"] == 1
        assert row["days_since_head"] == 0

    def test_a_ticker_with_no_bars_returns_an_empty_frame_with_the_right_columns(self, monkeypatch):
        monkeypatch.setattr(backtest, "_read_bars", lambda *a, **k: pd.DataFrame())
        monkeypatch.setattr(backtest, "_read_indicators", lambda *a, **k: pd.DataFrame())
        monkeypatch.setattr(backtest, "_read_market_days", lambda *a, **k: _empty_market())
        monkeypatch.setattr(backtest, "_read_universe_flags", lambda *a, **k: _empty_universe())
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        assert out.empty
        assert list(out.columns) == backtest._EVENT_COLUMNS


class TestAddCofireCount:
    def test_two_tickers_same_type_same_date_each_get_count_two(self):
        events = pd.DataFrame(
            [
                {"ticker": "AAA", "signal_date": date(2026, 1, 5), "signal_type": "bb_lower_touch"},
                {"ticker": "BBB", "signal_date": date(2026, 1, 5), "signal_type": "bb_lower_touch"},
            ]
        )
        out = backtest.add_cofire_count(events)
        assert list(out["cofire_count"]) == [2, 2]

    def test_entry_kind_fanout_does_not_inflate_the_count(self):
        """One ticker's own four entry-kind rows for the same event must not
        make it look like four separate co-firing tickers."""
        events = pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "signal_date": date(2026, 1, 5),
                    "signal_type": "bb_lower_touch",
                    "entry_kind": kind,
                }
                for kind in ("touch", "touch_5m", "touch_30m", "next_open")
            ]
            + [
                {
                    "ticker": "BBB",
                    "signal_date": date(2026, 1, 5),
                    "signal_type": "bb_lower_touch",
                    "entry_kind": "touch",
                }
            ]
        )
        out = backtest.add_cofire_count(events)
        assert list(out["cofire_count"]) == [2, 2, 2, 2, 2]

    def test_a_lone_signal_gets_count_one(self):
        events = pd.DataFrame(
            [{"ticker": "AAA", "signal_date": date(2026, 1, 5), "signal_type": "bb_lower_touch"}]
        )
        out = backtest.add_cofire_count(events)
        assert list(out["cofire_count"]) == [1]

    def test_empty_frame_stays_empty_and_gains_the_column(self):
        out = backtest.add_cofire_count(backtest._empty_events_frame())
        assert out.empty
        assert "cofire_count" in out.columns

    def test_does_not_mutate_the_input(self):
        events = pd.DataFrame(
            [{"ticker": "AAA", "signal_date": date(2026, 1, 5), "signal_type": "bb_lower_touch"}]
        )
        backtest.add_cofire_count(events)
        assert "cofire_count" not in events.columns


class TestRunBacktestDispatchAndWrite:
    def test_writes_events_with_the_owned_column_list(self, stub_reads, monkeypatch):
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
            return len(data)

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)

        report = backtest.run_backtest([TICKER], Config(), "run-1", engine=_FakeEngine())

        assert len(calls) == 1
        assert calls[0]["table_name"] == "events"
        assert calls[0]["conflict_cols"] == [
            "config_hash",
            "ticker",
            "signal_date",
            "signal_type",
            "entry_kind",
        ]
        assert calls[0]["update_columns"] == backtest._RUN_BACKTEST_UPDATE_COLUMNS
        assert report.rows_written == 4
        assert report.tickers == [TICKER]

    def test_tickers_are_sorted_before_dispatch(self, stub_reads, monkeypatch):
        seen: list[str] = []

        def fake_worker(ticker, config, run_id, database_url):
            seen.append(ticker)
            return backtest._empty_events_frame()

        monkeypatch.setattr(backtest, "_backtest_one_ticker", fake_worker)
        monkeypatch.setattr(backtest.db_io, "upsert", lambda *a, **k: 0)

        backtest.run_backtest(["ZZZ", "AAA", "MMM"], Config(), "run-1", engine=_FakeEngine())

        assert seen == ["AAA", "MMM", "ZZZ"]

    def test_collected_frame_is_sorted_before_write(self, monkeypatch):
        # Two tickers, deliberately handed back out of (ticker, signal_date,
        # entry_kind) order — as `as_completed` legitimately could under
        # `max_workers > 1` — must still be written sorted (ADR 060).
        def fake_worker(ticker, config, run_id, database_url):
            if ticker == "ZZZ":
                return pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "config_hash": "c",
                            "ticker": "ZZZ",
                            "signal_date": date(2026, 1, 6),
                            "signal_type": "bb_lower_touch",
                            "entry_kind": "touch",
                        }
                    ]
                )
            return pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "config_hash": "c",
                        "ticker": "AAA",
                        "signal_date": date(2026, 1, 5),
                        "signal_type": "bb_lower_touch",
                        "entry_kind": "touch",
                    }
                ]
            )

        monkeypatch.setattr(backtest, "_backtest_one_ticker", fake_worker)
        captured: dict = {}

        def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
            captured["data"] = data
            return len(data)

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)

        backtest.run_backtest(["ZZZ", "AAA"], Config(), "run-1", engine=_FakeEngine())

        written = captured["data"]
        assert list(written["ticker"]) == ["AAA", "ZZZ"]

    def test_no_events_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(
            backtest, "_backtest_one_ticker", lambda *a, **k: backtest._empty_events_frame()
        )
        called = {"upsert": False}

        def fake_upsert(*a, **k):
            called["upsert"] = True
            return 0

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)

        report = backtest.run_backtest([TICKER], Config(), "run-1", engine=_FakeEngine())

        assert called["upsert"] is False
        assert report.rows_written == 0
        assert report.tickers == []
