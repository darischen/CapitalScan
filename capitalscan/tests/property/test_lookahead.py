"""Look-ahead detection — the shift ladder (TESTS.md §3.1).

Four bounds anchored to a shuffled control, jointly stronger than the
single 0.5 threshold an earlier draft used. Measured on real bars a one-bar
shift lands near 0.59, and the cause is arithmetic rather than a bug: bands
drift roughly 0.35% of price per day while a bar's own range is roughly
1.59%, so a bar touching yesterday's band usually touches the day-before's
too.

**What this proves and what it does not.** It catches a signal that is not
reading the indicators at all, and one whose response to indicator
perturbation is structurally wrong. It does **not** distinguish correct t-1
reading from incorrect t reading: if `detect` wrongly read bar t's
indicators, shifting forward one bar would make it read t-1, giving the same
magnitude of change and roughly the same Jaccard. That limitation is
inherent to the shift design.

The t-1 guarantee itself is structural and is asserted directly in
`tests/unit/test_signature_guarantee.py` (TESTS.md §3.1b). This module and
that one are complements, not substitutes.

`detect_all` lives here rather than in `core/`. It is the t-1 alignment
loop, which the `events` job owns along with debounce, universe filtering,
and reject logging (DESIGN §4.7). Duplicating it in `core/` would create a
second place for the guard to drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import IndicatorParams, SignalParams
from capitalscan.core.indicators import compute_all
from capitalscan.core.signals import detect
from capitalscan.core.types import SignalType

IP = IndicatorParams()
SP = SignalParams()

SHIFTS = (1, 2, 5, 20)

# The shuffle is seeded. TESTS.md §3.1 writes `sample(frac=1.0)` unseeded;
# a nondeterministic control would make this test flaky against its own
# floor, and ADR 060 requires determinism anyway.
SHUFFLE_SEED = 0


def gbm_bars(n: int = 1200, seed: int = 42, mu: float = 0.0, sigma: float = 0.02):
    """A random walk with OHLC derived from each day's close.

    The question is whether detection *responds* to indicator values, which
    does not need a real price series — and a random walk has no edge to
    accidentally exploit (ADR 087).
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(mu / 252.0, sigma, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    spread = np.abs(rng.normal(0.0, sigma * 0.6, size=n)) * close
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0.0, sigma * 0.2, size=n)),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "adj_close": close,
            "volume": rng.integers(1_000_000, 5_000_000, size=n),
        },
        index=idx,
    ).clip(lower=0.01)


def detect_all(bars, indicators, detector=detect) -> set[tuple]:
    """Run a detector across a series, reading indicators at t-1.

    Returns `(date, signal_type)` pairs so two runs compare as sets.
    `detector` is injectable so the blind-detector guard can drive the same
    ladder the real signal goes through.
    """
    prior = indicators.shift(1)
    events: set[tuple] = set()
    for i in range(len(bars)):
        ind_row = prior.iloc[i]
        if pd.isna(ind_row.get("bb_lower")) or pd.isna(ind_row.get("bb_upper")):
            continue
        for hit in detector(bars.iloc[i], ind_row, SP):
            events.add((hit.ts, hit.signal_type))
    return events


def jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def run_shift_ladder(bars, indicators, detector=detect) -> dict:
    """Assert the four bounds. Raises AssertionError on any violation.

    Returns the measured values so callers can report them.
    """
    base = detect_all(bars, indicators, detector)
    shifted = {k: detect_all(bars, indicators.shift(k), detector) for k in SHIFTS}
    control = detect_all(bars, indicators.sample(frac=1.0, random_state=SHUFFLE_SEED), detector)

    j = {k: jaccard(base, v) for k, v in shifted.items()}
    jc = jaccard(base, control)

    assert jc < 0.15, f"shuffled control floor: {jc}"
    assert j[1] < 0.80, f"one-bar shift barely moved: {j[1]}"
    assert j[1] > j[2] > j[5] > j[20], f"ladder not monotonic: {j}"
    assert j[5] < 0.50, f"shift-5 above the pinned threshold: {j[5]}"
    assert j[20] < 2 * jc, f"shift-20 has not converged toward the control: {j[20]} vs {jc}"

    return {"base_n": len(base), "jaccard": j, "control": jc}


@pytest.fixture(scope="module")
def series():
    bars = gbm_bars()
    return bars, compute_all(bars, IP)


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_the_fixture_produces_enough_events_to_measure(series):
    # A threshold over an empty set is meaningless, so assert the setup first.
    bars, indicators = series
    assert len(detect_all(bars, indicators)) > 50


def test_shift_ladder(series):
    bars, indicators = series
    run_shift_ladder(bars, indicators)


def test_shift_ladder_holds_across_several_seeds():
    for seed in (7, 99, 2024):
        bars = gbm_bars(seed=seed)
        indicators = compute_all(bars, IP)
        measured = run_shift_ladder(bars, indicators)
        assert measured["base_n"] > 20, f"seed {seed} produced too few events"


def test_confluence_events_move_when_the_stochastic_moves(series):
    """Confluence additionally reads %K at t-1, so it moves further and
    faster than a band touch, which depends on the bar's own extremes."""
    bars, indicators = series
    conf = {SignalType.CONFLUENCE_LOW, SignalType.CONFLUENCE_HIGH}

    def confluence_at(shift: int) -> set:
        return {e for e in detect_all(bars, indicators.shift(shift)) if e[1] in conf}

    base = confluence_at(0)
    assert len(base) > 5
    assert jaccard(base, confluence_at(1)) < 0.80
    assert jaccard(base, confluence_at(5)) < 0.50


# ---------------------------------------------------------------------------
# The guard on the guard: prove the ladder can fail
# ---------------------------------------------------------------------------


def _always_fire(bar):
    """A detector that ignores its indicators entirely."""
    from capitalscan.core.types import Side

    class _Hit:
        ts = None
        signal_type = SignalType.BB_LOWER_TOUCH
        side = Side.LONG

    hit = _Hit()
    hit.ts = bar.name.date()
    return [hit]


def test_suite_catches_a_blind_detector(series):
    """A detector ignoring indicators entirely must fail the ladder.

    Without this the bounds could be inert — passing because nothing can
    fail them rather than because the signal is correct.
    """
    bars, indicators = series
    with pytest.raises(AssertionError):
        run_shift_ladder(bars, indicators, detector=lambda bar, ind, sp: _always_fire(bar))


def test_a_blind_detector_fails_specifically_on_the_control_floor(series):
    """Naming which bound catches it, so a future edit cannot silently
    remove the one doing the work.

    Overlap is ~0.97 rather than exactly 1.0: `detect_all` skips bars whose
    t-1 row has null bands, and shuffling relocates the warmup nulls, so a
    few bars are skipped in one run and not the other. Far above the 0.15
    floor either way, which is the point.
    """
    bars, indicators = series

    def blind(bar, ind, sp):
        return _always_fire(bar)

    base = detect_all(bars, indicators, blind)
    control = detect_all(bars, indicators.sample(frac=1.0, random_state=SHUFFLE_SEED), blind)
    assert jaccard(base, control) > 0.9  # bound 1 (jc < 0.15) catches it
