"""`_rows_on_day`: the harness's hot loop, and the equivalence that lets it
be fast.

**Why it exists.** `_hourly_bar_for_entry` is called once per priced hourly
event, and the original selection ran `.dt.date` across the ticker's entire
hourly frame every time — a Python `date` object per row, then a full scan,
then a sort. Cost was `events x hourly_rows_per_ticker`.

Event counts are flat; hourly rows are not. The feed starts 2024-08-06 and
nightly adds ~40k rows a night, so the rescanned frame grows daily. Measured
harness runs track that and not the event count: 35m42s, then 55m32s, then
71m10s, then 74m58s, against a 1.5% change in events, at a steady 98% of
theoretical CPU across 8 workers throughout.

These tests pin the **equivalence** rather than the speed: the fast path and
the original expression must select the same rows, including at the day
boundaries where an off-by-one would silently drop the first or last bar of
a session.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from capitalscan.research.harness import _rows_on_day


def _original(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    """The expression this replaced, kept as the oracle."""
    picked: pd.DataFrame = frame.loc[frame["ts"].dt.date == day]
    return picked.sort_values("ts")


def _frame(stamps: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"ts": pd.to_datetime(stamps), "v": range(len(stamps))})


SESSION = [
    "2026-08-25 19:30",
    "2026-08-25 20:30",
    "2026-08-26 13:30",
    "2026-08-26 14:30",
    "2026-08-26 15:30",
    "2026-08-26 19:30",
    "2026-08-26 20:30",
    "2026-08-27 13:30",
    "2026-08-27 14:30",
]


class TestEquivalence:
    def test_it_matches_the_original_on_a_full_session(self):
        f = _frame(SESSION)
        got, want = _rows_on_day(f, date(2026, 8, 26)), _original(f, date(2026, 8, 26))
        assert got["v"].tolist() == want["v"].tolist() == [2, 3, 4, 5, 6]

    def test_it_matches_on_the_first_day(self):
        f = _frame(SESSION)
        assert _rows_on_day(f, date(2026, 8, 25))["v"].tolist() == [0, 1]

    def test_it_matches_on_the_last_day(self):
        f = _frame(SESSION)
        assert _rows_on_day(f, date(2026, 8, 27))["v"].tolist() == [7, 8]

    def test_a_day_with_no_rows_is_empty_not_an_error(self):
        f = _frame(SESSION)
        assert _rows_on_day(f, date(2026, 8, 24)).empty

    def test_a_day_after_the_last_row_is_empty(self):
        f = _frame(SESSION)
        assert _rows_on_day(f, date(2030, 1, 1)).empty

    def test_an_empty_frame_is_empty(self):
        assert _rows_on_day(_frame([]), date(2026, 8, 26)).empty


class TestBoundaries:
    """An off-by-one here drops the open or the close of a session, which
    is exactly the bar a touch fill is most likely to be priced from."""

    def test_midnight_exactly_belongs_to_its_own_day(self):
        f = _frame(["2026-08-26 00:00", "2026-08-26 23:59"])
        assert _rows_on_day(f, date(2026, 8, 26))["v"].tolist() == [0, 1]

    def test_the_next_midnight_is_excluded(self):
        f = _frame(["2026-08-26 23:59", "2026-08-27 00:00"])
        assert _rows_on_day(f, date(2026, 8, 26))["v"].tolist() == [0]
        assert _rows_on_day(f, date(2026, 8, 27))["v"].tolist() == [1]


class TestUnsortedFallback:
    """The fast path needs ascending `ts`. `run_harness` guarantees that
    when it builds `hourly_by_ticker`, but the guard must hold for any
    other caller rather than silently returning a wrong slice."""

    def test_an_unsorted_frame_still_returns_the_right_rows(self):
        f = _frame(["2026-08-27 13:30", "2026-08-26 14:30", "2026-08-26 13:30"])
        got = _rows_on_day(f, date(2026, 8, 26))
        assert sorted(got["v"].tolist()) == [1, 2]

    def test_the_unsorted_path_agrees_with_the_original(self):
        f = _frame(["2026-08-27 13:30", "2026-08-26 14:30", "2026-08-26 13:30"])
        got = _rows_on_day(f, date(2026, 8, 26))
        want = _original(f, date(2026, 8, 26))
        assert got["v"].tolist() == want["v"].tolist()
