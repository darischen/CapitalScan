"""Tests for core/types.py.

Enum values are a shared contract with the database: `bars.side` and
`events.side` CHECK-constrain to ('long','short'), `events.split_key` to
('train','validate','holdout'), etc. (db/migrations §001-004). A typo in
either the Python enum or the SQL CHECK would fail silently at
insert time rather than at import time unless something pins the string
values here.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from capitalscan.core.types import (
    Bands,
    Bound,
    EntryKind,
    ExitReason,
    ExitResult,
    Side,
    SignalHit,
    SignalType,
)


def test_side_values_match_db_check_constraint():
    assert {s.value for s in Side} == {"long", "short"}


def test_bound_values():
    assert {b.value for b in Bound} == {"lower", "mid", "upper"}


def test_signal_type_values():
    """These strings are stored in `events.signal_type` and read back by
    every downstream query, so the set is pinned rather than derived. A
    rename is a data migration, not an edit."""
    assert {s.value for s in SignalType} == {
        "bb_lower_touch",
        "bb_upper_touch",
        "stoch_oversold",
        "stoch_overbought",
        "confluence_low",
        "confluence_high",
        # ADR 108, the only close-confirmed member.
        "bear_close_above_upper",
    }


def test_exit_reason_values():
    assert {r.value for r in ExitReason} == {
        "timeout",
        "target",
        "stop",
        "upper_band",
        "mid_band",
        "stoch_80",
    }


def test_entry_kind_values():
    assert {k.value for k in EntryKind} == {"touch", "touch_5m", "touch_30m", "next_open"}


def test_bands_is_frozen():
    b = Bands(
        bb_lower=90.0,
        bb_mid=100.0,
        bb_upper=110.0,
        k_full=50.0,
        d_full=50.0,
        k_fast=50.0,
        atr_14=2.0,
    )
    assert dataclasses.is_dataclass(b)
    try:
        b.bb_mid = 200.0
        assert False, "Bands must be frozen"
    except dataclasses.FrozenInstanceError:
        pass


def test_signal_hit_construction():
    hit = SignalHit(
        ticker="TSM",
        ts=date(2026, 7, 29),
        signal_type=SignalType.CONFLUENCE_LOW,
        signal_types_all=(SignalType.CONFLUENCE_LOW, SignalType.BB_LOWER_TOUCH),
        signal_strength=2,
        side=Side.LONG,
        touch_level=380.83,
        pctb=0.05,
        k_full=13.5,
    )
    assert hit.side is Side.LONG
    assert hit.signal_strength == 2


def test_exit_result_construction():
    result = ExitResult(
        exit_idx=3,
        exit_date=date(2026, 8, 3),
        exit_price=395.0,
        reason=ExitReason.TARGET,
        holding_days=3,
        mfe=0.06,
        mae=-0.01,
        time_to_mfe=3,
        ambiguous=False,
    )
    assert result.reason is ExitReason.TARGET
    assert result.mfe >= 0 >= result.mae
