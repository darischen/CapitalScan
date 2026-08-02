"""Determinism gate for the backtest engine (ADR 060, Session 9 Task 9b).

TESTS.md §3.3: two runs of `_backtest_one_ticker` against identical inputs
and identical config must produce byte-identical output, ignoring `run_id`
(which the caller injects and is allowed to differ run to run — the point
is that *given* the same `run_id`, the same config, and the same data, the
engine itself introduces no variation).

No live database: same read-stubbing pattern as `test_backtest_worker.py`.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.config import Config
from capitalscan.research import backtest

TICKER = "TSM"
N_BARS = 25
SIGNAL_IDX = 5


def _bars() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=N_BARS)
    rows = []
    for i, ts in enumerate(dates):
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


def _drop_run_id(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=["run_id"]).reset_index(drop=True)


class TestWorkerDeterminism:
    def test_two_runs_are_identical_ignoring_run_id(self, stub_reads):
        first = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        second = backtest._backtest_one_ticker(TICKER, Config(), "run-2", None)

        pd.testing.assert_frame_equal(_drop_run_id(first), _drop_run_id(second), check_like=False)

    def test_run_id_is_the_only_difference(self, stub_reads):
        first = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        second = backtest._backtest_one_ticker(TICKER, Config(), "run-2", None)

        assert (first["run_id"] == "run-1").all()
        assert (second["run_id"] == "run-2").all()

    def test_no_wall_clock_read_today_is_derived_from_loaded_bars(self, stub_reads, monkeypatch):
        """`apply_eligibility`'s `today` must never fall back to its own
        `date.today()` default (CONSTRAINTS.md item 2) — this asserts the
        worker always passes an explicit value, derived from the data."""
        captured = {}
        from capitalscan.research import candidates as research_candidates

        real_apply_eligibility = research_candidates.apply_eligibility

        def spy(candidates, universe_flags, sp_splits, today=None):
            captured["today"] = today
            return real_apply_eligibility(candidates, universe_flags, sp_splits, today=today)

        monkeypatch.setattr(research_candidates, "apply_eligibility", spy)

        backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)

        assert captured["today"] is not None
        assert captured["today"] == _bars()["ts"].max().date()


class TestRunBacktestDeterminism:
    def test_two_full_runs_write_identical_frames_ignoring_run_id(self, stub_reads, monkeypatch):
        captured: list[pd.DataFrame] = []

        def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
            captured.append(data)
            return len(data)

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)

        class _FakeEngine:
            class url:  # noqa: N801
                @staticmethod
                def render_as_string(hide_password=False):
                    return "postgresql://fake/db"

        backtest.run_backtest([TICKER], Config(), "run-1", engine=_FakeEngine())
        backtest.run_backtest([TICKER], Config(), "run-2", engine=_FakeEngine())

        assert len(captured) == 2
        pd.testing.assert_frame_equal(_drop_run_id(captured[0]), _drop_run_id(captured[1]))
