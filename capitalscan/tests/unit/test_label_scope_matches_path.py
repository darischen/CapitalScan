"""The label backfill must cover exactly what `path_capture` priced.

**The defect this pins, found 2026-09-23.** `cscan outcomes` had resolved
**0 predictions on four consecutive nights** while its backlog grew by ~400
a night, and every step of `nightly` reported `ok`. Nothing was broken: the
forward log waits on `peak_ret_5d`, `peak_labels` wrote it for `in_trade`
rows only, and `path_backfill` prices `(in_trade OR in_watch)`. One
predicate of difference, and 2,767 unresolved predictions sat on events
that had a complete `path`, an entry price, and no labels.

That is the shape worth guarding: two queries in different modules that
must describe the same population, with nothing connecting them. The
failure is silent by construction, because a job that is asked for a
narrower population and delivers it exactly is not failing.

**The three populations, which are not the same and must not be merged:**

    path + labels   `(in_trade OR in_watch)` -- priced and measured
    training        `in_trade` only, `features.TRADE_ONLY` on the frame
    cosmetic        neither universe (ADR 178) -- display only, no `path`

ADR 200 records why widening the middle one does not touch the first.
"""

from __future__ import annotations

from pathlib import Path

from capitalscan.research import features as feat
from capitalscan.research import path_backfill, peak_labels

POPULATION = "(in_trade OR in_watch)"


class TestTheTwoQueriesDescribeOneToPopulation:
    def test_the_label_sql_carries_the_path_population(self) -> None:
        sql = peak_labels.extremum_label_sql((5, 10), "peak")
        assert POPULATION in sql, (
            "the label backfill must cover what `path_capture` priced; "
            "narrower than that and the forward log stalls on rows whose "
            "path is already complete"
        )

    def test_both_families_carry_it(self) -> None:
        """`trough` was added by ADR 175 and shares the builder, so a
        divergence here would be a copy rather than an edit."""
        for family in ("peak", "trough"):
            assert POPULATION in peak_labels.extremum_label_sql((5,), family)

    def test_the_label_sql_still_requires_an_entry_price(self) -> None:
        """The label is a return against `entry_price`; without one there is
        nothing to measure from, and `_ENTRY_OFFSET_SQL` has no anchor."""
        assert "entry_price IS NOT NULL" in peak_labels.extremum_label_sql((5,), "peak")

    def test_the_label_sql_stays_scoped_to_one_config(self) -> None:
        """User's decision, 2026-08-09: superseded generations are history,
        not something a query reads. Widening the population must not
        quietly widen the generation too."""
        assert "config_hash = :config_hash" in peak_labels.extremum_label_sql((5,), "peak")

    def test_path_backfill_still_uses_the_same_population(self) -> None:
        """If path capture ever narrows or widens, this test fails with the
        label test still passing, which names which side moved."""
        src = Path(path_backfill.__file__).read_text(encoding="utf-8")
        assert POPULATION in src


class TestTrainingIsPinnedSomewhereElse:
    """Why the widening above is not a model change.

    ADR 183 declined it on the reasoning that it "moves the training
    population". It does not: the frame carries its own filter, so which
    rows hold labels and which rows train are two separate questions.
    Should that ever stop being true, this is the test that says so.
    """

    def test_the_training_frame_pins_in_trade_by_itself(self) -> None:
        assert feat.TRADE_ONLY == "AND e.in_trade"
        assert feat.TRADE_ONLY in feat.training_sql(feat._select_columns())

    def test_the_windowed_training_frame_pins_it_too(self) -> None:
        """ADR 193's expanding window is a second entry point into the same
        frame, and it would be easy to widen one and miss the other."""
        assert feat.TRADE_ONLY in feat.windowed_training_sql(feat._select_columns())

    def test_the_training_filter_is_narrower_than_the_label_filter(self) -> None:
        """The relationship that makes the widening safe, stated as itself:
        labels cover a superset, training selects a subset of it."""
        assert POPULATION not in feat.TRADE_ONLY
        assert POPULATION in peak_labels.extremum_label_sql((5,), "peak")


class TestCosmeticRowsAreStillExcluded:
    def test_neither_universe_is_not_labelled(self) -> None:
        """ADR 178's display-only rows are in neither universe. They carry no
        `path`, and this predicate excludes them regardless -- which is the
        boundary that protects the frame, not the `in_trade` narrowing that
        was removed. Measured 2026-09-08: 3,609,960 cosmetic rows took
        `path_capture` from 97 s to over an hour when a query forgot this.
        """
        sql = peak_labels.extremum_label_sql((5,), "peak")
        assert "entry_price IS NOT NULL" in sql
        assert POPULATION in sql
        # The filter is a conjunction, so a cosmetic row fails the second
        # clause whatever its entry price says.
        assert "AND (in_trade OR in_watch)" in sql
