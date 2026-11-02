"""What `cscan sync` ships, and what it must stop shipping.

The serving store filled on 2026-08-21 and the sync died on Neon's 512 MB
limit. Pruning the superseded config hash by hand freed 182 MB — 90% of the
events in the store belonged to a hash nothing reads.

That prune is a symptom. `cell_stats` and `benchmarks` were copied with a
bare `SELECT *`, so **every config hash the research store has ever held
ships to serving and stays there forever**. Measured locally: 2,048
cell_stats rows across 5 hashes where 512 are current, and 4,908 benchmarks
where 1,227 are. Each rebuild adds another generation.

`run_sync` never deletes — deliberately, since a bug in the cutoff
arithmetic would otherwise silently empty the served history. So narrowing
what is *selected* is the only lever that stops the growth without taking
on that risk.

**The serving store reads exactly one hash.** `serving_config` pins it and
`web/lib/db.ts` sets it on every connection (ADR 115), so rows for any other
hash cannot be reached by the site under any query it makes.
"""

from __future__ import annotations

from datetime import date

from capitalscan.jobs.sync import _tables
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH


def _sql_for(name: str) -> str:
    for table in _tables(date(2023, 8, 22), DEFAULT_CONFIG_HASH):
        if table.name == name:
            return " ".join(table.sql.split())
    raise AssertionError(f"{name} is not a synced table")


class TestConfigScopedTablesAreFiltered:
    """Anything carrying `config_hash` ships one generation, not all of them."""

    def test_cell_stats_is_scoped(self):
        assert "config_hash = :config_hash" in _sql_for("cell_stats"), (
            "cell_stats ships every config hash the research store has held, "
            "and run_sync never deletes, so they accumulate on serving forever"
        )

    def test_benchmarks_is_scoped(self):
        assert "config_hash = :config_hash" in _sql_for("benchmarks")

    def test_events_was_already_scoped(self):
        # The control: events has always carried the predicate, which is why
        # the store held two hashes rather than five.
        assert "config_hash = :config_hash" in _sql_for("events")


class TestTheUnscopedTablesAreDeliberate:
    """Not everything should be filtered, and the reasons differ.

    `bars` and `indicators` carry no `config_hash` at all — they are inputs,
    identical whatever config reads them. They are bounded by `:cutoff`
    instead, and at 126 MB and 116 MB they are the two biggest tables on
    serving. Their lever is `ServingParams.history_years`, which is a
    product decision about how much chart history the site shows, not a
    correctness one.
    """

    def test_bars_is_bounded_by_the_cutoff_not_a_hash(self):
        sql = _sql_for("bars")
        assert ":cutoff" in sql
        assert "b.config_hash" not in sql

    def test_indicators_is_bounded_by_the_cutoff_not_a_hash(self):
        sql = _sql_for("indicators")
        assert ":cutoff" in sql
        assert "i.config_hash" not in sql

    def test_bars_and_indicators_ship_every_ticker(self):
        """No universe filter at all (2026-11-01).

        The history of this predicate is two widenings for one finding: a
        ticker with events on serving and no price series to draw them on.
        `in_trade` alone blanked every watch chart (ELPC, ULS), so on
        2026-09-03 it became `in_trade OR in_watch`. That still left
        **688 active tickers** with no bars and no indicators on serving
        on 2026-11-01, every one rendering "No indicator history" on
        `/ticker/X`, 651 of them with live-config events already there.
        ADR 178 priced those out-of-universe events so a ticker page would
        not be blank. Membership decides what enters a statistic, not
        which charts can be drawn.
        """
        for name in ("bars", "indicators"):
            sql = _sql_for(name)
            assert "universe" not in sql
            assert "in_trade" not in sql

    def test_a_ticker_missing_on_serving_ships_its_whole_history(self):
        """The incremental bound is one date for the whole table, so each
        chart table also takes the list of tickers the target lacks, and
        those ship from `:cutoff` (`_backfill_tickers`)."""
        assert ":bars_backfill" in _sql_for("bars")
        assert ":indicators_backfill" in _sql_for("indicators")

    def test_the_chart_arms_are_disjoint_and_not_an_or(self):
        """Same shape as `events` and for the same reasons: an `OR` of a
        date range and a ticker list can plan as a scan of the whole table,
        and a key in both arms would appear twice in one `COPY` chunk."""
        for name, bound in (("bars", "bars_from"), ("indicators", "indicators_from")):
            sql = _sql_for(name)
            window = f"GREATEST(:cutoff, COALESCE(CAST(:{bound} AS date), :cutoff))"
            first, second = sql.split("UNION ALL")
            assert f"ts >= {window}" in first
            assert f"ts < {window}" in second and ":cutoff" in second
            assert "_backfill" in second and "_backfill" not in first
            assert " OR " not in sql
