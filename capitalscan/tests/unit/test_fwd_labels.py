"""`research/fwd_labels.py`: the nightly NULL-only fill of `fwd_ret_{h}d`.

The statement runs against Postgres, so what is pinned here is its shape:
the properties that would produce a plausible wrong number rather than a
crash. Agreement with the backtest's stored values was measured on `wivie`
(module docstring) and is not re-run here.
"""

from __future__ import annotations

import pytest

from capitalscan.core.config import StatsParams
from capitalscan.research.fwd_labels import fwd_ret_fill_sql

HORIZONS = (1, 2, 3, 5, 10)


class TestFwdRetFillSql:
    def test_every_horizon_is_written_and_nothing_else(self) -> None:
        sql = fwd_ret_fill_sql(HORIZONS)
        for h in HORIZONS:
            assert f"fwd_ret_{h}d = COALESCE(e.fwd_ret_{h}d, calc.f{h})" in sql
        assert "fwd_ret_4d" not in sql

    def test_a_stored_value_is_never_overwritten(self) -> None:
        """The backtest is the authority: every SET goes through COALESCE."""
        sql = fwd_ret_fill_sql(HORIZONS)
        sets = sql.split("UPDATE events e SET")[1].split("FROM calc")[0]
        assert sets.count("COALESCE(e.fwd_ret_") == len(HORIZONS)
        assert sets.count("=") == len(HORIZONS)

    def test_each_horizon_reads_exactly_its_own_bar(self) -> None:
        """Completeness: bar `rn + h` or NULL, never a nearer bar."""
        sql = fwd_ret_fill_sql(HORIZONS)
        for h in HORIZONS:
            assert f"FILTER (WHERE bh.rn = b0.rn + {h}) AS f{h}" in sql

    def test_it_is_total_return_close_on_the_entry_bar(self) -> None:
        sql = fwd_ret_fill_sql(HORIZONS)
        assert "bh.adj_close / b0.adj_close - 1" in sql
        assert "b0.d = p.entry_date" in sql
        assert ".close" not in sql

    def test_only_rows_that_change_are_updated(self) -> None:
        sql = fwd_ret_fill_sql((5,))
        assert "(e.fwd_ret_5d IS NULL AND calc.f5 IS NOT NULL)" in sql

    def test_it_is_scoped_to_one_config(self) -> None:
        assert "config_hash = :config_hash" in fwd_ret_fill_sql(HORIZONS)

    def test_it_fills_the_population_predict_scores(self) -> None:
        """Same predicate as `peak_labels` (ADR 200), or `outcomes` stalls."""
        assert "AND (in_trade OR in_watch)" in fwd_ret_fill_sql(HORIZONS)

    def test_it_follows_the_configured_horizons(self) -> None:
        sql = fwd_ret_fill_sql(StatsParams().fwd_ret_horizons)
        for h in StatsParams().fwd_ret_horizons:
            assert f"fwd_ret_{h}d" in sql

    def test_no_horizons_is_refused(self) -> None:
        with pytest.raises(ValueError):
            fwd_ret_fill_sql(())
