"""Writes to `events` that leave an unchanged row alone (2026-11-18).

Each weekly recomputed ~10.9M rows and rewrote every one, and the label
pass rewrote every labelled row twice a day, almost all to the values they
already held. A rewritten row is a dead tuple whether or not it changed:
~9 GB of `wivie`'s 22 GB `events` on 2026-11-17. These pin each guard.
"""

from __future__ import annotations

import inspect

import pytest

from capitalscan.jobs import cli, db_io
from capitalscan.research import backtest, peak_labels

H = (1, 2, 3, 5, 10)


class TestLabelPass:
    @pytest.mark.parametrize("family", ["peak", "trough"])
    def test_every_label_column_is_compared(self, family: str):
        sql = peak_labels.extremum_label_sql(H, family)
        guard = sql[sql.rindex("AND (") :]
        column = peak_labels.FAMILIES[family][2]
        for h in H:
            assert f"e.{column.format(h=h)} IS DISTINCT FROM" in guard

    @pytest.mark.parametrize("family", ["peak", "trough"])
    def test_the_guard_compares_the_value_it_would_write(self, family: str):
        # The guard must test the same expression the SET assigns, or a
        # completed window could be compared against a partial one and
        # skipped.
        sql = peak_labels.extremum_label_sql(H, family)
        column = peak_labels.FAMILIES[family][2]
        for h in H:
            value = f"CASE WHEN agg.n{h} = {h} THEN agg.pk{h} ELSE NULL END"
            assert f"{column.format(h=h)} = {value}" in sql
            assert f"e.{column.format(h=h)} IS DISTINCT FROM {value}" in sql

    def test_is_distinct_from_not_inequality(self):
        # `<>` is NULL against NULL and would never write a label into a
        # NULL slot, which is every new row.
        sql = peak_labels.extremum_label_sql(H, "peak")
        assert "<>" not in sql[sql.rindex("AND (") :]


class TestFinalizeCofire:
    def test_only_a_count_that_moved_is_written(self):
        src = inspect.getsource(backtest.finalize_cofire)
        assert "e.cofire_count IS DISTINCT FROM sub.cnt" in src


class TestBacktestWrite:
    def test_skip_compares_everything_but_run_id(self):
        assert backtest.BACKTEST_SKIP_COMPARE == ("run_id",)
        assert "run_id" in backtest._RUN_BACKTEST_UPDATE_COLUMNS

    def test_off_by_default(self):
        params = inspect.signature(backtest.run_backtest).parameters
        assert params["skip_unchanged"].default is False

    def test_the_chunked_compute_path_turns_it_on(self):
        # The weekly's path. Its harness reads by config_hash, so rows
        # keeping an older run_id are still in its input.
        src = inspect.getsource(cli._run_backtest_compute_chunked)
        assert "skip_unchanged=True" in src

    def test_the_run_id_harness_path_does_not(self):
        # `_load_events_for_run` reads a run's rows by run_id; the single
        # call that feeds it must keep stamping every row.
        src = inspect.getsource(cli)
        harness_call = src[: src.index("_load_events_for_run(engine, bt_report.run_id)")]
        last_call = harness_call[harness_call.rindex("bt_report = run_backtest(") :]
        assert "skip_unchanged" not in last_call


class TestFillScope:
    def test_default_is_the_run(self):
        where, params = db_io._fill_scope("r1", None, None)
        assert where == "e.run_id = :run_id"
        assert params == {"run_id": "r1"}

    def test_ticker_scope_reaches_rows_from_earlier_runs(self):
        where, params = db_io._fill_scope("r1", "c", ["AAA", "BBB"])
        assert "run_id" not in where
        assert params == {"config_hash": "c", "tickers": ["AAA", "BBB"]}

    def test_ticker_scope_needs_the_config(self):
        with pytest.raises(ValueError):
            db_io._fill_scope("r1", None, ["AAA"])

    @pytest.mark.parametrize("fn", ["fill_event_sector_and_mcap", "fill_event_derived_state"])
    def test_both_fills_only_touch_nulls(self, fn: str):
        src = inspect.getsource(getattr(db_io, fn))
        assert "IS NULL" in src
        assert "_fill_scope(run_id, config_hash, tickers" in src

    def test_the_alias_follows_the_query(self):
        where, _ = db_io._fill_scope("r1", "c", ["AAA"], alias="e2")
        assert where == "e2.config_hash = :config_hash AND e2.ticker = ANY(:tickers)"

    def test_mcap_writes_only_a_row_that_gains_a_value(self):
        # `SET mcap_usd = (subquery)` rewrote a row with no universe value
        # to NULL on every run.
        src = inspect.getsource(db_io.fill_event_sector_and_mcap)
        assert "s.v IS NOT NULL" in src

    def test_derived_state_writes_only_a_row_whose_values_move(self):
        src = inspect.getsource(db_io.fill_event_derived_state)
        for col in ("bb_mid", "close", "vix_pct_252d"):
            assert f"e.{col} IS DISTINCT FROM s.{col}" in src
