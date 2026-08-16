"""`ExitParams.exit_stoch_source` — the exit's %K column is exit policy.

**Why this file exists.** `core/exits.py` read `"k_full"` as a string
literal. That was invisible for as long as `SignalParams.stoch_source` was
also `k_full`, because the two agreed by coincidence rather than by
construction. Flipping the entry to `k_fast` on 2026-08-15 made them
disagree inside one backtest with no field naming the disagreement, which
is exactly what CLAUDE.md invariant 9 describes: "a literal `80.0` in the
exit path while `stoch_overbought` is sweepable lets entry and exit
disagree inside one backtest, and the output looks fine."

The tests below therefore assert two separate things:

1. The field is *honoured* — a `k_fast` exit reads `k_fast`. Without this,
   adding the field would move `config_hash` and change nothing, which is
   the worst outcome: a config dimension that looks swept but is not.
2. The default is *`k_full`* — introducing the field must not silently
   restate every exit measured to date. ADR 092 holds exit policy
   independent of signal detection, so this default deliberately does
   **not** track `SignalParams.stoch_source`.

The two %K values are set far apart in each fixture so a test cannot pass
by reading the wrong column and landing on the same side of the threshold.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from capitalscan.core.config import Config, ExitParams
from capitalscan.core.exits import _exit_on_bar
from capitalscan.core.types import Side


@pytest.fixture
def bar():
    return pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})


def _ind(k_full: float, k_fast: float) -> pd.Series:
    """One indicator row. Bands sit far from the bar so no band exit fires
    first and preempts the close-based branch under test."""
    return pd.Series(
        {
            "k_full": k_full,
            "k_fast": k_fast,
            "bb_upper": 500.0,
            "bb_lower": 1.0,
            "bb_mid": 250.0,
        }
    )


def _exits(**kw) -> ExitParams:
    base = replace(Config().exits, exit_on_upper_band=False, exit_on_mid_band=False)
    return replace(base, **kw)


def _close_based_exit(bar, ind, side, ep):
    """`_exit_on_bar` with the stop and target pushed out of reach.

    The stochastic branch is step 3 of DESIGN §5.5's pinned order, so a
    reachable stop or target would preempt it and every assertion below
    would pass for the wrong reason. NaN stop disables the stop checks
    (`_isnan(stop)` guards them); the target sits far on the favorable side
    for each direction.
    """
    target = 1e9 if side is Side.LONG else -1e9
    return _exit_on_bar(bar, ind, ind, side, float("nan"), target, ep)


def test_the_default_reads_k_full_not_k_fast():
    """Introducing the field must not restate any exit measured to date."""
    assert Config().exits.exit_stoch_source == "k_full"


def test_a_k_full_exit_fires_on_k_full_and_ignores_k_fast(bar):
    """k_full 90 is extreme, k_fast 10 is not. A `k_full` exit must fire."""
    result = _close_based_exit(
        bar, _ind(k_full=90.0, k_fast=10.0), Side.LONG, _exits(exit_stoch_source="k_full")
    )
    assert result is not None, "k_full=90 clears exit_stoch_threshold=80 and must exit"


def test_a_k_fast_exit_fires_on_k_fast_and_ignores_k_full(bar):
    """The mirror. k_fast 90 is extreme, k_full 10 is not.

    This is the assertion that would fail against the old hardcoded
    `"k_full"`, and the reason the field is worth its hash move.
    """
    result = _close_based_exit(
        bar, _ind(k_full=10.0, k_fast=90.0), Side.LONG, _exits(exit_stoch_source="k_fast")
    )
    assert result is not None, "exit_stoch_source='k_fast' must read k_fast, not k_full"


def test_a_k_fast_exit_does_not_fire_when_only_k_full_is_extreme(bar):
    """The negative direction, which the two tests above cannot prove.

    A `k_fast` exit reading `k_full` by mistake would pass both of them if
    it happened to read *either* column; only this asserts it reads the
    named one and no other.
    """
    result = _close_based_exit(
        bar, _ind(k_full=90.0, k_fast=10.0), Side.LONG, _exits(exit_stoch_source="k_fast")
    )
    assert result is None, "k_fast=10 is not extreme; the k_full=90 must be ignored"


def test_the_short_side_reads_the_same_field(bar):
    """ADR 016: the short is not a mirror of the long, but both read one
    column. `exit_stoch_threshold_short` is 20.0, so k_fast=10 exits."""
    result = _close_based_exit(
        bar, _ind(k_full=90.0, k_fast=10.0), Side.SHORT, _exits(exit_stoch_source="k_fast")
    )
    assert result is not None


def test_a_null_stochastic_does_not_exit(bar):
    """Invariant 4's shape: a missing value is never filled or treated as
    extreme. `_isnan` guards this and the field must not bypass it."""
    result = _close_based_exit(
        bar, _ind(k_full=90.0, k_fast=float("nan")), Side.LONG, _exits(exit_stoch_source="k_fast")
    )
    assert result is None


def test_exit_on_stoch_80_false_disables_the_branch_entirely(bar):
    result = _close_based_exit(
        bar,
        _ind(k_full=90.0, k_fast=90.0),
        Side.LONG,
        _exits(exit_stoch_source="k_fast", exit_on_stoch_80=False),
    )
    assert result is None
