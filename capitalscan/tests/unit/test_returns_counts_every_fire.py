"""`research/returns.py` counts every fire, and says so structurally.

**The defect this module was built against, which it then found in its own
motivating entry.** Every strategy-return figure in `RESULTS.md` before
2026-09-01 was hand-rolled SQL. The `max_hold_days` table was written that
way twice and was wrong both times:

1. filtered `is_cluster_head`, which for that sweep returns a *different
   population per arm* (104,460 / 78,432 / 51,832) because cluster
   membership derives from `days_since_head`, which derives from
   `max_hold_days`. The arms read as non-monotonic with the splits
   disagreeing -- an artifact of comparing three populations.
2. rewritten without the filter, it then omitted `in_trade` and averaged
   `next_open` and `touch` together: n 310,749 against the correct
   163,424, and a train mean of -2.77 bp against -1.94.

So the tests below pin the three predicates that decide *which rows are
being averaged*, because that -- not the arithmetic -- is where both
errors were.
"""

from __future__ import annotations

import inspect

import pytest

from capitalscan.research import returns


class TestTheMeasuredPopulation:
    def test_in_trade_is_not_optional(self) -> None:
        """ADR 122: detection records trade-universe membership rather than
        filtering on it, so omitting this widens the population and nothing
        about the output looks wrong. It was omitted by hand once."""
        assert "in_trade" in returns.BASE_PREDICATES

    def test_unclosed_windows_are_excluded(self) -> None:
        """An event whose forward window has not closed has no return.
        Averaging over it treats absent as zero."""
        assert "net_ret IS NOT NULL" in returns.BASE_PREDICATES

    def test_entry_kind_is_scoped(self) -> None:
        """`next_open` and `touch` are different populations with different
        means (-1.94 and -0.83 bp on the live arm). Averaging them together
        answers no question anyone asked."""
        src = inspect.getsource(returns.strategy_returns)
        assert "entry_kind = :entry_kind" in src

    def test_the_default_entry_kind_matches_cell_stats(self) -> None:
        """ADR 102's measured population. A returns table on a different
        entry kind than the statistics is not comparable to them."""
        sig = inspect.signature(returns.strategy_returns)
        assert sig.parameters["entry_kind"].default == "next_open"


class TestEveryFireIsCounted:
    def test_the_filter_is_off_by_default(self) -> None:
        """The whole point. The strategy's return is the return of the
        trades it takes, and the filter drops 74% of them."""
        for fn in (returns.strategy_returns, returns.compare):
            sig = inspect.signature(fn)
            assert sig.parameters["cluster_heads_only"].default is False, (
                f"{fn.__name__} defaults to filtering cluster heads; returns must "
                f"count every fire (ADR 165)"
            )

    def test_the_filter_is_still_reachable(self) -> None:
        """Kept so a pre-2026-09-01 figure can be reproduced rather than
        merely contradicted."""
        src = inspect.getsource(returns.strategy_returns)
        assert "is_cluster_head" in src

    def test_compare_reports_n(self) -> None:
        """`n` differing across arms of a sweep that should not change the
        population is the signal that the table is not measuring what it
        claims. It has to be visible to be noticed."""
        assert "n" in inspect.getsource(returns.compare)


class TestGroupingIsClosed:
    def test_an_unknown_column_raises(self) -> None:
        """This module builds its own SQL string, so `by` cannot be free
        text. Raising also beats silently ignoring: a dropped grouping
        reports the whole population under a heading claiming otherwise.
        """
        with pytest.raises(returns.UngroupableColumn):
            returns.strategy_returns(object(), "deadbeef", by="; DROP TABLE events --")  # type: ignore[arg-type]

    def test_the_columns_results_uses_are_allowed(self) -> None:
        for column in ("split_key", "era", "side", "signal_type", "dd_bucket"):
            assert column in returns.GROUPABLE


class TestItDoesNotTouchTheStatisticalPath:
    def test_cell_stats_keeps_its_filter(self) -> None:
        """The split is the decision (ADR 165). `cell_stats` answers "is
        this cell distinguishable from its baseline", where the extra
        observations are serially dependent by construction and dropping
        the filter narrows every interval ~2x -- the direction that
        manufactures significance.
        """
        from capitalscan.research import cell_stats

        assert "is_cluster_head" in inspect.getsource(cell_stats)

    def test_benchmarks_keeps_its_filter(self) -> None:
        from capitalscan.research import benchmarks

        assert "GRID_CLUSTER_HEADS_ONLY" in inspect.getsource(benchmarks)
