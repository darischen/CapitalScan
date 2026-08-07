"""Property-based tests for path store invariants (Session 10, task 10.7).

Four invariants, per `docs/sessions/session10.md` §10.7:
- No day-offset gaps in an extracted path.
- Monotonicity across thresholds and across horizons.
- Giveback non-negative by construction.
- Null semantics: null means unknown or untouched, never zero, never a
  fabricated value.

**Rewritten 2026-08-05.** The first version generated path frames with a
strategy that guaranteed contiguous offsets, `favorable >= 0`, `adverse
<= 0`, and `adverse <= terminal <= favorable`, then asserted exactly those
four facts back. Those tests passed against any implementation, including
no implementation — they described the generator. One of them
(`test_thresholds_monotonic_within_path`) had a loop body of comments and
asserted nothing at all. Two further problems came with that shape: the
sign constraint contradicts ADR 089 (MFE is deliberately unclamped, so a
position that never traded above entry has negative `favorable` and that
is load-bearing), and generating pre-shaped paths never exercised
`core.returns.path_for_event`, which is the code that actually builds
them. These tests now generate raw OHLC bars and assert against real
output.
"""

from __future__ import annotations

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from capitalscan.core.config import DEFAULT_CONFIG, ExitParams
from capitalscan.core.returns import path_for_event, realized_return
from capitalscan.core.types import Side
from capitalscan.research.path_labels import derive_labels_from_path
from capitalscan.research.path_queries import Direction, first_touch_day, touched_by

_PRICE = st.floats(min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False)


@st.composite
def ohlc_bars(draw, min_days: int = 1, max_days: int = 11):
    """Raw forward bars, the actual input `path_for_event` consumes.

    Only the OHLC ordering constraint the `bars` table itself enforces
    (`high >= low`, and close inside the range) is imposed. Nothing here
    pre-shapes the path, so a generated case is free to produce a path that
    never trades above entry — the negative-MFE case ADR 089 requires to
    stay representable.
    """
    n = draw(st.integers(min_value=min_days, max_value=max_days))
    rows = []
    for _ in range(n):
        low = draw(_PRICE)
        high = draw(st.floats(min_value=low, max_value=low * 1.5, allow_nan=False))
        close = draw(st.floats(min_value=low, max_value=high, allow_nan=False))
        rows.append({"high": high, "low": low, "close": close})
    return pd.DataFrame(rows)


@given(ohlc_bars(), _PRICE, st.sampled_from(Side))
def test_extracted_path_has_contiguous_one_based_offsets(bars, entry_price, side):
    """`path_for_event` numbers days 1..N with no gap, whatever the input
    length. A gap would silently shift every entry-anchored label reading
    `day_offset = entry_offset + horizon`."""
    path = path_for_event(entry_price, side, bars)
    assert list(path["day_offset"]) == list(range(1, len(bars) + 1))


@given(ohlc_bars(), _PRICE, st.sampled_from(Side))
def test_extracted_path_never_pads_past_the_bars_it_was_given(bars, entry_price, side):
    """Invariant 4: a short window near the end of price history yields
    fewer rows, never a filled one."""
    assert len(path_for_event(entry_price, side, bars)) == len(bars)


@given(ohlc_bars(), _PRICE, st.sampled_from(Side))
def test_terminal_sits_between_the_days_extremes(bars, entry_price, side):
    """A real constraint on real output: the close is inside the day's
    range, so the terminal mark is inside the favorable/adverse pair, for
    both sides. Sign-flipping a short incorrectly would break this."""
    path = path_for_event(entry_price, side, bars)
    for _, row in path.iterrows():
        assert row["adverse"] <= row["terminal"] + 1e-12
        assert row["terminal"] <= row["favorable"] + 1e-12


@given(ohlc_bars(min_days=1, max_days=11), _PRICE, st.sampled_from(Side))
@settings(max_examples=50)
def test_first_touch_is_monotonic_across_thresholds(bars, entry_price, side):
    """§10.5: "A tighter threshold is touched no later than a looser one in
    the same direction." Checked on both tails, over the real config's
    targets, against a path built by real extraction code."""
    path = path_for_event(entry_price, side, bars)
    targets = sorted(DEFAULT_CONFIG.stats.reach_targets)
    for direction in Direction:
        days = [
            first_touch_day(path, t, horizon=11, direction=direction, entry_offset=0)
            for t in targets
        ]
        touched = [d for d in days if d is not None]
        # Untouched entries are null, and a looser threshold untouched
        # while a tighter one is touched is the expected shape — so the
        # property is on the non-null prefix, which must be non-decreasing.
        assert touched == sorted(touched)
        # And a tighter threshold can never be untouched while a looser
        # one is touched.
        for tighter, looser in zip(days, days[1:]):
            if looser is not None:
                assert tighter is not None


