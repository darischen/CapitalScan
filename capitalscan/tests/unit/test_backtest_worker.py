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
`confluence_low`. With `stop_mode="atr"`, `stop_atr_k=1.5` (config default),
and a small constant `atr_14=0.5`, the ATR stop sits at roughly `entry -
0.75`, well below every forward bar's `low` (95.5) — a stop is placed but
never breached — and `bb_upper=999.0` stays out of reach, so the position
still times out on day 5 of the exit window — a clean, fully-resolved
TIMEOUT trade. `sma_200=90.0` against a constant `close=96.0` makes
`above_sma200` a deterministic `True` on every row. `atr_14=0.5` and
`days_to_earnings=45` (both previously `None` here) are non-null values,
not just null-tolerant placeholders, per Review Finding B (fix round 2):
`test_state_at_signal_columns_are_populated` needs a concrete, non-null
fixture value for every state column to actually catch that column being
dropped from the row dict again — a `None` fixture value can't distinguish
"correctly read as null" from "silently never read at all."
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
                "atr_14": 0.5,
                "rv_pct_252d": 0.5,
                "dd_52w": 0.05,
                "sma_200": 90.0,
                "sma200_slope_60": 0.01,
                "vol_z_20d": 0.0,
                "days_to_earnings": 45,
            }
        )
    return pd.DataFrame(rows)


def _empty_market() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts", "vix_close", "spx_ret_1d"])


def _empty_universe() -> pd.DataFrame:
    """No evaluation at all — which, since ADR 129, means **not tradeable**.

    Kept for the tests that want that case explicitly. Everything else uses
    `_universe()`: `in_trade` fails closed now, so a fixture with no rows
    produces zero events and every assertion about the output shape passes
    vacuously or fails confusingly. It failed confusingly, on fifteen tests
    at once, which is how this was found.
    """
    return pd.DataFrame(columns=["ticker", "as_of", "in_trade"])


def _universe(ticker: str = "TSM", *, in_trade: bool = True) -> pd.DataFrame:
    """One evaluation, well before any fixture bar, saying the name is in.

    Explicit because the behaviour under test is what the backtest does with
    a tradeable name, not whether it considers it tradeable.
    """
    return pd.DataFrame([{"ticker": ticker, "as_of": date(2000, 1, 1), "in_trade": in_trade}])


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
    monkeypatch.setattr(backtest, "_read_universe_flags", lambda engine, ticker: _universe(ticker))
    # The tests below call `_backtest_one_ticker(..., database_url=None)`,
    # and `None` means "resolve from `DATABASE_URL_RESEARCH`". Without this
    # stub they pass only on a machine holding a local `.env.local` and fail
    # in CI. Nothing ever connects — every `_read_*` above is stubbed — so a
    # placeholder engine is enough.
    monkeypatch.setattr(backtest.db_io, "get_engine", lambda *a, **k: _FakeEngine())


class _FakeEngine:
    class url:  # noqa: N801 - mimics sqlalchemy.Engine.url's interface
        @staticmethod
        def render_as_string(hide_password=False):
            return "postgresql://fake/db"


def _minimal_row(**overrides) -> dict:
    """A minimal `events` row dict, filled with `None` for every column this
    module's row-shape tests don't care about — used by the dispatch/sort
    and failure-handling tests below, which stub `_backtest_one_ticker`
    itself rather than exercising the real per-signal pipeline."""
    row = {col: None for col in backtest._EVENT_COLUMNS}
    row.update(overrides)
    return row


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
        monkeypatch.setattr(backtest, "_read_universe_flags", lambda *a, **k: _universe())
        # Stubs its own reads rather than taking `stub_reads`, so it needs the
        # same placeholder engine for the same reason.
        monkeypatch.setattr(backtest.db_io, "get_engine", lambda *a, **k: _FakeEngine())
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        assert out.empty
        assert list(out.columns) == backtest._EVENT_COLUMNS

    def test_today_override_replaces_the_per_ticker_derivation(self, stub_reads):
        """Controller ruling, fix round 1: an explicit `today` overrides the
        `max(bars.ts)` default. A `today` before the signal date must make
        the signal ineligible (`apply_eligibility`'s window upper bound),
        proving the override actually reaches `apply_eligibility` rather
        than being silently ignored."""
        early_today = date(2026, 1, 9)  # before the signal bar (index 5, ~2026-01-12)
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None, today=early_today)
        assert out.empty


