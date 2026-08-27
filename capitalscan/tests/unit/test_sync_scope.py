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


def _sql_for(name: str) -> str:
    for table in _tables(date(2023, 8, 22), "a38d3ca6b58295e8"):
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

    def test_their_universe_predicate_IS_scoped_by_hash(self):
        """The rows are not hash-scoped; the membership test inside them is.

        `universe` gained `config_hash` in d4a17c93f60b, so
        `EXISTS (... u.in_trade)` unscoped would match *any* arm's
        membership and ship chart data for names the served generation does
        not consider tradeable. These assertions read `u.config_hash`
        specifically -- the pair above pins that the bar rows themselves
        stay generation-agnostic, which is the property this class is about.
        """
        for name in ("bars", "indicators"):
            assert "u.config_hash = :config_hash" in _sql_for(name)

    def test_bars_and_indicators_ship_only_tradeable_names(self):
        """Already narrow: both require `universe.in_trade`, so the store
        does not carry chart data for names the screener cannot list."""
        for name in ("bars", "indicators"):
            assert "in_trade" in _sql_for(name)
