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


class TestWatchAlertsAreDistinguishable:
    """ADR 149: a watched name says so, and says which reason admitted it.

    The poller's alert is the surface where the reader decides whether to
    act. A watched name is **not** tradeable under the four criteria, and an
    alert that renders identically to a tradeable one hides that at the one
    moment it matters.

    The reason is carried, not just the fact: `history` and `pullback` were
    admitted by different arguments, and ADR 149 stores them separately
    precisely so they can be told apart.
    """

    def test_a_watched_name_is_tagged(self):
        from capitalscan.jobs.poll import _watch_tag

        assert _watch_tag("history") == " [WATCH: history]"
        assert _watch_tag("pullback") == " [WATCH: pullback]"

    def test_a_tradeable_name_is_not(self):
        from capitalscan.jobs.poll import _watch_tag

        assert _watch_tag("trade") == ""

    def test_an_unknown_population_is_not_tagged_as_watched(self):
        """Fails toward 'tradeable' only for the label, never for the row.

        A ticker missing from the map is treated as `trade` for the tag, but
        the event row's `in_trade`/`in_watch` come from the same map with the
        same default -- so a name that somehow escaped the universe read is
        recorded the way it has always been recorded, not silently marked
        watched on the strength of a lookup miss.
        """
        from capitalscan.jobs.poll import _watch_tag

        assert _watch_tag("") == ""
        assert _watch_tag("something_else") == ""

    def test_the_subject_carries_the_tag(self):
        import inspect

        from capitalscan.jobs import poll

        src = inspect.getsource(poll._process_tick)
        assert "_watch_tag(" in src
        assert "{tag}{watch}" in src


class TestTheFrameCannotSilentlyDropAColumn:
    """`pd.DataFrame(rows, columns=_EVENT_COLUMNS)` drops unknown keys.

    This is how the first ADR 149 attempt failed on 2026-08-24. The row dict
    set `in_trade` and `in_watch`, every test asserting that passed, and the
    frame dropped both because `_EVENT_COLUMNS` had no slot — so 245k
    watched events were written with `in_trade` from the column's NOT NULL
    DEFAULT true, straight into ADR 112's population.

    Nothing raised. The code even documents the hazard three lines above the
    construction, for `path_metrics` specifically, and guards only that one
    contributor. The guard is now general.
    """

    def test_membership_has_a_slot(self):
        assert "in_trade" in backtest._EVENT_COLUMNS
        assert "in_watch" in backtest._EVENT_COLUMNS

    def test_an_unrepresentable_key_raises_rather_than_vanishing(self):
        import inspect

        src = inspect.getsource(backtest)
        assert "unknown = set(rows[0]) - set(_EVENT_COLUMNS)" in src, (
            "the general drop-guard is gone; a row-dict key with no slot in "
            "_EVENT_COLUMNS would again be dropped in silence"
        )

    # Added to the frame *after* construction by `add_cofire_count`, so it
    # never passes through the row dict and needs no slot. The only one --
    # writing this down is the point, because any other name appearing here
    # is a column the job may update but can never actually write.
    _POST_PASS_COLUMNS = {"cofire_count"}

    def test_every_backtest_owned_column_is_representable(self):
        """A column the job may update but cannot put in the frame is one it
        can never actually write. `in_trade` and `in_watch` were exactly
        that for one run: listed as updatable, dropped at construction."""
        missing = (
            set(backtest._RUN_BACKTEST_UPDATE_COLUMNS)
            - set(backtest._EVENT_COLUMNS)
            - self._POST_PASS_COLUMNS
        )
        assert not missing, f"updatable but unrepresentable: {sorted(missing)}"