class TestBacktestOneTickerStateAtSignal:
    """Review Finding 1: state-at-signal columns must be populated (not
    left NULL) on every entry-kind row, and must be identical across
    entry kinds for the same signal — they describe the t-1 indicator row
    the signal fired against, which does not vary by entry kind.
    """

    def test_state_at_signal_columns_are_populated(self, stub_reads):
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        row = out.iloc[0]
        assert row["bb_pctb"] == pytest.approx(0.1)
        assert row["bb_width_pct"] == pytest.approx(0.2)
        assert row["k_full"] == pytest.approx(50.0)
        assert row["d_full"] == pytest.approx(50.0)
        assert row["k_fast"] == pytest.approx(50.0)
        assert bool(row["k_cross_up"]) is False
        assert bool(row["k_cross_down"]) is False
        assert row["atr_14"] == pytest.approx(0.5)
        assert row["rv_pct_252d"] == pytest.approx(0.5)
        assert row["dd_52w"] == pytest.approx(0.05)
        assert row["sma200_slope_60"] == pytest.approx(0.01)
        assert row["vol_z_20d"] == pytest.approx(0.0)
        assert row["days_to_earnings"] == 45
        # sma_200=90.0 < close=96.0 on every fixture bar (module docstring).
        assert bool(row["above_sma200"]) is True

    def test_touch_and_next_open_siblings_carry_identical_state(self, stub_reads):
        """The sharpest form of Finding 1: two rows for the SAME signal,
        different entry kinds, must read the same t-1 state — not "touch
        has it, next_open reads NULL."""
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        touch = out.loc[out["entry_kind"] == "touch"].iloc[0]
        next_open = out.loc[out["entry_kind"] == "next_open"].iloc[0]
        state_cols = [
            "bb_pctb",
            "bb_width_pct",
            "k_full",
            "d_full",
            "k_fast",
            "k_cross_up",
            "k_cross_down",
            "atr_14",
            "rv_pct_252d",
            "dd_52w",
            "sma200_slope_60",
            "above_sma200",
            "vol_z_20d",
            "days_to_earnings",
        ]
        for col in state_cols:
            left, right = touch[col], next_open[col]
            if pd.isna(left) and pd.isna(right):
                continue
            assert left == right, (
                f"{col} differs between touch and next_open: {left!r} vs {right!r}"
            )

    def test_touch_5m_and_touch_30m_also_carry_the_state_despite_a_null_entry_price(
        self, stub_reads
    ):
        """The rows Finding 1 named explicitly: `run_events` never writes
        these entry kinds at all, so if this worker leaves the state null
        here, nothing ever fills it in."""
        out = backtest._backtest_one_ticker(TICKER, Config(), "run-1", None)
        for kind in ("touch_5m", "touch_30m"):
            row = out.loc[out["entry_kind"] == kind].iloc[0]
            assert pd.isna(row["entry_price"])  # unfilled, per DESIGN §5.4
            assert row["k_full"] == pytest.approx(50.0)  # but state is still known
            assert row["dd_52w"] == pytest.approx(0.05)


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
        assert report.failed_tickers == {}

    def test_tickers_are_sorted_before_dispatch(self, monkeypatch):
        seen: list[str] = []

        def fake_worker(ticker, config, run_id, database_url, today=None):
            seen.append(ticker)
            return backtest._empty_events_frame()

        monkeypatch.setattr(backtest, "_backtest_one_ticker", fake_worker)
        monkeypatch.setattr(backtest.db_io, "upsert", lambda *a, **k: 0)

        backtest.run_backtest(["ZZZ", "AAA", "MMM"], Config(), "run-1", engine=_FakeEngine())

        assert seen == ["AAA", "MMM", "ZZZ"]

    def test_collected_frame_is_sorted_by_ticker_signal_date_entry_kind(self, monkeypatch):
        """Review Finding 2: the original version of this test passed with
        `backtest.py`'s `sort_values` call deleted, because `max_workers=1`
        already iterates `sorted_tickers` in order and each fake ticker
        returned exactly one row — nothing in the assertion depended on the
        sort actually running.

        This version returns AAA's own two rows already reversed
        (2026-01-07 before 2026-01-05) — `sorted_tickers` iterating AAA
        before ZZZ cannot fix that on its own, only `sort_values` can. If
        `backtest.py`'s sort is deleted, `written["signal_date"]` comes back
        `[Jan 7, Jan 5, Jan 6]`, not `[Jan 5, Jan 6, Jan 7]`, and this test
        fails.
        """

        def fake_worker(ticker, config, run_id, database_url, today=None):
            if ticker == "ZZZ":
                return pd.DataFrame(
                    [_minimal_row(ticker="ZZZ", signal_date=date(2026, 1, 6), entry_kind="touch")]
                )
            return pd.DataFrame(
                [
                    _minimal_row(ticker="AAA", signal_date=date(2026, 1, 7), entry_kind="touch"),
                    _minimal_row(ticker="AAA", signal_date=date(2026, 1, 5), entry_kind="touch"),
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
        assert list(written["ticker"]) == ["AAA", "AAA", "ZZZ"]
        assert list(written["signal_date"]) == [
            date(2026, 1, 5),
            date(2026, 1, 7),
            date(2026, 1, 6),
        ]

    def test_sort_key_includes_signal_type_so_same_day_long_and_short_signals_are_deterministic(
        self, monkeypatch
    ):
        """Final-review Finding 2: the sort key `["ticker", "signal_date",
        "entry_kind"]` omits `signal_type`. One ticker firing both a
        long-side and a short-side signal on the same day yields two rows
        that share every original sort-key value and differ only in
        `signal_type` — `sort_values`'s default `kind="quicksort"` is not
        stable, so their relative order is not reproducible across runs
        without `signal_type` (or `side`) in the key.

        Returns the two rows in `stoch_overbought`-before-`bb_lower_touch`
        order (the wrong alphabetical order) so a passing test proves
        `signal_type` actually drove the sort, not incidental input order.
        """

        def fake_worker(ticker, config, run_id, database_url, today=None):
            return pd.DataFrame(
                [
                    _minimal_row(
                        ticker="AAA",
                        signal_date=date(2026, 1, 5),
                        entry_kind="touch",
                        signal_type="stoch_overbought",
                    ),
                    _minimal_row(
                        ticker="AAA",
                        signal_date=date(2026, 1, 5),
                        entry_kind="touch",
                        signal_type="bb_lower_touch",
                    ),
                ]
            )

        monkeypatch.setattr(backtest, "_backtest_one_ticker", fake_worker)
        captured: dict = {}

        def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
            captured["data"] = data
            return len(data)

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)

        backtest.run_backtest(["AAA"], Config(), "run-1", engine=_FakeEngine())

        written = captured["data"]
        assert list(written["signal_type"]) == ["bb_lower_touch", "stoch_overbought"]

    def test_entry_kind_is_sorted_alphabetically_not_declaration_order(
        self, stub_reads, monkeypatch
    ):
        """The real worker emits entry kinds in `EntryKind` declaration
        order (touch, touch_5m, touch_30m, next_open — `test_produces_one_
        row_per_entry_kind` proves this). `run_backtest` must reorder them
        alphabetically before writing. Deleting the sort makes this fail:
        the unsorted order would be `touch, touch_5m, touch_30m, next_open`.
        """
        captured: dict = {}

        def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
            captured["data"] = data
            return len(data)

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)

        backtest.run_backtest([TICKER], Config(), "run-1", engine=_FakeEngine())

        written = captured["data"]
        assert list(written["entry_kind"]) == ["next_open", "touch", "touch_30m", "touch_5m"]

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


