"""`db_io.drop_unreproduced_events`: a rebuild removes the slots it did not write.

`cscan add-ticker EMBJ,SNDK` rebuilt both histories on 2026-11-22 after a
split repair. `events` upserts on its slot key, so 30 slots only the corrupt
history produced kept their rows, features and labels, and 22 labelled EMBJ
rows were in line for the weekly refit. Deleted by hand on 2026-11-28. The
SQL was checked the same day against research inside a rolled-back
transaction, with the 30 rows restored from their backup: it deleted those
30 and nothing else.
"""

from __future__ import annotations

import inspect
from datetime import date

from capitalscan.jobs import cli, db_io

SQL = db_io.DROP_UNREPRODUCED_SQL


def test_no_tickers_touches_nothing():
    """`engine` is a bare object: any attempt to connect would raise."""
    assert db_io.drop_unreproduced_events(object(), "h", [], date(2005, 1, 1), "events_x") == 0


def test_the_scope_is_one_generation_and_the_rebuilt_tickers():
    assert "e.config_hash = :config_hash" in SQL
    assert "e.ticker = ANY(:tickers)" in SQL
    assert "e.signal_date >= :since_date" in SQL


def test_a_row_this_rebuild_wrote_survives():
    """`run_events` writes `run_id` on every slot it produces."""
    assert "NOT EXISTS (SELECT 1 FROM runs r WHERE r.run_id = e.run_id" in SQL
    cutoff = "(SELECT b.started_at FROM runs b WHERE b.run_id = :rebuild_run_id)"
    assert f"r.started_at >= {cutoff}" in SQL


def test_an_unknown_rebuild_run_deletes_nothing():
    """A NULL cutoff would make every row look unreproduced."""
    assert "AND EXISTS (SELECT 1 FROM runs b WHERE b.run_id = :rebuild_run_id)" in SQL


def test_a_row_only_excluded_predictions_reference_is_deleted():
    """ADR 210: those predictions scored the corrupt row, and nothing reads them."""
    assert "p.event_id = e.id" in SQL
    assert "FROM forward_log_exclusions x" in SQL
    assert "FROM predictions r" not in SQL


def test_every_other_reference_keeps_the_row():
    for table in db_io.EVENT_REFERENCING_TABLES:
        if table != "predictions":
            assert f"NOT EXISTS (SELECT 1 FROM {table} r WHERE r.event_id = e.id)" in SQL


def test_add_ticker_cleans_up_after_the_backtest_with_the_events_run():
    source = inspect.getsource(cli.add_ticker)
    assert "events_run_id = report.run_id" in source
    assert source.index("compute.run_events(") < source.index("events_run_id = report.run_id")
    assert source.index("backtest(") < source.index("db_io.drop_unreproduced_events(")
