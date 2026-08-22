"""The prior-indicator lookup is O(log n), and means exactly what it meant.

Profiled 2026-08-21 on AAPL, 5,248 bars, one detection pass: **11.3 seconds**,
of which the prior-indicator search was 1.9s and 53% went to row extraction.

`scan_candidates` indexed the indicator frame by `datetime.date` *objects*
and then, for every bar, scanned the whole index:

    prior_dates = ind_group.index[ind_group.index < bar_date]
    prior_ind = ind_group.loc[prior_dates.max()]

That is an object-dtype comparison across all 5,248 entries once per row —
27.5M comparisons per ticker per pass, and the harness runs six passes over
543 tickers. Measured 125x slower than `searchsorted` on the same data.

**The semantics must not move.** This is invariant 3's lookup: "indicators
are read at t-1, never t", and `core/signals.py` plus the events job both
depend on it. The index is already sorted (`sort_values("ts")` precedes
`set_index`), so "the last entry strictly before `bar_date`" is exactly what
a left-side `searchsorted` minus one returns. These tests pin that
equivalence rather than the speed.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def _prior_old(index: pd.Index, bar_date: date):
    """The original expression, kept as the oracle."""
    prior = index[index < bar_date]
    return None if len(prior) == 0 else prior.max()


def _prior_new(values: np.ndarray, bar_date: date):
    """The replacement: last entry strictly before `bar_date`.

    The `ignore` is numpy's stubs, not a real problem: `searchsorted` has no
    overload accepting a `date`, though an object-dtype array of dates
    compares and searches fine at runtime. The production call in
    `scan_candidates` passes mypy only because `.to_numpy()` erases to
    `Any`, so this helper is the one place the gap is visible.
    """
    pos = int(np.searchsorted(values, bar_date, side="left")) - 1  # type: ignore[call-overload]
    return None if pos < 0 else values[pos]


class TestTheReplacementIsTheSameFunction:
    def _index(self, days: list[int]) -> pd.Index:
        return pd.Index([date(2024, 1, d) for d in days])

    def test_agrees_across_a_dense_range(self):
        idx = self._index(list(range(1, 29)))
        arr = np.array(list(idx))
        for probe_day in range(1, 30):
            probe = date(2024, 1, probe_day)
            assert _prior_old(idx, probe) == _prior_new(arr, probe), probe

    def test_agrees_when_the_index_has_gaps(self):
        """Ruling C3: the lookup is by calendar date precisely so a gap in
        either frame cannot shift the pairing. Weekends and holidays make
        gaps the common case, not the exception."""
        idx = self._index([2, 3, 8, 9, 10, 16, 22])
        arr = np.array(list(idx))
        for probe_day in range(1, 29):
            probe = date(2024, 1, probe_day)
            assert _prior_old(idx, probe) == _prior_new(arr, probe), probe

    def test_a_date_present_in_the_index_still_looks_backward(self):
        """**Invariant 3, and the sharp case.**

        `bar_date` itself is usually *in* the index — the ticker has its own
        indicator row that day. The comparison is strictly `<`, so the
        lookup must return the prior entry, never the same-day one. An
        off-by-one here is look-ahead, silently.
        """
        idx = self._index([1, 2, 3, 4])
        arr = np.array(list(idx))
        probe = date(2024, 1, 3)
        assert _prior_old(idx, probe) == date(2024, 1, 2)
        assert _prior_new(arr, probe) == date(2024, 1, 2)

    def test_before_the_first_entry_yields_nothing(self):
        idx = self._index([10, 11, 12])
        arr = np.array(list(idx))
        assert _prior_old(idx, date(2024, 1, 1)) is None
        assert _prior_new(arr, date(2024, 1, 1)) is None

    def test_exactly_the_first_entry_yields_nothing(self):
        # `< first` is empty, so warmup is skipped rather than pairing a bar
        # with itself.
        idx = self._index([10, 11, 12])
        arr = np.array(list(idx))
        assert _prior_old(idx, date(2024, 1, 10)) is None
        assert _prior_new(arr, date(2024, 1, 10)) is None

    def test_after_the_last_entry_yields_the_last(self):
        idx = self._index([10, 11, 12])
        arr = np.array(list(idx))
        assert _prior_old(idx, date(2024, 2, 1)) == date(2024, 1, 12)
        assert _prior_new(arr, date(2024, 2, 1)) == date(2024, 1, 12)


class TestTheSourceUsesIt:
    def test_scan_candidates_no_longer_scans_the_whole_index(self):
        """Checked against *code*, not prose.

        The comment explaining the fix necessarily quotes the expression it
        replaced, so a naive substring search over the source matches its
        own documentation and can never pass. Strip comment lines first.
        """
        import inspect

        from capitalscan.research import candidates

        src = inspect.getsource(candidates.scan_candidates)
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        assert "searchsorted" in code, "the O(n^2) prior-date scan is back"
        assert "ind_group.index < bar_date" not in code

    def test_the_index_is_still_built_sorted(self):
        """`searchsorted` is only correct on sorted input, and the sort is
        two lines above the lookup. If that ordering is ever dropped the
        search returns a wrong neighbour with no error at all."""
        import inspect

        from capitalscan.research import candidates

        src = inspect.getsource(candidates.scan_candidates)
        assert 'sort_values("ts")' in src
