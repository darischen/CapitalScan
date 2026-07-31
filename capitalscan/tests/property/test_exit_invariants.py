"""Exit resolver invariants, property-based (BUILD.md §3.6, TESTS.md §3.4).

Case count comes from the hypothesis profile, not from this module. Every
test here carries the `exit_invariant` marker, which is what the slow tier
selects to run at `full` — 10,000 cases each, as TESTS.md §3.4 and the
Phase 3 gate require. Profiles are registered in
`capitalscan/tests/conftest.py` (TESTS.md §9).

**MFE is not clamped at zero** (ADR 089, TESTS.md §3.4). An earlier draft
asserted `mae <= 0 <= mfe`, which contradicts DESIGN §5.6: a position that
gaps down at t+1 and never trades back above entry has genuinely negative
MFE, since `MFE = max_i (high_i - P0)/P0` and every `high_i < P0`. Clamping
would make §5.6's "`capture_ratio` stored null when MFE <= 0" clause dead
code and overstate every capture ratio. The bound asserted is `mae <= mfe`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from capitalscan.core.config import ExitParams
from capitalscan.core.exits import min_stop_distance, resolve_exit, stop_level
from capitalscan.core.returns import realized_return
from capitalscan.core.types import ExitReason, Side

# The slow tier selects on this to run at the full profile. Applied to the
# four invariants TESTS.md §3.4 names; the determinism test below is ADR 060
# rather than a §3.4 invariant and is deliberately left unmarked.
pytestmark = pytest.mark.exit_invariant

# Only the health-check suppression is fixed here. `max_examples` and
# `deadline` come from the active profile so the tier split controls cost.
SETTINGS = settings(suppress_health_check=[HealthCheck.too_slow])


def finite(min_value: float, max_value: float) -> st.SearchStrategy[float]:
    """Real prices and fractions only: no NaN, no infinity."""
    return st.floats(
        min_value=min_value, max_value=max_value, allow_nan=False, allow_infinity=False
    )


@st.composite
def ohlc_sequences(draw, min_size: int = 1, max_size: int = 5) -> pd.DataFrame:
    """Valid OHLC bars: low <= open, close <= high, per the `bars` CHECKs."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    rows = []
    for _ in range(n):
        low = draw(finite(10.0, 500.0))
        high = low + draw(finite(0.0, 60.0))
        open_ = draw(finite(low, high))
        close = draw(finite(low, high))
        rows.append((open_, high, low, close))
    idx = pd.date_range("2026-01-05", periods=n, freq="B")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


@st.composite
def exit_configs(draw) -> ExitParams:
    return ExitParams(
        max_hold_days=draw(st.integers(min_value=1, max_value=5)),
        target_pct=draw(finite(0.01, 0.10)),
        stop_mode=draw(st.sampled_from(["atr", "fixed", "none"])),
        stop_atr_k=draw(st.sampled_from([1.0, 1.5, 2.0, 2.5])),
        stop_fixed_pct=draw(finite(0.01, 0.10)),
        exit_on_upper_band=draw(st.booleans()),
        exit_on_stoch_80=draw(st.booleans()),
        exit_on_mid_band=draw(st.booleans()),
    )


@st.composite
def indicator_frames(draw, bars: pd.DataFrame) -> pd.DataFrame:
    """Indicator rows aligned to `bars`, with bands and %K in plausible ranges."""
    n = len(bars)
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())

    def _col(low_bound: float, high_bound: float):
        return draw(st.lists(finite(low_bound, high_bound), min_size=n, max_size=n))

    return pd.DataFrame(
        {
            "bb_upper": _col(lo, hi * 1.5),
            "bb_mid": _col(lo * 0.8, hi),
            "bb_lower": _col(lo * 0.5, hi),
            "k_full": _col(0.0, 100.0),
        },
        index=bars.index,
    )


