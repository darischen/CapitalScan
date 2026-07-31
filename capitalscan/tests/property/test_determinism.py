"""Determinism — ADR 060.

Identical config in, identical output out. Deliberately **not** marked
`exit_invariant`: ADR 060 is not one of the four exit invariants TESTS.md
§3.4 names, so it runs at `ci_fast` in the slow tier rather than consuming
10,000 examples the Phase 3 gate does not ask for.

Determinism is cheap to establish and expensive to over-sample — a
nondeterminism that survives 250 generated scenarios would have to be
vanishingly rare, and the backtest engine's own determinism check
(TESTS.md §3.3, whole-frame equality across two runs) is the real guard.
This module covers the `core/` half of it.
"""

from __future__ import annotations

import dataclasses
import math

from hypothesis import given, settings

from capitalscan.core.exits import resolve_exit
from capitalscan.core.signals import breach_live, detect
from capitalscan.tests.property.test_exit_invariants import scenarios
from capitalscan.tests.property.test_signal_parity import (
    _bands_from,
    bar_and_bands,
    signal_params,
)


def _identical(a, b) -> bool:
    """Field-wise equality treating NaN as equal to NaN.

    Needed for `ExitResult`, whose `mfe`, `mae`, and `exit_price` are plain
    floats that a degenerate window could leave as NaN — and `nan != nan`,
    so plain `==` would report a deterministic function as nondeterministic.

    **Not** needed for `SignalHit` any more. `touch_level` is `float | None`
    (DESIGN §3.4), so identical hits compare equal and hash equal directly;
    the tests below use `==` on purpose, so a revert to a NaN sentinel fails
    here rather than being papered over by this helper.
    """
    if type(a) is not type(b):
        return False
    if dataclasses.is_dataclass(a):
        return all(
            _identical(getattr(a, f.name), getattr(b, f.name)) for f in dataclasses.fields(a)
        )
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_identical(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or a == b
    return bool(a == b)


@given(scenarios())
@settings(deadline=None)
def test_resolve_exit_is_deterministic(scenario):
    entry, side, bars, ind, atr, cfg = scenario
    kwargs = dict(
        entry_price=entry,
        entry_idx=0,
        side=side,
        fwd_bars=bars,
        fwd_ind=ind,
        atr_at_entry=atr,
        ep=cfg,
    )
    assert _identical(resolve_exit(**kwargs), resolve_exit(**kwargs))


@given(bar_and_bands(), signal_params())
@settings(deadline=None)
def test_detect_is_deterministic(fixture, sp):
    # Plain `==`, deliberately: `touch_level` is `float | None`, so a revert
    # to a NaN sentinel would surface here rather than hide behind
    # `_identical`.
    bar, ind = fixture
    assert detect(bar, ind, sp) == detect(bar, ind, sp)


@given(bar_and_bands(), signal_params())
@settings(deadline=None)
def test_detect_output_is_set_safe(fixture, sp):
    """Equality and hashing must agree on every generated hit.

    Under a NaN sentinel these disagree — stable hash, unequal compare — so
    a set keeps duplicates. This is the property debounce would have relied
    on had it keyed on the dataclass (DESIGN §4.7).
    """
    bar, ind = fixture
    hits = detect(bar, ind, sp)
    assert len(set(hits)) == len({(h.ticker, h.ts, h.signal_type) for h in hits})


@given(bar_and_bands(), signal_params())
@settings(deadline=None)
def test_breach_live_is_deterministic(fixture, sp):
    bar, ind = fixture
    bands = _bands_from(ind)
    price = float(bar["low"])
    assert breach_live(price, bands, sp) == breach_live(price, bands, sp)


def test_a_null_touch_level_keeps_equality_and_hashing_coherent():
    """The boundary case that drove `touch_level` to `float | None`.

    A stochastic-only hit touched no band. Under the old NaN sentinel these
    three assertions failed: `a != b` because `nan != nan`, while
    `hash(a) == hash(b)` because `hash(nan)` is stable, so `{a, b}` kept
    both. Left in place so reverting the type fails loudly here.
    """
    import pandas as pd

    from capitalscan.core.config import SignalParams

    bar = pd.Series(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ticker": "X"},
        name=pd.Timestamp("2026-07-29"),
    )
    ind = pd.Series(
        {
            "bb_lower": 1.0,
            "bb_mid": 100.0,
            "bb_upper": 500.0,
            "bb_pctb": 0.5,
            "k_full": 15.0,
            "d_full": 15.0,
            "k_fast": 15.0,
            "atr_14": 2.0,
        }
    )
    sp = SignalParams()
    (a,) = detect(bar, ind, sp)
    (b,) = detect(bar, ind, sp)

    assert a.touch_level is None  # no band was touched
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
