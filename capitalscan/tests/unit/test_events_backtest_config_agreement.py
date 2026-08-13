"""Final-review Finding 1: `run_events` must derive `config_hash` and
`split_key` from the SAME resolved `Config` the caller resolved, not from a
hardcoded `SplitParams()` (split_key) plus a `Config(signals=sp)` that drops
every other section (config_hash).

Before this fix, `run_events` and `research.backtest.run_backtest` could
compute two different `config_hash` values for the identical resolved
config the moment any non-`signals` override was set, which breaks the
`(config_hash, ticker, signal_date, signal_type, entry_kind)` join between
the two jobs' writes to `events` (CLAUDE.md ruling 5/C4). This file proves
the fix two ways:

1. `run_events` alone: threading a `Config` with non-default `splits`
   changes both the written `config_hash` and `split_key` away from what a
   hardcoded default would have produced.
2. Cross-module: `run_events` and `run_backtest`, given the literal same
   `Config` object and matching fixture data (same ticker, same signal
   date, same bar/indicator values), must write the SAME `config_hash` and
   the SAME `split_key` on the resulting `events` row. Tested directly by
   running both pipelines and comparing their captured output, not by
   checking each in isolation and inferring agreement.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import Config, SignalParams, SplitParams
from capitalscan.jobs import compute, db_io
from capitalscan.jobs.config import config_hash as jobs_config_hash
from capitalscan.research import backtest

TICKER = "TSM"
SIGNAL_DATE = date(2026, 7, 30)
PRIOR_DATE = date(2026, 7, 29)


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _FakeEngine:
    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()

    @contextmanager
    def connect(self):  # noqa: ANN201
        yield _FakeConn()

    class url:  # noqa: N801 - mimics sqlalchemy.Engine.url's interface
        @staticmethod
        def render_as_string(hide_password=False):
            return "postgresql://fake/db"


# ---------------------------------------------------------------------------
# Shared fixture data. Both `run_events` (one bar, its t-1 indicator row) and
# `run_backtest` (a full price history so entry/exit resolution has forward
# bars to work with) are built so the SAME single signal fires at the SAME
# (ticker, signal_date): a plain `bb_lower_touch`, long, touch_level=95.0.
# `k_full=50.0` (neutral) on every bar keeps the stochastic-oversold and
# confluence conditions from also firing, and `k_cross_up/down=False` keeps
# crossover-based signals off, so exactly one signal type fires on both
# sides of this test.
# ---------------------------------------------------------------------------

_IND_ROW = {
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
    "atr_14": 0.5,
    "rv_pct_252d": 0.5,
    "dd_52w": 0.05,
    "sma_200": 90.0,
    "sma200_slope_60": 0.01,
    "vol_z_20d": 0.0,
    "days_to_earnings": 45,
}


def _events_bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": TICKER,
                "ts": pd.Timestamp(SIGNAL_DATE),
                "open": 96.0,
                "high": 96.5,
                "low": 94.0,  # breaches bb_lower=95.0
                "close": 96.0,
                "volume": 1_000_000,
            }
        ]
    )


def _events_indicators() -> pd.DataFrame:
    return pd.DataFrame([{"ticker": TICKER, "ts": pd.Timestamp(PRIOR_DATE), **_IND_ROW}])


def _backtest_bars() -> pd.DataFrame:
    # 25 business days, signal on index 5 (2026-07-30, matches `_events_bars`
    # above), plenty of forward bars for the 5-day MFE/exit window. Every
    # other bar's `low` stays clear of the band.
    dates = pd.bdate_range("2026-07-23", periods=25)
    rows = []
    for ts in dates:
        low = 94.0 if ts.date() == SIGNAL_DATE else 95.5
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


def _backtest_indicators() -> pd.DataFrame:
    dates = pd.bdate_range("2026-07-23", periods=25)
    return pd.DataFrame([{"ticker": TICKER, "ts": ts, **_IND_ROW} for ts in dates])


def _empty_market() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts", "vix_close", "spx_ret_1d"])


def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "as_of", "in_trade"])


def _empty_hourly() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["ticker", "ts", "open", "high", "low", "close", "adj_close", "volume"]
    )


@pytest.fixture()
def stub_events_reads(monkeypatch):
    monkeypatch.setattr(db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(
        compute, "_read_bars_range", lambda engine, tickers, start, end: _events_bars()
    )
    monkeypatch.setattr(
        compute, "_read_indicators_range", lambda engine, tickers, start, end: _events_indicators()
    )
    monkeypatch.setattr(compute, "_read_market_days", lambda engine, start, end: _empty_market())
    monkeypatch.setattr(compute, "_read_universe_flags", lambda engine, tickers: _empty_universe())


@pytest.fixture()
def stub_backtest_reads(monkeypatch):
    def fake_read_bars(engine, ticker, start, interval):
        return _backtest_bars() if interval == "1d" else _empty_hourly()

    monkeypatch.setattr(backtest, "_read_bars", fake_read_bars)
    monkeypatch.setattr(
        backtest, "_read_indicators", lambda engine, ticker, start: _backtest_indicators()
    )
    monkeypatch.setattr(backtest, "_read_market_days", lambda engine: _empty_market())
    monkeypatch.setattr(backtest, "_read_universe_flags", lambda engine, ticker: _empty_universe())


@pytest.fixture()
def captured_events_upsert(monkeypatch):
    """Both `compute.py` and `research/backtest.py` do `from capitalscan.jobs
    import db_io` — the identical module object, not two copies — so
    patching `db_io.upsert` here is the ONE patch point for every job under
    test, `run_events` and `run_backtest` alike. Patching it twice (once per
    module reference) would just have the second patch silently clobber the
    first's, since they are the same attribute."""
    calls: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
        calls.append({"table_name": table_name, "data": data})
        return len(data) if hasattr(data, "__len__") else 0

    monkeypatch.setattr(db_io, "upsert", fake_upsert)
    return calls