@st.composite
def scenarios(draw):
    bars = draw(ohlc_sequences())
    ind = draw(indicator_frames(bars))
    cfg = draw(exit_configs())
    side = draw(st.sampled_from([Side.LONG, Side.SHORT]))
    entry = draw(finite(10.0, 500.0))
    atr = draw(finite(0.1, 25.0))
    return entry, side, bars, ind, atr, cfg


def _resolve(scenario):
    entry, side, bars, ind, atr, cfg = scenario
    result = resolve_exit(
        entry_price=entry,
        entry_idx=0,
        side=side,
        fwd_bars=bars,
        fwd_ind=ind,
        atr_at_entry=atr,
        ep=cfg,
    )
    return entry, side, bars, cfg, atr, result


# ---------------------------------------------------------------------------
# Invariant 1 — the fill happened inside the bar it is attributed to
# ---------------------------------------------------------------------------


@given(scenarios())
@SETTINGS
def test_exit_price_lies_within_its_bar(scenario):
    _, _, bars, _, _, r = _resolve(scenario)
    bar = bars.iloc[r.exit_idx]
    assert float(bar["low"]) - 1e-9 <= r.exit_price <= float(bar["high"]) + 1e-9


# ---------------------------------------------------------------------------
# Invariant 2 — the hold is bounded by the config
# ---------------------------------------------------------------------------


@given(scenarios())
@SETTINGS
def test_holding_days_within_config_bounds(scenario):
    _, _, bars, cfg, _, r = _resolve(scenario)
    assert 1 <= r.holding_days <= cfg.max_hold_days
    assert r.holding_days == r.exit_idx + 1
    assert r.exit_idx < len(bars)


# ---------------------------------------------------------------------------
# Invariant 3 — the sharp one (TESTS.md §3.4)
# ---------------------------------------------------------------------------


@given(scenarios())
@SETTINGS
def test_mfe_is_never_below_the_realized_return(scenario):
    entry, side, _, _, _, r = _resolve(scenario)
    realized = realized_return(entry, r.exit_price, side)
    # Realized return can never exceed max favorable excursion. A violation
    # means the path metrics and the exit disagree about what happened.
    assert r.mfe >= realized - 1e-9
    assert r.mae <= r.mfe + 1e-9
    assert 1 <= r.time_to_mfe <= r.holding_days


# ---------------------------------------------------------------------------
# Invariant 4 — a STOP exit landed at or beyond the stop
# ---------------------------------------------------------------------------


@given(scenarios())
@SETTINGS
def test_stop_exits_land_at_or_beyond_the_stop_level(scenario):
    entry, side, _, cfg, atr, r = _resolve(scenario)
    assume(r.reason is ExitReason.STOP)
    stop = stop_level(entry, side, atr, cfg)
    assert not np.isnan(stop)
    if side is Side.LONG:
        assert r.exit_price <= stop + 1e-9
        assert r.exit_price <= entry * (1 - min_stop_distance(entry, atr, cfg)) + 1e-9
    else:
        assert r.exit_price >= stop - 1e-9


# ---------------------------------------------------------------------------
# Determinism (ADR 060) — same inputs, same result
# ---------------------------------------------------------------------------


def test_resolve_exit_is_deterministic_on_a_worked_case():
    """Spot check. The generated determinism sweep lives in
    `test_determinism.py`, unmarked, because ADR 060 is not one of the four
    §3.4 exit invariants and does not need the full profile."""
    bars = pd.DataFrame(
        {"open": [100.0], "high": [105.0], "low": [96.0], "close": [104.0]},
        index=pd.date_range("2026-01-05", periods=1, freq="B"),
    )
    ind = pd.DataFrame(
        {"bb_upper": [200.0], "bb_mid": [150.0], "bb_lower": [1.0], "k_full": [50.0]},
        index=bars.index,
    )
    kwargs = dict(
        entry_price=100.0,
        entry_idx=0,
        side=Side.LONG,
        fwd_bars=bars,
        fwd_ind=ind,
        atr_at_entry=2.0,
        ep=ExitParams(),
    )
    assert resolve_exit(**kwargs) == resolve_exit(**kwargs)
