"""Purged walk-forward CV (DESIGN §7.5, ADR 065).

Written before the implementation, per the `core/` rule.

The failure this guards is the same one ADR 065 exists for and it is
invisible in a score. An event on 2018-03-05 and one on 2018-03-07 share
overlapping forward windows, so a random K-fold puts two views of one
market move on opposite sides of the split. The model then "predicts"
validation events it has already seen the outcome of, the fold score comes
back excellent, and nothing raises.

Purge and embargo are the two halves of the repair, and they are
asymmetric on purpose: purge looks *backwards* from the fold boundary into
train, embargo looks *forwards* from it into validate.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from capitalscan.core import folds


def _days(start: date, n: int) -> list[date]:
    """A dense calendar. Real trading days are passed in by the caller;
    `core/` may not read one (invariant 1)."""
    return [start + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Fold construction
# ---------------------------------------------------------------------------


def test_walk_forward_folds_expand_the_training_window():
    """Fold k trains on everything before its validation year.

    Never a sliding window: dropping early years to keep the training span
    fixed discards the only data a thin population has.
    """
    got = folds.walk_forward_folds(2010, 2021)
    assert got[0] == folds.Fold(train_start=2010, train_end=2014, validate=2015)
    assert got[-1] == folds.Fold(train_start=2010, train_end=2020, validate=2021)


def test_there_are_seven_folds_for_2010_to_2021():
    """DESIGN §7.5's ladder, exactly."""
    assert len(folds.walk_forward_folds(2010, 2021)) == 7


def test_every_fold_validates_after_it_trains():
    """The property that makes it walk-forward. If this ever fails, the
    model is being scored on its own past."""
    for fold in folds.walk_forward_folds(2010, 2021):
        assert fold.validate > fold.train_end


def test_folds_are_contiguous_and_ordered():
    got = folds.walk_forward_folds(2010, 2021)
    years = [f.validate for f in got]
    assert years == sorted(years)
    assert years == list(range(2015, 2022))


def test_too_short_a_span_yields_no_folds():
    """Rather than one degenerate fold. A single fold is not
    cross-validation and reporting it as such overstates what was checked.
    """
    assert folds.walk_forward_folds(2010, 2011) == ()


def test_the_first_validation_year_is_configurable():
    """Five years of burn-in is a default, not a law -- an ablation that
    starts later should not need a second implementation."""
    got = folds.walk_forward_folds(2010, 2021, min_train_years=8)
    assert got[0].validate == 2018


# ---------------------------------------------------------------------------
# Purge: backwards from the boundary, into train
# ---------------------------------------------------------------------------


def test_purge_drops_training_events_whose_window_reaches_the_fold():
    """A 2014-12-30 event with a 10-day forward window resolves inside
    2015. Its label is partly the thing the fold is meant to predict.
    """
    boundary = date(2015, 1, 1)
    kept = folds.purge([date(2014, 6, 1), date(2014, 12, 30)], boundary=boundary, horizon_days=10)
    assert kept == [True, False]


def test_purge_keeps_an_event_that_resolves_before_the_boundary():
    boundary = date(2015, 1, 1)
    kept = folds.purge([date(2014, 11, 1)], boundary=boundary, horizon_days=10)
    assert kept == [True]


def test_purge_uses_the_longest_horizon():
    """ADR 113 trains h=5 and h=10 off one frame. Purging at 5 would leave
    every 10-day label leaking across the boundary, and the leak would be
    in exactly half the heads.
    """
    # 2014-12-24 resolves 2014-12-29 over 5 days (clear of the boundary)
    # and 2015-01-03 over 10 (across it). The date has to sit in that gap
    # for the test to say anything -- 2014-12-27 is purged at *both*
    # horizons, which is correct and proves nothing.
    boundary = date(2015, 1, 1)
    short = folds.purge([date(2014, 12, 24)], boundary=boundary, horizon_days=5)
    long = folds.purge([date(2014, 12, 24)], boundary=boundary, horizon_days=10)
    assert short == [True]
    assert long == [False]


def test_purge_is_inclusive_at_the_boundary():
    """An event resolving exactly on the boundary date overlaps it.
    Off-by-one here is a single leaked event per fold, which no score
    shows."""
    boundary = date(2015, 1, 1)
    assert folds.purge([date(2014, 12, 22)], boundary=boundary, horizon_days=10) == [False]


# ---------------------------------------------------------------------------
# Embargo: forwards from the boundary, into validate
# ---------------------------------------------------------------------------


def test_embargo_drops_the_first_trading_days_of_the_fold():
    """Symmetric hazard to purge, opposite direction: a validation event on
    the fold's first day is predicted by training events whose windows end
    days earlier, and those are close enough in time to be the same move.
    """
    calendar = _days(date(2015, 1, 1), 30)
    kept = folds.embargo(
        [date(2015, 1, 1), date(2015, 1, 3), date(2015, 1, 20)],
        boundary=date(2015, 1, 1),
        calendar=calendar,
        embargo_days=5,
    )
    assert kept == [False, False, True]


def test_embargo_counts_trading_days_not_calendar_days():
    """Five sessions across a weekend is a week; five calendar days is not.
    Counting the wrong one under-embargoes every fold that starts near a
    holiday.
    """
    calendar = [
        date(2015, 1, 2),
        date(2015, 1, 5),
        date(2015, 1, 6),
        date(2015, 1, 7),
        date(2015, 1, 8),
    ]
    kept = folds.embargo(
        [date(2015, 1, 8)], boundary=date(2015, 1, 2), calendar=calendar, embargo_days=5
    )
    assert kept == [False], "the fifth session is still inside a 5-session embargo"


def test_a_zero_embargo_keeps_everything():
    """The ablation arm. Measuring what the embargo is worth should not
    need a second code path."""
    calendar = _days(date(2015, 1, 1), 10)
    kept = folds.embargo(
        [date(2015, 1, 1)], boundary=date(2015, 1, 1), calendar=calendar, embargo_days=0
    )
    assert kept == [True]


# ---------------------------------------------------------------------------
# Sample weights
# ---------------------------------------------------------------------------


def test_cluster_members_are_weighted_by_the_inverse_of_cluster_size():
    """DESIGN §7.5. Four events from one cluster are four views of one
    move; unweighted, that move counts four times."""
    got = folds.cluster_weights(["a", "a", "a", "a", "b"])
    assert got[:4] == [0.25, 0.25, 0.25, 0.25]
    assert got[4] == 1.0


def test_an_unclustered_event_weighs_one():
    """`cluster_id` is NULL on a row the backtest has not tagged (ADR 151).
    Treating NULL as one shared cluster would down-weight every untagged
    event to near zero.
    """
    got = folds.cluster_weights([None, None, "a"])
    assert got == [1.0, 1.0, 1.0]


def test_weights_sum_to_the_number_of_clusters():
    """The property that makes this a de-duplication rather than a
    rescaling: each cluster contributes one event's worth of evidence."""
    got = folds.cluster_weights(["a", "a", "b", "b", "b", "c"])
    assert sum(got) == pytest.approx(3.0)


def test_cofiring_is_not_weighted_here():
    """DESIGN §7.5 is explicit: the randomization null handles co-firing at
    the reporting layer, and double-correcting understates confidence.

    So the signature takes cluster ids and nothing else -- there is no
    parameter to pass co-fire counts through, which is the cheapest way to
    keep a future caller from adding one.
    """
    import inspect

    params = set(inspect.signature(folds.cluster_weights).parameters)
    assert params == {"cluster_ids"}
