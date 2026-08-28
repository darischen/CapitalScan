"""`purge_bars`: deleting a bar must delete what was computed from it.

**Why it exists.** A bar is not a leaf. `indicators` is computed per bar,
and `events` reference bars three separate ways — `signal_date` (the bar
the signal fired on), `entry_date` (the fill bar, which for `next_open` is
**t+1**, a different bar) and `exit_date`. None are foreign keys: `events`
has exactly one FK, to `runs`.

On 2026-08-27 a purge of 29,242 fabricated bars left ~300 orphaned events.
The database raised nothing. It surfaced 75 minutes later as three failing
harness checks, and the first cleanup attempt looked complete but was not,
because it matched `signal_date` only — `next_open` events whose fill bar
had been deleted survived it.

These tests pin the SQL shape rather than run a database: the ordering and
the three-column sweep are the content, and an integration test here would
truncate live tables (CLAUDE.md).
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from capitalscan.jobs import db_io

SRC = inspect.getsource(db_io.purge_bars)


class TestItSweepsEveryReference:
    @pytest.mark.parametrize("column", ["signal_date", "entry_date", "exit_date"])
    def test_each_event_date_column_is_swept(self, column):
        """The regression that cost two cleanup rounds: matching one column
        leaves the others orphaned and the result looks finished."""
        assert column in SRC, f"{column} not swept; orphans will survive the purge"

    def test_indicators_are_swept(self):
        assert "DELETE FROM indicators" in SRC

    def test_bars_are_swept(self):
        assert "DELETE FROM bars" in SRC


class TestOrdering:
    def test_the_doomed_dates_are_captured_before_the_delete(self):
        """Dependents match on the deleted bars' dates. Reading them after
        the delete returns nothing and the sweep silently does nothing."""
        select_at = SRC.index("SELECT ts FROM bars")
        delete_at = SRC.index("DELETE FROM bars")
        assert select_at < delete_at, "dates must be captured before bars are deleted"

    def test_it_runs_in_one_transaction(self):
        """A partial purge is worse than none: bars gone, events kept, and
        nothing to indicate it."""
        assert "engine.begin()" in SRC


class TestGuards:
    def test_no_dates_and_not_all_dates_is_a_no_op(self):
        """`engine` is a bare object: any connection attempt would raise,
        so this also proves the guard short-circuits before touching the
        database."""
        assert db_io.purge_bars(object(), "TSM") == {}
        assert db_io.purge_bars(object(), "TSM", []) == {}

    def test_all_dates_requires_no_date_list(self):
        """The whole-ticker form must not be reachable by accident: it is
        opt-in through a keyword-only flag, not by omitting `dates`."""
        sig = inspect.signature(db_io.purge_bars)
        assert sig.parameters["all_dates"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["all_dates"].default is False

    def test_dates_are_bound_not_interpolated(self):
        """A ticker or date reaching SQL by f-string is an injection path.
        The predicate is built from fixed fragments; values bind."""
        assert ":t" in SRC and ":dates" in SRC
        assert "ANY(:d)" in SRC


def test_it_returns_a_count_per_table():
    """The caller logs what was removed. A bare bool would make the audit
    row unwriteable."""
    ann = inspect.signature(db_io.purge_bars).return_annotation
    assert ann == "dict[str, int]"


def test_dates_parameter_accepts_a_sequence_of_date():
    sig = inspect.signature(db_io.purge_bars)
    assert "Sequence[date]" in str(sig.parameters["dates"].annotation)
    assert date.today() is not None  # the annotation names this type
