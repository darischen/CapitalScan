"""The backtest must state membership, never inherit it from a column default.

`events.in_trade` is `NOT NULL DEFAULT true`. `research/backtest.py`'s row
dict did not set it, so for the whole life of the project every
backtest-inserted row has taken that default. It was correct only by
coincidence: `apply_eligibility` admitted nothing but in-trade names, so
"true" was always the right answer.

ADR 149 removes the coincidence. `apply_eligibility` now admits watched
names as well, and a row dict that omits `in_trade` would mark them
in-trade — putting them in ADR 112's population, which is the single thing
ADR 149 promises not to do. Nothing would look wrong: the rows are real, the
prices are real, and the count simply grows.

This is the same shape as the `entry_price` defect ADR 140 fixed and as the
`path_backfill` allowlist going stale under ADR 122 — a value that was true
for a reason, kept after the reason stopped holding.
"""

from __future__ import annotations

import ast
import inspect

from capitalscan.research import backtest, candidates


def _row_dict_source() -> str:
    """The `row = {...}` literal inside `_backtest_one_ticker`."""
    return inspect.getsource(backtest)


class TestMembershipIsWrittenNotDefaulted:
    def test_the_event_row_sets_in_trade(self):
        src = _row_dict_source()
        assert '"in_trade": bool(cand.get("in_trade"' in src, (
            "backtest row dict no longer sets in_trade; it would fall back to "
            "the NOT NULL DEFAULT true and mark watched names as tradeable"
        )

    def test_the_event_row_sets_in_watch(self):
        assert '"in_watch": bool(cand.get("in_watch"' in _row_dict_source()

    def test_both_are_updatable_not_insert_only(self):
        """A row `run_events` wrote first must be corrected, not left.

        Without these in `_RUN_BACKTEST_UPDATE_COLUMNS` the stored
        membership would be whichever writer reached the row first, which
        makes the population depend on job order.
        """
        assert "in_trade" in backtest._RUN_BACKTEST_UPDATE_COLUMNS
        assert "in_watch" in backtest._RUN_BACKTEST_UPDATE_COLUMNS

    def test_the_universe_read_carries_the_watch_flag(self):
        """`_read_universe_flags` feeds `core.universe.in_watch`, which
        returns False for a missing column — so omitting it here would not
        raise, it would silently empty the watch universe."""
        src = inspect.getsource(backtest._read_universe_flags)
        assert "in_watch" in src


class TestEligibilityAdmitsBothPopulations:
    def test_apply_eligibility_checks_watch_membership(self):
        src = inspect.getsource(candidates.apply_eligibility)
        assert "core_universe.in_watch" in src

    def test_it_still_rejects_names_in_neither(self):
        """The filter is widened, not removed. A name in neither population
        is still dropped with `not_in_trade`."""
        src = inspect.getsource(candidates.apply_eligibility)
        assert "if not traded and not watched:" in src
        assert '"reason": "not_in_trade"' in src

    def test_the_flags_are_attached_to_the_kept_row(self):
        """The backtest reads them off the candidate rather than re-deriving
        membership, so the two cannot disagree — the condition Ruling C4
        requires before two jobs may own one column."""
        tree = ast.parse(inspect.getsource(candidates.apply_eligibility).lstrip())
        code = ast.unparse(tree)
        assert "row['in_trade'] = traded" in code
        assert "row['in_watch'] = watched" in code
