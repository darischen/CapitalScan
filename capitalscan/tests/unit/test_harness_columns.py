"""The harness's column list must match what the harness actually reads.

**Why this file exists.** `_load_events_for_config` used `SELECT *` and
pulled 85 columns to satisfy 12. On the 10.8M-row table that is 12.3 GB
resident against 2.2 GB, and building the wide frame committed ~70 GB of
address space -- enough to be killed by the Windows low-memory reaper three
times, including at `--workers 1`. → `OPERATIONS.md` 2026-09-09

Narrowing it is safe **only while the list stays correct**. A column the
harness reads and the loader stops fetching becomes a `KeyError` in the
middle of a check. That is loud, which is the saving grace, but it is loud
for whoever runs the harness next rather than for whoever narrowed it.

So the list is re-derived from `harness.py` here and compared. This is the
test that makes the optimisation reversible-by-evidence rather than a
promise.
"""

from __future__ import annotations

import pathlib
import re

from capitalscan.jobs import cli

#: Columns that reach the harness through `bars_by_ticker` **and never
#: through `events`**.
#:
#: `ticker` is deliberately NOT here even though `harness._BAR_COLUMNS`
#: contains it: it is the one name that lives on both frames, and the
#: events copy is load-bearing -- `_run_harness_parallel` splits work with
#: `events["ticker"].isin(chunk)`. Excluding it would let the loader drop a
#: column the chunker needs.
_BAR_ONLY = {"ts", "open", "high", "low", "close", "adj_close", "volume"}


def _columns_the_harness_reads() -> set[str]:
    """Every `events[...]` and `row.get(...)` name in `harness.py`.

    Deliberately crude: a regex over the source rather than an import and
    introspection. The alternative is running the harness, which needs the
    database and several gigabytes, and this only has to catch *drift* --
    a new column reference appearing without the loader learning about it.
    """
    src = (pathlib.Path(__file__).resolve().parents[2] / "research" / "harness.py").read_text(
        encoding="utf-8"
    )
    bracket = set(re.findall(r"\[[\"']([a-z_0-9]+)[\"']\]", src))
    gets = set(re.findall(r"\.get\([\"']([a-z_0-9]+)[\"']", src))
    # `_signal_date` is derived inside the harness, never selected.
    return (bracket | gets) - _BAR_ONLY - {"_signal_date"}


class TestTheLoaderFetchesWhatTheHarnessReads:
    def test_no_column_is_read_without_being_loaded(self) -> None:
        """The failure that matters: a check reads a column nobody fetched."""
        missing = _columns_the_harness_reads() - set(cli._HARNESS_EVENT_COLUMNS)
        assert not missing, (
            f"harness.py reads {sorted(missing)} but _HARNESS_EVENT_COLUMNS "
            "does not fetch them; the harness will KeyError mid-check"
        )

    def test_no_column_is_loaded_without_being_read(self) -> None:
        """The other direction, and it is only about cost.

        A column fetched and never read is 10.8M values of waste. Not a
        correctness problem, which is why this asserts separately -- if it
        ever needs relaxing, relax this one and not the test above.
        """
        unused = set(cli._HARNESS_EVENT_COLUMNS) - _columns_the_harness_reads()
        assert not unused, f"_HARNESS_EVENT_COLUMNS fetches unused {sorted(unused)}"

    def test_the_bar_only_set_stays_a_subset_of_the_harness_bar_columns(self) -> None:
        """The exclusion above must stay honest as `_BAR_COLUMNS` moves.

        `_BAR_ONLY` is `_BAR_COLUMNS` minus `ticker`. If a name is added to
        the harness's set, this fails and forces the question of whether it
        also lives on `events` -- which is exactly the judgement that got
        `ticker` wrong the first time.
        """
        from capitalscan.research import harness

        assert _BAR_ONLY == harness._BAR_COLUMNS - {"ticker"}

    def test_it_no_longer_selects_star(self) -> None:
        import inspect

        src = inspect.getsource(cli._load_events_for_config)
        assert "SELECT *" not in src
        assert "_HARNESS_EVENT_COLUMNS" in src

    def test_categoricals_are_a_subset_of_what_is_loaded(self) -> None:
        """Casting a column that was never fetched is a silent no-op."""
        assert set(cli._HARNESS_CATEGORICAL) <= set(cli._HARNESS_EVENT_COLUMNS)


class TestTheHarnessScopeIsTheUniverse:
    """ADR 187. The harness validates rows that enter a statistic.

    Measured 2026-09-09: `in_trade` (1,129,486 events) passes all five
    checks, `in_watch` (769,089) passes all five, and the full 10.8M table
    fails with entry 2, exit 7, non-overlap 328. Every violation is in the
    8.9M out-of-universe rows that ADR 178's cosmetic backfill priced.

    The scope is pinned here because widening it back would not fail
    loudly -- it would fail as 337 violations that look like an engine
    regression, and cost 26 minutes to discover.
    """

    def test_the_load_is_scoped_to_the_universe(self) -> None:
        import inspect

        src = inspect.getsource(cli._load_events_for_config)
        assert "in_trade OR in_watch" in src

    def test_watch_is_in_scope_not_just_trade(self) -> None:
        """`in_watch` rows are shown to a reader as guidance.

        A wrong entry price there is misleading rather than untidy, so they
        are validated rather than excluded with the cosmetic rows. They
        earn it by passing; if that stops being true it is a finding about
        the guidance, not a reason to narrow the scope.
        """
        import inspect

        src = inspect.getsource(cli._load_events_for_config)
        assert "in_watch" in src
        assert "AND in_trade\n" not in src, "scope narrowed to trade alone"
