"""`slot_side` derives a prediction's side from its `signal_type`.

The slot-keyed remap (design doc, 2026-09-20) resolves `event_id` through
`(config_hash, ticker, signal_date, side, entry_kind)` instead of through the
label a detector happened to emit, because the Pi's live detector and the
end-of-day pass can fill the same debounce slot with different labels
(ADR 194). `side` has to come from somewhere that cannot drift from
`core.cells`' grid, and it has to refuse rather than guess on anything it
does not recognise -- a silently wrong side links a long prediction to a
short event.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.cells import LONG_SIGNALS
from capitalscan.core.types import SignalType
from capitalscan.jobs.sync import slot_side


def _frame(signal_types: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"ticker": ["AAPL"] * len(signal_types), "signal_type": signal_types})


def test_every_signal_type_member_resolves():
    """All eight `SignalType` members produce a side, none left over.

    `core/cells.py` says `LONG_SIGNALS` and `SHORT_SIGNALS` between them
    cover every member; this pins that as behaviour of the helper the
    remap actually calls, not just a comment in `cells.py`.
    """
    members = [member.value for member in SignalType]
    result = slot_side(_frame(members))
    assert set(result["side"]) == {"long", "short"}
    assert not result["side"].isna().any()
    for signal_type, side in zip(members, result["side"], strict=True):
        expected = "long" if signal_type in LONG_SIGNALS else "short"
        assert side == expected


def test_close_confirmed_types_land_on_the_right_side():
    """The two close-confirmed types (ADR 108, ADR 144) are not a special case.

    `bear_close_above_upper` is short, `bull_close_below_lower` is long --
    same as their intraday counterparts on each side -- because side is a
    property of the family, not of whether the fill was intraday or
    close-confirmed.
    """
    result = slot_side(
        _frame(
            [
                SignalType.BEAR_CLOSE_ABOVE_UPPER.value,
                SignalType.BULL_CLOSE_BELOW_LOWER.value,
            ]
        )
    )
    assert list(result["side"]) == ["short", "long"]


def test_unknown_signal_type_raises():
    """A typo or a type not yet added to either tuple stops the job.

    Defaulting here is the one mistake that cannot be caught downstream --
    a wrong side still joins to *some* event, quietly.
    """
    with pytest.raises(ValueError, match="made_up_signal"):
        slot_side(_frame(["made_up_signal"]))


def test_does_not_mutate_its_input():
    """Project convention: return a new object, never mutate in place."""
    original = _frame([SignalType.BB_LOWER_TOUCH.value, SignalType.BB_UPPER_TOUCH.value])
    before = original.copy(deep=True)
    result = slot_side(original)
    pd.testing.assert_frame_equal(original, before)
    assert "side" not in original.columns
    assert "side" in result.columns
