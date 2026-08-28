"""The close-confirmed flags are attached once per ticker, not once per bar.

**Profiled 2026-08-27.** `scan_candidates` is the one detection
implementation (invariant 2) and has two callers: `run_backtest`'s compute
phase, and the harness look-ahead ladder, which runs it **six times per
chunk** (four shift levels, base, shuffled control). `cProfile` over 29,509
bar rows:

    82,667,988 function calls in 56.8s   -- 2,800 calls per bar
      pandas Series.__setitem__  20.3s   (29,389 calls, one per bar)
      pandas .iloc/.loc getitem  20.8s
      core.signals.detect         9.5s   <- the actual work

Roughly 40 of 57 seconds was pandas row access rather than detection. One
source was this loop, which wrote each close-confirmed flag into a freshly
materialised Series per bar:

    own_ind = ind_group.loc[bar_date] if bar_date in ind_group.index else None
    for field in CLOSE_CONFIRMED_FIELDS:
        value = None if own_ind is None else own_ind.get(field)
        bar[field] = False if value is None or pd.isna(value) else bool(value)

The flags are a per-date lookup joined onto the bar frame, so they can be
computed as a column once per ticker. **What must not move is the
semantics**, and there are three:

1. **A date with no indicator row of its own yields `False`**, never a
   neighbour's flag and never a raise. This is bar `t`'s own row, not
   `t-1`: ADR 108 lets exactly `CLOSE_CONFIRMED_FIELDS` cross from `t`, and
   nothing else.
2. **A null flag through warmup is `False`**, not NaN and not dropped.
   "No band yet" is not "did not fire" (invariant 4).
3. **The value reaching `detect` is a real `bool`**, because
   `core.signals._bear_close_flag` re-checks it and a NaN would read as
   truthy.

These tests pin all three against the original expression as an oracle.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from capitalscan.research.candidates import CLOSE_CONFIRMED_FIELDS, _close_confirmed_frame

FIELD = CLOSE_CONFIRMED_FIELDS[0]


def _ind(rows: list[tuple]) -> pd.DataFrame:
    """An indicator frame indexed by date, the shape `scan_candidates` builds."""
    frame = pd.DataFrame({"ts": pd.to_datetime([r[0] for r in rows]), FIELD: [r[1] for r in rows]})
    return frame.set_index(frame["ts"].dt.date)


def _original(ind_group: pd.DataFrame, bar_date: date) -> dict:
    """The per-bar expression this replaced, kept as the oracle."""
    own = ind_group.loc[bar_date] if bar_date in ind_group.index else None
    out = {}
    for field in CLOSE_CONFIRMED_FIELDS:
        value = None if own is None else own.get(field)
        out[field] = False if value is None or pd.isna(value) else bool(value)
    return out


DATES = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]


class TestEquivalence:
    @pytest.mark.parametrize("d", DATES)
    def test_it_matches_the_original_on_every_date(self, d):
        ind = _ind([(DATES[0], True), (DATES[1], False), (DATES[2], None), (DATES[3], True)])
        day = pd.Timestamp(d).date()
        got = _close_confirmed_frame(ind, [day])
        assert got.loc[day, FIELD] == _original(ind, day)[FIELD]

    def test_a_date_absent_from_the_indicator_frame_is_false(self):
        """Semantics 1. A ticker missing its own indicator row must not
        inherit a neighbour's flag, and must not raise."""
        ind = _ind([(DATES[0], True), (DATES[2], True)])
        missing = pd.Timestamp(DATES[1]).date()
        got = _close_confirmed_frame(ind, [missing])
        assert got.loc[missing, FIELD] is False or got.loc[missing, FIELD] == False  # noqa: E712
        assert got.loc[missing, FIELD] == _original(ind, missing)[FIELD]

    def test_a_null_flag_is_false_not_nan(self):
        """Semantics 2, invariant 4: 'no band yet' is not 'did not fire'."""
        ind = _ind([(DATES[0], None)])
        day = pd.Timestamp(DATES[0]).date()
        got = _close_confirmed_frame(ind, [day])
        assert got.loc[day, FIELD] == False  # noqa: E712
        assert not pd.isna(got.loc[day, FIELD])

    def test_nan_is_false_too(self):
        ind = _ind([(DATES[0], np.nan)])
        day = pd.Timestamp(DATES[0]).date()
        assert _close_confirmed_frame(ind, [day]).loc[day, FIELD] == False  # noqa: E712

    def test_the_value_is_a_real_bool(self):
        """Semantics 3. `_bear_close_flag` re-checks this and a NaN or a
        numpy float would read as truthy."""
        ind = _ind([(DATES[0], True), (DATES[1], None)])
        got = _close_confirmed_frame(ind, [pd.Timestamp(d).date() for d in DATES[:2]])
        for value in got[FIELD]:
            assert isinstance(value, (bool, np.bool_)), f"{value!r} is {type(value)}"