class TestRunBacktestFullUniverseCofire:
    """Review Finding 3: `cofire_count` computed over a ticker subset must
    never silently overwrite a correct universe-wide value already in the
    database."""

    def test_full_universe_default_writes_cofire_count(self, monkeypatch):
        monkeypatch.setattr(
            backtest,
            "_backtest_one_ticker",
            lambda *a, **k: pd.DataFrame(
                [_minimal_row(ticker="AAA", signal_date=date(2026, 1, 5), entry_kind="touch")]
            ),
        )
        captured: dict = {}
        monkeypatch.setattr(
            backtest.db_io,
            "upsert",
            lambda engine, table_name, data, conflict_cols, update_columns=None: (
                captured.update(update_columns=update_columns) or len(data)
            ),
        )

        backtest.run_backtest(["AAA"], Config(), "run-1", engine=_FakeEngine())

        assert "cofire_count" in captured["update_columns"]

    def test_partial_run_excludes_cofire_count_from_the_write_and_warns(self, monkeypatch):
        monkeypatch.setattr(
            backtest,
            "_backtest_one_ticker",
            lambda *a, **k: pd.DataFrame(
                [_minimal_row(ticker="AAA", signal_date=date(2026, 1, 5), entry_kind="touch")]
            ),
        )
        captured: dict = {}
        monkeypatch.setattr(
            backtest.db_io,
            "upsert",
            lambda engine, table_name, data, conflict_cols, update_columns=None: (
                captured.update(update_columns=update_columns, data=data) or len(data)
            ),
        )

        with pytest.warns(UserWarning, match="full_universe"):
            backtest.run_backtest(
                ["AAA"], Config(), "run-1", engine=_FakeEngine(), full_universe=False
            )

        assert "cofire_count" not in captured["update_columns"]
        # Still present on the written frame itself (informational / for a
        # brand-new row's INSERT) — only excluded from the UPDATE scope.
        assert "cofire_count" in captured["data"].columns

    def test_full_universe_true_raises_no_warning(self, monkeypatch, recwarn):
        monkeypatch.setattr(
            backtest, "_backtest_one_ticker", lambda *a, **k: backtest._empty_events_frame()
        )
        monkeypatch.setattr(backtest.db_io, "upsert", lambda *a, **k: 0)

        backtest.run_backtest(["AAA"], Config(), "run-1", engine=_FakeEngine())

        assert len(recwarn) == 0