@given(ohlc_bars(min_days=1, max_days=11), _PRICE, st.sampled_from(Side))
@settings(max_examples=50)
def test_touched_is_monotonic_across_horizons(bars, entry_price, side):
    """A threshold touched by day 3 is touched by day 5. Three-valued:
    `None` (window too short) may become `True` or `False` as the horizon
    grows, but `True` never becomes `False`."""
    path = path_for_event(entry_price, side, bars)
    horizons = sorted(DEFAULT_CONFIG.stats.fwd_ret_horizons)
    for direction in Direction:
        for target in DEFAULT_CONFIG.stats.reach_targets:
            flags = [touched_by(path, target, h, direction, entry_offset=0) for h in horizons]
            for earlier, later in zip(flags, flags[1:]):
                if earlier is True:
                    assert later is True


@given(ohlc_bars(min_days=1, max_days=11), _PRICE, st.sampled_from(Side))
@settings(max_examples=50)
def test_first_touch_null_exactly_when_untouched(bars, entry_price, side):
    """Null semantics, both directions: `first_touch_day` returns a day if
    and only if `touched_by` says True — and never 0, since the window
    starts at entry+1."""
    path = path_for_event(entry_price, side, bars)
    for direction in Direction:
        for target in DEFAULT_CONFIG.stats.reach_targets:
            flag = touched_by(path, target, 5, direction, entry_offset=0)
            day = first_touch_day(path, target, 5, direction, entry_offset=0)
            if flag is True:
                assert day is not None and day >= 1
            else:
                assert day is None


@given(ohlc_bars(min_days=5, max_days=11), _PRICE, st.sampled_from(Side), st.data())
@settings(max_examples=50)
def test_giveback_is_never_negative(bars, entry_price, side, data):
    """§10.5: giveback is non-negative for favorable peaks by construction,
    and a violation raises rather than writes. `derive_labels_from_path`
    raises past its rounding tolerance, so reaching the assertion below at
    all means no violation was produced.

    `exit_price` is drawn from the exit bar's own range rather than
    independently, because that is what production does: `core.exits`
    resolves an exit against the same bars this path is built from, so the
    exit price is always a price the window traded. Drawing it freely
    generates an impossible event — Hypothesis immediately found
    `entry=1.0, exit=2.0` against a window whose high never left `1.0`,
    giving `mfe=0.0` against `r_exit=1.0` — and `derive_labels_from_path`
    correctly raises on it. That branch is a real guarantee worth keeping,
    and `test_path_labels.py::test_giveback_still_raises_past_rounding_tolerance`
    is where it belongs; this test is about consistent inputs.
    """
    holding_days = data.draw(st.integers(min_value=1, max_value=5))
    exit_bar = bars.iloc[holding_days - 1]
    exit_price = data.draw(
        st.floats(
            min_value=float(exit_bar["low"]),
            max_value=float(exit_bar["high"]),
            allow_nan=False,
        )
    )
    path = path_for_event(entry_price, side, bars)
    labels = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=holding_days,
        entry_price=entry_price,
        exit_price=exit_price,
        side=side,
        max_hold_days=5,
        targets=DEFAULT_CONFIG.stats.reach_targets,
        horizons=DEFAULT_CONFIG.stats.fwd_ret_horizons,
        capture_ratio_cap=ExitParams().capture_ratio_cap,
    )
    giveback = labels["giveback"]
    if giveback is not None and giveback == giveback:  # not None, not NaN
        assert giveback >= 0.0
        # And it is the quantity it claims to be: peak minus what the exit
        # actually kept, within the same rounding tolerance the function
        # clamps at.
        expected = labels["mfe"] - realized_return(entry_price, exit_price, side)
        assert giveback == max(expected, 0.0) or abs(giveback - expected) < 1.2e-4


def test_empty_path_yields_null_labels():
    """No forward data means no label. Null, never a fallback value."""
    empty_path = pd.DataFrame(columns=["day_offset", "favorable", "adverse", "terminal"])

    labels = derive_labels_from_path(
        path=empty_path,
        entry_offset=0,
        holding_days=None,
        entry_price=100.0,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=ExitParams().capture_ratio_cap,
    )

    assert labels.get("mfe") is None or (
        isinstance(labels["mfe"], float) and labels["mfe"] != labels["mfe"]
    ), "mfe must be null on empty path"
    assert labels.get("mae") is None or (
        isinstance(labels["mae"], float) and labels["mae"] != labels["mae"]
    ), "mae must be null on empty path"
    assert labels.get("time_to_mfe") is None, "time_to_mfe must be null on empty path"
    assert labels.get("capture_ratio") is None, "capture_ratio must be null on empty path"


def test_unfilled_entry_yields_null_labels():
    """An unfilled entry (`entry_price` NaN) means the position never
    traded and has no labels. Invariant 4 forbids filling the gap."""
    path_frame = pd.DataFrame(
        {
            "day_offset": [1],
            "favorable": [0.01],
            "adverse": [-0.01],
            "terminal": [0.005],
        }
    )

    labels = derive_labels_from_path(
        path=path_frame,
        entry_offset=0,
        holding_days=None,
        entry_price=float("nan"),
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
        capture_ratio_cap=ExitParams().capture_ratio_cap,
    )

    mfe = labels.get("mfe")
    assert mfe is None or (isinstance(mfe, float) and mfe != mfe), (
        "mfe must be null on unfilled entry"
    )