class TestShape:
    def test_an_empty_date_list_yields_an_empty_frame(self):
        ind = _ind([(DATES[0], True)])
        assert _close_confirmed_frame(ind, []).empty

    def test_a_column_is_produced_for_every_close_confirmed_field(self):
        ind = _ind([(DATES[0], True)])
        got = _close_confirmed_frame(ind, [pd.Timestamp(DATES[0]).date()])
        for field in CLOSE_CONFIRMED_FIELDS:
            assert field in got.columns

    def test_a_field_missing_from_the_indicator_frame_is_false(self):
        """A frame that never carried the column at all -- the `.get`
        default in the original -- must still yield False, not KeyError."""
        frame = pd.DataFrame({"ts": pd.to_datetime([DATES[0]])})
        frame = frame.set_index(frame["ts"].dt.date)
        got = _close_confirmed_frame(frame, [pd.Timestamp(DATES[0]).date()])
        assert got.loc[pd.Timestamp(DATES[0]).date(), FIELD] == False  # noqa: E712

    def test_duplicate_dates_do_not_explode_the_result(self):
        """One row per requested date, even if the caller passes repeats."""
        ind = _ind([(DATES[0], True)])
        day = pd.Timestamp(DATES[0]).date()
        assert len(_close_confirmed_frame(ind, [day, day])) == 2


class TestFlagsReachDetectAsColumns:
    """The flags must arrive on the bar Series `detect` receives.

    **Profiled 2026-08-28, after the per-ticker lookup fix.**
    `Series.__setitem__` was still 19.3s of a 37s pass, 29,389 calls -- one
    per bar -- and 16.7s of that was `_setitem_with_indexer_missing`. The
    reason is that `bear_close_above_upper` is not a column of the bar
    frame, so `bar[field] = ...` made pandas **grow the Series index** on
    every single bar rather than replace a value.

    Assigning the flags as real columns before `iterrows()` removes the
    write entirely: the Series already carries them.

    What must not change is that `detect` sees the same values. ADR 108
    allows exactly `CLOSE_CONFIRMED_FIELDS` to cross from row `t`, and
    `core.signals._close_flag` reads them off the bar -- so a column that
    is absent, misaligned, or NaN silently turns a fired bar into an unfired
    one.
    """

    def test_the_flag_columns_are_assigned_before_iterrows(self):
        """Order is the whole optimisation. Assigning after the loop starts
        would put the growth cost back."""
        import inspect

        from capitalscan.research import candidates

        src = inspect.getsource(candidates.scan_candidates)
        assign_at = src.index("_close_confirmed_frame(")
        loop_at = src.index("for _, bar in bar_group.iterrows()")
        assert assign_at < loop_at

    def test_no_per_bar_setitem_remains(self):
        """`bar[field] = ...` inside the loop is the pattern being removed."""
        import inspect
        import re

        from capitalscan.research import candidates

        src = inspect.getsource(candidates.scan_candidates)
        loop = src[src.index("for _, bar in bar_group.iterrows()") :]
        assert not re.search(r"^\s+bar\[[a-z_]+\]\s*=", loop, re.M), (
            "a per-bar assignment into the bar Series is back; that grows the index on every row"
        )

    def test_the_flags_are_aligned_positionally_with_the_bars(self):
        """`_close_confirmed_frame` is built from the bar frame's own dates
        in order, so a positional assignment is correct -- but only while
        that order is preserved. A sort between the two would misalign every
        flag by an unknown offset and never raise."""
        import inspect

        from capitalscan.research import candidates

        src = inspect.getsource(candidates.scan_candidates)
        between = src[
            src.index("_close_confirmed_frame(") : src.index("for _, bar in bar_group.iterrows()")
        ]
        assert "sort_values" not in between, (
            "bar_group is re-sorted after the flag frame is built; the "
            "positional assignment would misalign"
        )
