"""The expanding training window, and the forward log it must never reach.

**ADR 193.** The weekly refit trained on identical rows every week --
`split_key` is fixed at event creation, so 516,615 events since 2024 never
entered training and the reliability tables stayed anchored to 2022-2023.
Measured 2026-09-10: the same fit scored 25/30 on 2022-23 and **14/30** on
2026. Expanding the window took it back to 25/30 on those same 2026 rows.

Two properties carry the correctness load here and both are cheap to break
by a plausible edit:

- **the forward log is never inside train or validate**, because it is the
  only out-of-sample estimate the project has and contaminating it is
  irreversible;
- **the embargo is applied**, because a 10-day label on the last day of
  train resolves inside validate.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from capitalscan.core import folds

TODAY = date(2026, 9, 10)
START = date(2010, 3, 31)


def window(**kw):
    return folds.training_window(TODAY, START, **kw)


class TestTheForwardLogIsUntouchable:
    def test_validate_stops_before_the_forward_log(self) -> None:
        """`outcomes` scores predictions over the last few days. Calibrating
        on them would fit the tables to the rows they are later judged by."""
        w = window()
        assert w.validate_end == TODAY - timedelta(days=folds.FORWARD_LOG_DAYS)
        assert w.validate_end < TODAY

    def test_no_bound_reaches_today(self) -> None:
        w = window()
        for bound in (w.train_start, w.train_end, w.validate_start, w.validate_end):
            assert bound < TODAY

    @pytest.mark.parametrize("days", [1, 5, 10, 30])
    def test_the_carve_out_scales_with_the_setting(self, days: int) -> None:
        w = window(forward_log_days=days)
        assert w.validate_end == TODAY - timedelta(days=days)


class TestTheEmbargoIsApplied:
    def test_train_ends_before_validate_begins(self) -> None:
        """Without the gap the model reads its own validation labels."""
        w = window()
        assert w.train_end < w.validate_start

    def test_the_gap_is_the_embargo_width(self) -> None:
        w = window()
        assert (w.validate_start - w.train_end).days == folds.TRAIN_EMBARGO_DAYS

    @pytest.mark.parametrize("days", [5, 10, 21])
    def test_the_gap_follows_the_setting(self, days: int) -> None:
        w = window(embargo_days=days)
        assert (w.validate_start - w.train_end).days == days

    def test_a_zero_embargo_is_refused_rather_than_allowed(self) -> None:
        """**A zero embargo is the leak, not a configuration of it.**

        It makes `train_end == validate_start`, so the last training day's
        forward label resolves in the first validation day. The ascending
        bounds refuse it, and that refusal is the guard -- caught when this
        test was first written with `0` in the list above and the
        constructor rejected it.
        """
        with pytest.raises(ValueError, match="ascend"):
            window(embargo_days=0)

    def test_a_ten_day_label_from_the_last_train_day_lands_before_validate(self) -> None:
        """The embargo's actual job, asserted as the property rather than
        as an arithmetic identity.

        `TRAIN_EMBARGO_DAYS` is 10 because the longest forward horizon is
        10 days. A shorter embargo than the horizon would leak, and this
        fails if someone lowers one without the other.
        """
        w = window()
        longest_horizon = 10
        assert w.train_end + timedelta(days=longest_horizon) <= w.validate_start


class TestItExpandsRatherThanSlides:
    def test_the_start_does_not_move_with_today(self) -> None:
        """**The whole finding of ADR 193.** `roll7` slid the start forward
        and cut training 42%, then blamed recency for the damage."""
        early = folds.training_window(date(2024, 1, 1), START)
        late = folds.training_window(date(2026, 9, 10), START)
        assert early.train_start == late.train_start == START

    def test_a_later_run_trains_on_strictly_more(self) -> None:
        early = folds.training_window(date(2024, 1, 1), START)
        late = folds.training_window(date(2026, 9, 10), START)
        assert late.train_end > early.train_end
        assert (late.train_end - late.train_start) > (early.train_end - early.train_start)

    def test_the_window_moves_week_to_week(self) -> None:
        """It is a *weekly* refit; a window that did not move would be the
        no-op this ADR exists to end."""
        a = folds.training_window(TODAY, START)
        b = folds.training_window(TODAY + timedelta(days=7), START)
        assert b.train_end == a.train_end + timedelta(days=7)
        assert b.validate_end == a.validate_end + timedelta(days=7)


class TestTheBoundsAreOrdered:
    def test_ascending_bounds_are_enforced(self) -> None:
        with pytest.raises(ValueError, match="ascend"):
            folds.TrainingWindow(
                train_start=date(2020, 1, 1),
                train_end=date(2019, 1, 1),
                validate_start=date(2021, 1, 1),
                validate_end=date(2022, 1, 1),
            )

    def test_validate_may_not_start_inside_train(self) -> None:
        with pytest.raises(ValueError, match="ascend"):
            folds.TrainingWindow(
                train_start=date(2010, 1, 1),
                train_end=date(2025, 1, 1),
                validate_start=date(2024, 1, 1),
                validate_end=date(2026, 1, 1),
            )

    def test_a_start_after_the_boundaries_is_refused(self) -> None:
        """A caller passing a `train_start` later than the computed
        `train_end` -- a five-year start on a six-month validate, say --
        should fail rather than silently produce an empty frame."""
        with pytest.raises(ValueError, match="ascend"):
            folds.training_window(TODAY, date(2026, 9, 1))


class TestItIsPureAndReproducible:
    def test_the_same_inputs_give_the_same_window(self) -> None:
        assert folds.training_window(TODAY, START) == folds.training_window(TODAY, START)

    def test_today_is_an_argument_not_a_clock_read(self) -> None:
        """Invariant 1: `core/` performs no IO, and that includes the clock.
        A refit must be reproducible from its recorded inputs."""
        src = __import__("inspect").getsource(folds.training_window)
        assert "today.now" not in src
        assert "date.today" not in src