# A non-default `SplitParams`: default `validate_end` is 2023-12-31, so
# 2026-07-30 would be "holdout" under the hardcoded default the bug used.
# Pushing `train_end`/`validate_end` out to 2027 relabels the SAME date
# "train" — proof the override, not the default, decided the label.
_OVERRIDE_SPLITS = SplitParams(train_end="2026-12-31", validate_end="2027-12-31")
_OVERRIDE_SIGNALS = SignalParams(stoch_oversold=25.0)
_OVERRIDE_CONFIG = Config(signals=_OVERRIDE_SIGNALS, splits=_OVERRIDE_SPLITS)


class TestRunEventsThreadsFullConfig:
    def test_split_key_uses_the_passed_config_not_a_hardcoded_default(
        self, stub_events_reads, captured_events_upsert
    ):
        compute.run_events(
            [TICKER], SIGNAL_DATE, SIGNAL_DATE, engine=_FakeEngine(), config=_OVERRIDE_CONFIG
        )

        events_calls = [c for c in captured_events_upsert if c["table_name"] == "events"]
        assert len(events_calls) == 1
        row = events_calls[0]["data"][0]
        assert row["split_key"] == "train"

    def test_config_hash_uses_the_full_passed_config_not_just_signals(
        self, stub_events_reads, captured_events_upsert
    ):
        compute.run_events(
            [TICKER], SIGNAL_DATE, SIGNAL_DATE, engine=_FakeEngine(), config=_OVERRIDE_CONFIG
        )

        events_calls = [c for c in captured_events_upsert if c["table_name"] == "events"]
        row = events_calls[0]["data"][0]
        assert row["config_hash"] == jobs_config_hash(_OVERRIDE_CONFIG)
        # The old bug's hash: `Config(signals=sp)`, everything else default.
        # `_OVERRIDE_CONFIG` overrides `splits` too, so the old computation
        # is a distinct, wrong hash — proving the fix reads more than `signals`.
        old_buggy_hash = jobs_config_hash(Config(signals=_OVERRIDE_SIGNALS))
        assert row["config_hash"] != old_buggy_hash