class TestRunBacktestPerTickerFailureIsolation:
    """Review Finding 4: one ticker's worker raising must not discard every
    other ticker's already-completed work."""

    def test_a_failing_ticker_does_not_block_the_others(self, monkeypatch):
        def fake_worker(ticker, config, run_id, database_url, today=None):
            if ticker == "BAD":
                raise ValueError(
                    "tag_clusters: ticker 'BAD' has candidate events but no trading_dates"
                )
            return pd.DataFrame(
                [_minimal_row(ticker=ticker, signal_date=date(2026, 1, 5), entry_kind="touch")]
            )

        monkeypatch.setattr(backtest, "_backtest_one_ticker", fake_worker)
        captured: dict = {}
        monkeypatch.setattr(
            backtest.db_io,
            "upsert",
            lambda engine, table_name, data, conflict_cols, update_columns=None: (
                captured.update(data=data) or len(data)
            ),
        )

        report = backtest.run_backtest(
            ["AAA", "BAD", "ZZZ"], Config(), "run-1", engine=_FakeEngine()
        )

        assert list(captured["data"]["ticker"]) == ["AAA", "ZZZ"]
        assert report.rows_written == 2
        assert set(report.failed_tickers) == {"BAD"}
        assert "ValueError" in report.failed_tickers["BAD"]

    def test_every_ticker_failing_raises_instead_of_returning_a_clean_report(self, monkeypatch):
        """Review Finding A, fix round 2: total failure (every dispatched
        ticker's worker raised) is a config-level fault — every worker
        resolves the identical config — and must not be reported as a
        routine empty run. Superseded `test_every_ticker_failing_writes_
        nothing_but_does_not_raise`, which codified the pre-fix behavior."""

        def always_fails(ticker, config, run_id, database_url, today=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(backtest, "_backtest_one_ticker", always_fails)
        called = {"upsert": False}
        monkeypatch.setattr(
            backtest.db_io, "upsert", lambda *a, **k: called.__setitem__("upsert", True) or 0
        )

        with pytest.raises(backtest.BacktestRunFailed) as excinfo:
            backtest.run_backtest(["AAA", "BBB"], Config(), "run-1", engine=_FakeEngine())

        assert called["upsert"] is False
        assert set(excinfo.value.failed_tickers) == {"AAA", "BBB"}
        assert "boom" in str(excinfo.value)

    def test_partial_failure_still_writes_what_succeeded_not_a_raise(self, monkeypatch):
        """The companion boundary to the total-failure case above: even one
        surviving ticker keeps `run_backtest` on the Finding 4 (non-raising)
        path — only ALL tickers failing raises `BacktestRunFailed`."""

        def fake_worker(ticker, config, run_id, database_url, today=None):
            if ticker == "BAD":
                raise RuntimeError("boom")
            return pd.DataFrame(
                [_minimal_row(ticker=ticker, signal_date=date(2026, 1, 5), entry_kind="touch")]
            )

        monkeypatch.setattr(backtest, "_backtest_one_ticker", fake_worker)
        captured: dict = {}
        monkeypatch.setattr(
            backtest.db_io,
            "upsert",
            lambda engine, table_name, data, conflict_cols, update_columns=None: (
                captured.update(data=data) or len(data)
            ),
        )

        report = backtest.run_backtest(["BAD", "OK"], Config(), "run-1", engine=_FakeEngine())

        assert list(captured["data"]["ticker"]) == ["OK"]
        assert report.rows_written == 1
        assert set(report.failed_tickers) == {"BAD"}


class TestRunBacktestUnknownPathColumn:
    """Review Finding 5: a swept `StatsParams` that produces a column name
    the fixed `events` schema has no slot for must raise, not silently drop
    the column via `pd.DataFrame(rows, columns=_EVENT_COLUMNS)`."""

    def test_an_unrecognized_reach_target_raises(self, stub_reads):
        from dataclasses import replace

        swept = replace(Config(), stats=replace(Config().stats, reach_targets=(0.02, 0.07)))
        with pytest.raises(ValueError, match="touched_7pct"):
            backtest._backtest_one_ticker(TICKER, swept, "run-1", None)