class TestRunEventsBackwardCompatibility:
    def test_documented_default_hash_is_unchanged(self):
        """Guard rail named in the task: `config_hash(Config())` used to be
        pinned at `3e598c59e7d71eae`. Two Session 10 changes (2026-08-05),
        both genuine fields of `Config`, move it again (ADR 060):
        `UniverseParams.min_mcap_usd` 100e9 -> 30e9 (user's decision,
        2026-08-03) and the new `SignalParams.stoch_source` field, giving
        `1835688bf7d760ba`.

        Moved again 2026-08-13 by ADR 108's
        `SignalParams.enabled_signal_types`, which exists precisely to give
        the new signal set its own identity rather than overwriting the
        events Sessions 12 and 13 published against. New value:
        `697f3ae71428d392` — a Postgres GUC is set from this literal, and
        must not be moved until a backtest has written rows under it."""
        assert jobs_config_hash(Config()) == "697f3ae71428d392"

    def test_sp_only_caller_still_works_and_matches_config_signals_default(
        self, stub_events_reads, captured_events_upsert
    ):
        compute.run_events(
            [TICKER], SIGNAL_DATE, SIGNAL_DATE, engine=_FakeEngine(), sp=SignalParams()
        )

        events_calls = [c for c in captured_events_upsert if c["table_name"] == "events"]
        row = events_calls[0]["data"][0]
        assert row["config_hash"] == jobs_config_hash(Config())

    def test_no_args_at_all_still_matches_the_documented_default_hash(
        self, stub_events_reads, captured_events_upsert
    ):
        compute.run_events([TICKER], SIGNAL_DATE, SIGNAL_DATE, engine=_FakeEngine())

        events_calls = [c for c in captured_events_upsert if c["table_name"] == "events"]
        row = events_calls[0]["data"][0]
        assert (
            row["config_hash"] == "697f3ae71428d392"
        )  # ADR 108: enabled_signal_types field (was 1835688bf7d760ba)
        assert row["split_key"] == "holdout"  # 2026-07-30 is past the default validate_end

    def test_sp_and_config_disagreeing_raises_rather_than_silently_picking_one(
        self, stub_events_reads, captured_events_upsert
    ):
        with pytest.raises(ValueError):
            compute.run_events(
                [TICKER],
                SIGNAL_DATE,
                SIGNAL_DATE,
                engine=_FakeEngine(),
                sp=SignalParams(stoch_oversold=99.0),
                config=_OVERRIDE_CONFIG,
            )


class TestRunEventsAndRunBacktestAgreeOnTheSameConfig:
    """The property the finding asks for directly: given the SAME resolved
    `Config`, `run_events` and `run_backtest` must write the SAME
    `config_hash` and the SAME `split_key` for the SAME signal_date."""

    def test_config_hash_and_split_key_agree_across_both_jobs(
        self,
        stub_events_reads,
        stub_backtest_reads,
        captured_events_upsert,
    ):
        compute.run_events(
            [TICKER], SIGNAL_DATE, SIGNAL_DATE, engine=_FakeEngine(), config=_OVERRIDE_CONFIG
        )
        backtest.run_backtest([TICKER], _OVERRIDE_CONFIG, "run-1", engine=_FakeEngine())

        events_calls = [
            c
            for c in captured_events_upsert
            if c["table_name"] == "events" and isinstance(c["data"], list)
        ]
        backtest_calls = [
            c
            for c in captured_events_upsert
            if c["table_name"] == "events" and isinstance(c["data"], pd.DataFrame)
        ]
        events_row = events_calls[0]["data"][0]
        backtest_rows = backtest_calls[0]["data"]
        touch_row = backtest_rows.loc[backtest_rows["entry_kind"] == "touch"].iloc[0]

        assert events_row["signal_date"] == touch_row["signal_date"]
        assert events_row["config_hash"] == touch_row["config_hash"]
        assert events_row["split_key"] == touch_row["split_key"]
        # And both agree with the value computed directly off the shared
        # config, not just with each other by coincidence.
        assert events_row["config_hash"] == jobs_config_hash(_OVERRIDE_CONFIG)
        assert events_row["split_key"] == "train"
