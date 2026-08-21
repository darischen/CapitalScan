"""Unit tests for core/signals.py.

`_breach` is the single band comparison in the repo (CLAUDE.md invariant 2),
so it gets exhaustive coverage: rounding, tolerance, NaN, and the MID guard.

`detect` gets tested for the three things that decide correctness:
band conditions read the bar's intraday extremes (ADR 005), stochastic
conditions read the t-1 indicator row (DESIGN §3.6), and concurrent
signals collapse to one hit per side with the pinned specificity ranking
(ADR 057).

The look-ahead (§3.1) and parity (§3.2) tests live in tests/property/.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import signals as sig
from capitalscan.core.config import SignalParams
from capitalscan.core.types import Bands, Bound, Side, SignalType

# `SP` inherits the live default on purpose, and `_ind(k=...)` below puts
# the extreme on whichever column that default selects. So these tests
# *follow* `stoch_source` rather than depending on it.
#
# The problem this solves: every test here used to set its extreme on
# `k_full` and leave `k_fast` neutral, then read `SignalParams()` for the
# source. The file therefore depended on `k_full` being the default, not on
# `k_full` being the column under test — and flipping that default on
# 2026-08-15 broke 21 tests at once, none of which are about defaults.
#
# Skipping them under `k_fast` was the obvious fix and the wrong one. Their
# subjects are confluence rules, the specificity ranking, `signal_types_all`
# ordering, `signal_strength` counting, debounce keys, and null handling;
# `k_full` was only ever the vehicle. Skipping would leave every one of
# those uncovered on a `k_fast` run, which is when new behaviour is least
# proven.
#
# `SP_FULL` / `SP_FAST` pin an explicit column and belong only to
# `TestStochSourceSelectsTheColumn`, which asks what `stoch_source`
# selects. That question needs fixed columns; every other test here does
# not.
SP = SignalParams()
# Agreement off on both, so `TestStochSourceSelectsTheColumn` varies exactly
# one thing. Its fixtures put the two %K values on opposite sides of the
# threshold by design, which is a large gap — leaving `require_fast_agreement`
# at its default would have those fixtures fail the agreement check and turn
# a column-selection test into an agreement test.
SP_FULL = SignalParams(stoch_source="k_full", require_fast_agreement=False)
SP_FAST = SignalParams(stoch_source="k_fast", require_fast_agreement=False)

# Which %K column `_ind(k=...)` / `_bands(k=...)` write to. Read once at
# import: `SP` is built once too, so a test reading one and a helper
# reading the other can never disagree mid-run.
ACTIVE_K = SP.stoch_source


def _bar(open_=100.0, high=101.0, low=99.0, close=100.0, ts="2026-07-29", ticker="TSM"):
    """A bar as `read_bars` returns one: `ticker` is a column, `ts` the index
    label. Matches the golden fixtures' `ticker,ts,open,high,low,close,...`
    shape, so tests exercise the real caller contract."""
    fields = {"open": open_, "high": high, "low": low, "close": close}
    if ticker is not None:
        fields["ticker"] = ticker
    return pd.Series(fields, name=pd.Timestamp(ts))


def _route_k(k, k_other, k_full, k_fast):
    """Put `k` on the column `stoch_source` selects, `k_other` on the other.

    This is what makes a test follow the active source instead of pinning
    one column. Both default to `None`, meaning "no opinion", so explicit
    `k_full=` / `k_fast=` arguments pass through untouched — which
    `TestStochSourceSelectsTheColumn` relies on, since asking what
    `stoch_source` selects requires naming both columns outright.

    `k_other` exists for the ADR 044 agreement tests. Their subject is the
    *gap* between the two %K columns, so they need the trigger on the live
    column and a disagreeing value on the other one, whichever those are.
    Writing them as `k_full=15, k_fast=90` pinned the roles and inverted
    them under a `k_fast` source: the 90 became the trigger, firing an
    overbought short beside the long band touch and returning two hits
    where the test unpacks one.

    When `k_other` is omitted it **mirrors** `k`, putting the two columns
    in exact agreement. That is required rather than convenient: with
    `require_fast_agreement` True by default (2026-08-16), leaving the
    other column at a neutral 50 while `k` is 15 gives a gap of 35 and
    blocks every confluence in the file. Eleven tests about confluence
    rules, specificity, and debounce failed that way on the flip.

    Mirroring costs nothing in coverage because every test whose subject
    *is* the gap passes `k_other` explicitly, which overrides this.
    """
    if k is None and k_other is None:
        return k_full, k_fast
    if k is None:
        k = k_full if ACTIVE_K == "k_full" else k_fast
    if k_other is None:
        k_other = k
    return (k, k_other) if ACTIVE_K == "k_full" else (k_other, k)


def _ind(
    bb_lower=95.0,
    bb_mid=100.0,
    bb_upper=105.0,
    bb_pctb=0.5,
    k=None,
    k_other=None,
    k_full=50.0,
    d_full=50.0,
    k_fast=50.0,
    atr_14=2.0,
):
    k_full, k_fast = _route_k(k, k_other, k_full, k_fast)
    return pd.Series(
        {
            "bb_lower": bb_lower,
            "bb_mid": bb_mid,
            "bb_upper": bb_upper,
            "bb_pctb": bb_pctb,
            "k_full": k_full,
            "d_full": d_full,
            "k_fast": k_fast,
            "atr_14": atr_14,
        }
    )


def _bands(**kw):
    base = dict(
        bb_lower=95.0,
        bb_mid=100.0,
        bb_upper=105.0,
        k_full=50.0,
        d_full=50.0,
        k_fast=50.0,
        atr_14=2.0,
    )
    k = kw.pop("k", None)
    k_other = kw.pop("k_other", None)
    base.update(kw)
    base["k_full"], base["k_fast"] = _route_k(k, k_other, base["k_full"], base["k_fast"])
    return Bands(**base)


# ---------------------------------------------------------------------------
# _breach — the single comparison (DESIGN §3.6)
# ---------------------------------------------------------------------------


def test_breach_lower_fires_at_or_below_level():
    assert sig._breach(94.0, 95.0, Bound.LOWER)
    assert sig._breach(95.0, 95.0, Bound.LOWER)  # "at or beyond"
    assert not sig._breach(95.01, 95.0, Bound.LOWER)


def test_breach_upper_fires_at_or_above_level():
    assert sig._breach(106.0, 105.0, Bound.UPPER)
    assert sig._breach(105.0, 105.0, Bound.UPPER)
    assert not sig._breach(104.99, 105.0, Bound.UPPER)


def test_breach_rounds_to_four_decimals_before_comparing():
    # 95.00004 rounds to 95.0, which is "at" the level and therefore a breach.
    assert sig._breach(95.00004, 95.0, Bound.LOWER)
    # 95.00006 rounds to 95.0001, which is above it.
    assert not sig._breach(95.00006, 95.0, Bound.LOWER)


def test_breach_returns_false_on_nan():
    assert not sig._breach(np.nan, 95.0, Bound.LOWER)
    assert not sig._breach(94.0, np.nan, Bound.LOWER)


def test_breach_tolerance_widens_the_trigger():
    # tol=0.01 means the lower band triggers 1% above the level.
    assert sig._breach(95.9, 95.0, Bound.LOWER, tol=0.01)
    assert not sig._breach(95.9, 95.0, Bound.LOWER, tol=0.0)
    assert sig._breach(104.0, 105.0, Bound.UPPER, tol=0.01)


def test_breach_rejects_mid_bound():
    # MID is a level, not a direction; callers must pass the direction they mean.
    with pytest.raises(ValueError):
        sig._breach(100.0, 100.0, Bound.MID)


# ---------------------------------------------------------------------------
# detect — band conditions read intraday extremes (ADR 005)
# ---------------------------------------------------------------------------


def test_lower_touch_fires_on_low_not_close():
    bar = _bar(low=94.0, close=99.0)
    hits = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert [h.signal_type for h in hits] == [SignalType.BB_LOWER_TOUCH]


def test_lower_touch_does_not_fire_when_only_close_is_below():
    # close below the band but the low never reached it is impossible for a
    # real bar; the guard is that detect never looks at close for bands.
    bar = _bar(low=96.0, close=94.0)
    assert sig.detect(bar, _ind(bb_lower=95.0), SP) == []


def test_upper_touch_fires_on_high():
    bar = _bar(high=106.0, close=100.0)
    hits = sig.detect(bar, _ind(bb_upper=105.0), SP)
    assert [h.signal_type for h in hits] == [SignalType.BB_UPPER_TOUCH]


def test_upper_touch_is_short_side():
    bar = _bar(high=106.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=105.0), SP)
    assert hit.side is Side.SHORT


def test_lower_touch_is_long_side():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.side is Side.LONG


# ---------------------------------------------------------------------------
# detect — stochastic conditions read the t-1 row it was handed
# ---------------------------------------------------------------------------


def test_stoch_oversold_fires_from_k_full():
    hits = sig.detect(_bar(), _ind(k=15.0), SP)
    assert [h.signal_type for h in hits] == [SignalType.STOCH_OVERSOLD]


def test_stoch_overbought_fires_from_k_full():
    hits = sig.detect(_bar(), _ind(k=85.0), SP)
    assert [h.signal_type for h in hits] == [SignalType.STOCH_OVERBOUGHT]


def test_stoch_thresholds_are_inclusive():
    assert sig.detect(_bar(), _ind(k=20.0), SP)
    assert sig.detect(_bar(), _ind(k=80.0), SP)


def test_stoch_only_hit_has_null_touch_level():
    # None, never NaN. A stochastic-only signal touched no band, and DESIGN
    # §3.4 pins that absence as None so equality and hashing stay coherent.
    (hit,) = sig.detect(_bar(), _ind(k=15.0), SP)
    assert hit.touch_level is None


# ---------------------------------------------------------------------------
# detect — confluence (pinned in DESIGN §3.6)
# ---------------------------------------------------------------------------


def test_confluence_low_requires_both_conditions():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0), SP)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_confluence_low_does_not_fire_on_band_touch_alone():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=50.0), SP)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


def test_confluence_high_requires_both_conditions():
    bar = _bar(high=106.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=105.0, k=85.0), SP)
    assert hit.signal_type is SignalType.CONFLUENCE_HIGH


# ---------------------------------------------------------------------------
# detect — multi-signal collapse (ADR 057)
# ---------------------------------------------------------------------------


def test_concurrent_signals_emit_one_hit_with_the_most_specific_type():
    bar = _bar(low=94.0)
    hits = sig.detect(bar, _ind(bb_lower=95.0, k=15.0), SP)
    assert len(hits) == 1
    assert hits[0].signal_type is SignalType.CONFLUENCE_LOW


def test_signal_types_all_carries_every_concurrent_signal():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0), SP)
    assert set(hit.signal_types_all) == {
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    }


def test_signal_types_all_is_ordered_most_specific_first():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0), SP)
    assert hit.signal_types_all == (
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    )


def test_signal_strength_counts_concurrent_signals():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0), SP)
    assert hit.signal_strength == 3


def test_both_sides_can_fire_on_one_wide_bar():
    # A bar spanning both bands with %K neutral: one long hit, one short hit.
    bar = _bar(low=94.0, high=106.0)
    hits = sig.detect(bar, _ind(bb_lower=95.0, bb_upper=105.0, k=50.0), SP)
    assert [h.side for h in hits] == [Side.LONG, Side.SHORT]


def test_no_signal_returns_empty_list():
    assert sig.detect(_bar(), _ind(), SP) == []


# ---------------------------------------------------------------------------
# detect — null handling (DESIGN §3.11: never fill, drop instead)
# ---------------------------------------------------------------------------


def test_null_band_produces_no_band_signal():
    bar = _bar(low=94.0)
    assert sig.detect(bar, _ind(bb_lower=np.nan), SP) == []


def test_null_k_full_produces_no_stochastic_signal():
    assert sig.detect(_bar(), _ind(k=np.nan), SP) == []


def test_null_k_full_still_allows_a_band_touch():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=np.nan), SP)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


# ---------------------------------------------------------------------------
# detect — payload fields
# ---------------------------------------------------------------------------


def test_hit_carries_ticker_and_bar_date():
    bar = _bar(low=94.0, ts="2026-07-29")
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.ticker == "TSM"
    assert hit.ts == pd.Timestamp("2026-07-29").date()


def test_long_hit_touch_level_is_the_lower_band():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.touch_level == 95.0


def test_short_hit_touch_level_is_the_upper_band():
    bar = _bar(high=106.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=105.0), SP)
    assert hit.touch_level == 105.0


def test_hit_carries_pctb_and_k_full_from_the_indicator_row():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, bb_pctb=-0.05, k_full=12.5), SP)
    assert hit.pctb == -0.05
    assert hit.k_full == 12.5


# ---------------------------------------------------------------------------
# detect — ADR 044 fast agreement, ADR 045 crossover never gates
# ---------------------------------------------------------------------------


def test_fast_agreement_disabled_lets_a_disagreeing_confluence_fire():
    """Renamed 2026-08-16: `require_fast_agreement` used to be False by
    default and this test read that default, so its name asserted a config
    value rather than a behaviour. Turning the flag on flipped the name to
    a lie while the assertion below stayed correct.

    The behaviour is what matters and is unchanged: with the check
    disabled, a 75-point gap between the two %K columns does not stop
    `confluence_low`. Stated against an explicit params object so it means
    the same thing whatever the default becomes.
    """
    sp = SignalParams(require_fast_agreement=False)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0, k_other=90.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_fast_agreement_is_on_by_default():
    """The default itself, asserted once and in one place.

    Every other test in this file now states its own `require_fast_agreement`
    or relies on `_route_k` mirroring the columns, so this is the only test
    that moves when the default moves — which is what makes flipping it a
    one-line change rather than an eleven-test change.
    """
    assert SignalParams().require_fast_agreement is True


def test_fast_agreement_blocks_confluence_when_fast_disagrees():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0, k_other=90.0), sp)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


def test_fast_agreement_allows_confluence_inside_tolerance():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0, k_other=19.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


# ---------------------------------------------------------------------------
# ADR 142 — agreement means agreeing about the state, not being close
# ---------------------------------------------------------------------------


def test_both_deeply_overbought_agree_even_though_they_are_far_apart():
    """The case ADR 044's tolerance was silently rejecting.

    %K_fast 99 and %K_full 89 are 10 points apart, so the +/-5 band refuses
    them. Both are far beyond `stoch_overbought`, which is the strongest
    possible statement the two columns can make together -- and the
    tolerance read the gap as disagreement.
    """
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(high=101.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=100.0, k=99.0, k_other=89.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_HIGH


def test_both_deeply_oversold_agree_even_though_they_are_far_apart():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=2.0, k_other=14.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_extremes_on_opposite_sides_are_not_agreement():
    """**The reason this reads a side rather than a pair of booleans.**

    %K_fast 15 and %K_full 90 are both extreme. They are also maximally
    opposed, and a rule phrased as "both beyond a threshold" without naming
    *which* threshold would call that agreement and fire a confluence on the
    most contradictory pair the two columns can produce.
    """
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0, k_other=90.0), sp)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


def test_one_extreme_and_one_merely_close_still_needs_the_tolerance():
    """%K_fast past the threshold, %K_full not, and 12 points apart.

    Neither limb holds, so this stays a band touch. The widened rule is a
    second way to satisfy agreement, not a removal of the first.
    """
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(high=101.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=100.0, k=95.0, k_other=83.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_HIGH
    # 95 and 83 are both overbought, so this one *does* fire -- named here so
    # the boundary below is read as deliberate rather than as an oversight.


def test_the_tolerance_limb_still_fires_on_its_own():
    """Nothing that fired before stops firing (the user's requirement).

    %K_full 77 is not oversold and 81 is not overbought, so only the +/-5
    band can carry this. It did before ADR 142 and it does after.
    """
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(high=101.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=100.0, k=81.0, k_other=77.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_HIGH


def test_the_widened_limb_is_on_by_default():
    """The default itself, asserted once, so flipping it is a one-line
    change rather than a six-test change."""
    assert SignalParams().fast_agreement_both_extreme is True


def test_disabling_the_widened_limb_restores_adr_044_exactly():
    """**Reconstructibility, the property ADR 108 and 109 exist for.**

    Every event measured before ADR 142 came from the tolerance alone. With
    this flag off, 99/89 is refused again -- so the superseded population is
    reachable from a config rather than only from a database snapshot.
    """
    sp = SignalParams(
        require_fast_agreement=True,
        fast_agreement_tol=5.0,
        fast_agreement_both_extreme=False,
    )
    bar = _bar(high=101.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=100.0, k=99.0, k_other=89.0), sp)
    assert hit.signal_type is SignalType.BB_UPPER_TOUCH


def test_the_widened_limb_is_still_governed_by_require_fast_agreement():
    """The master switch stays master. With agreement off entirely there is
    nothing for the second limb to add."""
    sp = SignalParams(require_fast_agreement=False, fast_agreement_both_extreme=True)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0, k_other=90.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_a_missing_column_still_fails_the_check():
    """Invariant 4's shape: absent is not permissive. A null %K_full cannot
    be beyond a threshold, so neither limb may treat it as agreement."""
    sp = SignalParams(require_fast_agreement=True)
    bar = _bar(high=101.0)
    ind = _ind(bb_upper=100.0, k_fast=99.0, k_full=float("nan"))
    types = [h.signal_type for h in sig.detect(bar, ind, sp)]
    assert SignalType.CONFLUENCE_HIGH not in types


def test_crossover_flags_never_change_the_signal_set():
    # ADR 045: computed and stored, but they never enter detect().
    bar = _bar(low=94.0)
    base = sig.detect(bar, _ind(bb_lower=95.0, k=15.0), SP)
    ind_with_cross = _ind(bb_lower=95.0, k=15.0)
    ind_with_cross["k_cross_up"] = True
    ind_with_cross["k_cross_down"] = True
    assert sig.detect(bar, ind_with_cross, SP) == base


# ---------------------------------------------------------------------------
# breach_live — the live path (DESIGN §3.6)
# ---------------------------------------------------------------------------


def test_breach_live_fires_lower_touch_on_the_quote():
    assert SignalType.BB_LOWER_TOUCH in sig.breach_live(94.0, _bands(bb_lower=95.0), SP)


def test_breach_live_fires_upper_touch_on_the_quote():
    assert SignalType.BB_UPPER_TOUCH in sig.breach_live(106.0, _bands(bb_upper=105.0), SP)


def test_breach_live_returns_empty_inside_the_bands():
    assert sig.breach_live(100.0, _bands(), SP) == []


def test_breach_live_reads_k_full_from_the_prior_close_bands():
    # %K is only defined at session end, so intraday it is a static qualifier.
    out = sig.breach_live(94.0, _bands(bb_lower=95.0, k=15.0), SP)
    assert SignalType.CONFLUENCE_LOW in out
    assert SignalType.STOCH_OVERSOLD in out


def test_breach_live_returns_every_concurrent_type_not_just_the_most_specific():
    out = sig.breach_live(94.0, _bands(bb_lower=95.0, k=15.0), SP)
    assert set(out) == {
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    }


def test_breach_live_respects_fast_agreement_flag():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    out = sig.breach_live(94.0, _bands(bb_lower=95.0, k=15.0, k_other=90.0), sp)
    assert SignalType.CONFLUENCE_LOW not in out
    assert SignalType.BB_LOWER_TOUCH in out


def test_breach_live_returns_empty_on_nan_quote():
    assert sig.breach_live(np.nan, _bands(bb_lower=95.0), SP) == []


# ---------------------------------------------------------------------------
# Missing-data paths — `core/` never fills, so every one returns null or False
# ---------------------------------------------------------------------------


def test_breach_treats_none_as_missing():
    assert not sig._breach(None, 95.0, Bound.LOWER)
    assert not sig._breach(94.0, None, Bound.LOWER)


def test_breach_treats_a_non_numeric_price_as_missing():
    # A text column leaking in must not raise mid-backfill, and must never
    # be coerced into a comparison that fires.
    assert not sig._breach("n/a", 95.0, Bound.LOWER)


def test_missing_indicator_column_reads_as_null_not_zero():
    # A row without bb_lower must not compare against an implicit 0.0.
    ind = pd.Series({"bb_upper": 105.0, "k_full": 50.0})
    assert sig.detect(_bar(low=1.0), ind, SP) == []


def test_none_valued_indicator_reads_as_null():
    ind = _ind(bb_lower=95.0)
    ind["bb_lower"] = None
    assert sig.detect(_bar(low=94.0), ind, SP) == []


def test_fast_agreement_fails_closed_when_k_fast_is_missing():
    # The flag exists to restrict the event set, so absent data must not
    # widen it by defaulting to "agrees".
    sp = SignalParams(require_fast_agreement=True)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k=15.0, k_other=np.nan), sp)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


# ---------------------------------------------------------------------------
# Bar date and ticker resolution
# ---------------------------------------------------------------------------


def test_bar_date_falls_back_to_a_ts_column_when_the_index_is_unnamed():
    bar = pd.Series(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 94.0,
            "close": 100.0,
            "ts": pd.Timestamp("2026-07-29"),
            "ticker": "TSM",
        }
    )
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.ts == pd.Timestamp("2026-07-29").date()


def test_bar_date_accepts_a_plain_date_label():
    import datetime as dt

    bar = _bar(low=94.0)
    bar.name = dt.date(2026, 7, 29)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.ts == dt.date(2026, 7, 29)


def test_bar_date_accepts_a_string_label():
    bar = _bar(low=94.0)
    bar.name = "2026-07-29"
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.ts == pd.Timestamp("2026-07-29").date()


def test_ticker_comes_from_the_bars_ticker_column():
    bar = _bar(low=94.0, ticker="NVDA")
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0), SP)
    assert hit.ticker == "NVDA"


def test_a_bar_without_a_ticker_field_raises():
    """`events.ticker` is NOT NULL. A silent "" would write an event
    attributed to no ticker, which is a fabricated value (DESIGN §3.11).

    Per-ticker frames loaded from the `bars` table carry `ticker` as a
    column, so this fires only when a caller builds a bar by hand and
    forgets it — which must fail loudly, not produce an orphan event.
    """
    bar = _bar(low=94.0, ticker=None)
    with pytest.raises(KeyError, match="ticker"):
        sig.detect(bar, _ind(bb_lower=95.0), SP)


def test_a_null_ticker_raises_rather_than_stringifying_to_none():
    # The column is present but null — `str(None)` would silently produce
    # the literal ticker "None".
    bar = _bar(low=94.0, ticker=None)
    bar["ticker"] = None
    with pytest.raises(KeyError, match="ticker"):
        sig.detect(bar, _ind(bb_lower=95.0), SP)


def test_an_empty_ticker_raises():
    bar = _bar(low=94.0, ticker="")
    with pytest.raises(KeyError, match="ticker"):
        sig.detect(bar, _ind(bb_lower=95.0), SP)


def test_a_missing_ticker_is_not_reported_until_a_signal_fires():
    """A quiet bar returns [] without touching `ticker`, so a frame that
    legitimately omits it never breaks bulk scanning of non-events."""
    assert sig.detect(_bar(), _ind(), SP) == []


# ---------------------------------------------------------------------------
# touch_level is float | None, never NaN (DESIGN §3.4)
# ---------------------------------------------------------------------------


def test_identical_stoch_only_hits_compare_equal():
    """The bug NaN caused. Two structurally identical hits must be equal."""
    (a,) = sig.detect(_bar(), _ind(k=15.0), SP)
    (b,) = sig.detect(_bar(), _ind(k=15.0), SP)
    assert a == b


def test_identical_stoch_only_hits_collapse_in_a_set():
    """Hashing and equality must agree.

    Frozen dataclasses derive `__hash__` from their fields and `hash(nan)`
    is stable, so under the old NaN encoding two identical hits hashed the
    same and compared unequal — a set kept both. That is precisely the
    silent duplicate a debounce set would have let through.
    """
    (a,) = sig.detect(_bar(), _ind(k=15.0), SP)
    (b,) = sig.detect(_bar(), _ind(k=15.0), SP)
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_a_band_hit_still_carries_a_float_touch_level():
    (hit,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0), SP)
    assert isinstance(hit.touch_level, float)
    assert hit.touch_level == 95.0


def test_no_hit_ever_carries_a_nan_touch_level():
    """Sweep the signal space: every reachable hit is None or a real float."""
    cases = [
        (_bar(low=94.0), _ind(bb_lower=95.0, k=50.0)),  # band only
        (_bar(), _ind(k=15.0)),  # stoch only
        (_bar(low=94.0), _ind(bb_lower=95.0, k=15.0)),  # confluence low
        (_bar(high=106.0), _ind(bb_upper=105.0, k=50.0)),  # upper band
        (_bar(), _ind(k=85.0)),  # stoch overbought
        (_bar(high=106.0), _ind(bb_upper=105.0, k=85.0)),  # confluence high
        (_bar(low=94.0, high=106.0), _ind(bb_lower=95.0, bb_upper=105.0)),  # both
    ]
    for bar, ind in cases:
        for hit in sig.detect(bar, ind, SP):
            assert hit.touch_level is None or not np.isnan(hit.touch_level)


# ---------------------------------------------------------------------------
# debounce_key — the explicit tuple (DESIGN §4.7)
# ---------------------------------------------------------------------------


def test_debounce_key_is_ticker_date_bound():
    (hit,) = sig.detect(_bar(low=94.0, ticker="TSM"), _ind(bb_lower=95.0), SP)
    assert sig.debounce_key(hit) == ("TSM", pd.Timestamp("2026-07-29").date(), Bound.LOWER)


def test_debounce_key_maps_a_short_hit_to_the_upper_bound():
    (hit,) = sig.detect(_bar(high=106.0, ticker="TSM"), _ind(bb_upper=105.0), SP)
    assert sig.debounce_key(hit) == ("TSM", pd.Timestamp("2026-07-29").date(), Bound.UPPER)


def test_debounce_key_collapses_a_band_hit_and_a_confluence_hit():
    """One event per ticker per bound per day. A lower touch on Monday and a
    confluence-low on the same Monday are the same debounce slot, even though
    the two hits carry different signal_types."""
    (band,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0, k=50.0), SP)
    (conf,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0, k=15.0), SP)
    assert band.signal_type is not conf.signal_type
    assert sig.debounce_key(band) == sig.debounce_key(conf)


def test_debounce_key_separates_the_two_sides_on_one_bar():
    """A wide bar touching both bands is two events, not one."""
    hits = sig.detect(_bar(low=94.0, high=106.0), _ind(bb_lower=95.0, bb_upper=105.0), SP)
    assert len({sig.debounce_key(h) for h in hits}) == 2


def test_debounce_key_separates_days():
    (a,) = sig.detect(_bar(low=94.0, ts="2026-07-29"), _ind(bb_lower=95.0), SP)
    (b,) = sig.detect(_bar(low=94.0, ts="2026-07-30"), _ind(bb_lower=95.0), SP)
    assert sig.debounce_key(a) != sig.debounce_key(b)


def test_debounce_key_separates_tickers():
    (a,) = sig.detect(_bar(low=94.0, ticker="TSM"), _ind(bb_lower=95.0), SP)
    (b,) = sig.detect(_bar(low=94.0, ticker="NVDA"), _ind(bb_lower=95.0), SP)
    assert sig.debounce_key(a) != sig.debounce_key(b)


def test_debounce_key_is_hashable_and_stable():
    (hit,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0), SP)
    assert sig.debounce_key(hit) == sig.debounce_key(hit)
    assert len({sig.debounce_key(hit), sig.debounce_key(hit)}) == 1


def test_debounce_key_ignores_float_fields_entirely():
    """The property that makes it immune to the next float field added.

    Two hits differing only in pctb occupy the same debounce slot, so a
    future NaN-valued field cannot reintroduce silent duplicates.
    """
    (a,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0, bb_pctb=-0.05), SP)
    (b,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0, bb_pctb=0.42), SP)
    assert a != b
    assert sig.debounce_key(a) == sig.debounce_key(b)


def test_a_stochastic_only_hit_still_has_a_debounce_key():
    """No band was touched, so touch_level is None — the key must still
    resolve, from side rather than from the absent level."""
    (hit,) = sig.detect(_bar(ticker="TSM"), _ind(k=15.0), SP)
    assert sig.debounce_key(hit) == ("TSM", pd.Timestamp("2026-07-29").date(), Bound.LOWER)


# --------------------------------------------------------------------------
# BEAR_CLOSE_ABOVE_UPPER (ADR 108) — the close-confirmed model
# --------------------------------------------------------------------------


def _bear_bar(flag=True, **kw):
    """A bar carrying the precomputed close-confirmed boolean.

    The flag arrives on the **bar**, not the indicator row, which is what
    dates the event to D rather than D+1 (ADR 108). Raw `open`/`close` are
    still never read by `detect`.
    """
    bar = _bar(**kw)
    bar["bear_close_above_upper"] = flag
    return bar


def test_the_flag_fires_the_close_confirmed_type():
    hits = sig.detect(_bear_bar(flag=True, high=106.0), _ind(), SP)
    short = [h for h in hits if h.side is Side.SHORT]
    assert short
    assert SignalType.BEAR_CLOSE_ABOVE_UPPER in short[0].signal_types_all


def test_a_false_flag_does_not_fire_it():
    hits = sig.detect(_bear_bar(flag=False, high=106.0), _ind(), SP)
    for hit in hits:
        assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in hit.signal_types_all


def test_a_missing_flag_does_not_fire_it():
    """Every bar predating the indicator column carries no such field. A
    KeyError here would break the whole existing corpus, and a default of
    True would fire on all of it."""
    hits = sig.detect(_bar(high=106.0), _ind(), SP)
    for hit in hits:
        assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in hit.signal_types_all


def test_a_null_flag_does_not_fire_it():
    """NULL through warmup (invariant 4) must read as "not fired", never as
    truthy. `np.nan` is truthy in Python, so this is a real trap."""
    hits = sig.detect(_bear_bar(flag=np.nan, high=106.0), _ind(), SP)
    for hit in hits:
        assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in hit.signal_types_all


def test_it_ranks_above_confluence_high():
    """ADR 108's ranking: a band breach plus a close-confirmed rejection is
    strictly more specific than a band touch plus a stochastic extreme."""
    bar = _bear_bar(flag=True, high=106.0)
    hits = sig.detect(bar, _ind(k=85.0), SP)
    short = [h for h in hits if h.side is Side.SHORT][0]
    assert short.signal_type is SignalType.BEAR_CLOSE_ABOVE_UPPER
    assert SignalType.CONFLUENCE_HIGH in short.signal_types_all


def test_it_raises_signal_strength_when_it_co_fires():
    """`signal_strength` counts concurrent types (ADR 057), which is exactly
    why this signal forces a new `config_hash` rather than a backfill."""
    plain = sig.detect(_bar(high=106.0), _ind(k=85.0), SP)
    plain_short = [h for h in plain if h.side is Side.SHORT][0]
    withflag = sig.detect(_bear_bar(flag=True, high=106.0), _ind(k=85.0), SP)
    flag_short = [h for h in withflag if h.side is Side.SHORT][0]
    assert flag_short.signal_strength == plain_short.signal_strength + 1


def test_it_is_short_side_only():
    """No bullish counterpart was requested, and ADR 106 already rejects
    long/short symmetry as an assumption."""
    hits = sig.detect(_bear_bar(flag=True, high=106.0, low=94.0), _ind(), SP)
    for hit in hits:
        if hit.side is Side.LONG:
            assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in hit.signal_types_all


def test_the_specificity_order_is_fully_pinned():
    """All four short types, ranked. A partial assertion would pass on a
    ranking that got two of them backwards."""
    assert sig._SPECIFICITY[Side.SHORT] == (
        SignalType.BEAR_CLOSE_ABOVE_UPPER,
        SignalType.CONFLUENCE_HIGH,
        SignalType.BB_UPPER_TOUCH,
        SignalType.STOCH_OVERBOUGHT,
    )


def test_the_long_side_ranking_is_unchanged():
    assert sig._SPECIFICITY[Side.LONG] == (
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    )


def test_breach_live_never_returns_the_close_confirmed_type():
    """The live path walks a session tick by tick and has no close to
    confirm against. A poller that could emit this type would fire it
    intraday, before the bar that defines it exists."""
    for price in (90.0, 100.0, 106.0, 120.0):
        assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in sig.breach_live(price, _bands(), SP)


def test_the_flag_alone_fires_without_any_stochastic_extreme():
    """It is not gated on %K. The poller gates its *display* on
    confluence_high (user's decision), but the signal itself stands alone
    so the backtest can measure both populations."""
    hits = sig.detect(_bear_bar(flag=True, high=106.0), _ind(k=50.0), SP)
    short = [h for h in hits if h.side is Side.SHORT][0]
    assert short.signal_type is SignalType.BEAR_CLOSE_ABOVE_UPPER
    assert SignalType.CONFLUENCE_HIGH not in short.signal_types_all


# ---------------------------------------------------------------------------
# `stoch_source` — which %K column the oversold/overbought test reads
# ---------------------------------------------------------------------------


class TestStochSourceSelectsTheColumn:
    """The field exists as an A/B switch (2026-08-05, user's decision) and
    until now nothing asserted that it switches anything.

    That gap is the dangerous kind. `stoch_source` is a `Config` field, so
    setting it moves `config_hash` and mints a separate identity whatever it
    does — a run labelled `k_fast` that silently kept reading `k_full` would
    produce a clean, reproducible, *wrong* comparison, and the A/B it exists
    to support would conclude the two columns behave identically.

    Every fixture below puts the two %K values on opposite sides of the
    threshold, so no test can pass by reading the wrong column and landing
    on the same answer. The negative cases matter as much as the positive
    ones: an implementation reading *either* column passes the two
    `_fires_on_` tests and fails the two `_ignores_` tests.
    """

    def test_k_full_source_fires_on_k_full(self):
        hits = sig.detect(_bar(), _ind(k_full=15.0, k_fast=50.0), SP_FULL)
        assert [h.signal_type for h in hits] == [SignalType.STOCH_OVERSOLD]

    def test_k_full_source_ignores_an_extreme_k_fast(self):
        assert sig.detect(_bar(), _ind(k_full=50.0, k_fast=15.0), SP_FULL) == []

    def test_k_fast_source_fires_on_k_fast(self):
        hits = sig.detect(_bar(), _ind(k_full=50.0, k_fast=15.0), SP_FAST)
        assert [h.signal_type for h in hits] == [SignalType.STOCH_OVERSOLD]

    def test_k_fast_source_ignores_an_extreme_k_full(self):
        assert sig.detect(_bar(), _ind(k_full=15.0, k_fast=50.0), SP_FAST) == []

    def test_the_overbought_side_switches_too(self):
        """Both comparisons read the one `k`, so a swap that fixed only the
        oversold branch would leave the short side on the other column."""
        assert sig.detect(_bar(), _ind(k_full=50.0, k_fast=85.0), SP_FAST)
        assert sig.detect(_bar(), _ind(k_full=85.0, k_fast=50.0), SP_FAST) == []

    def test_confluence_follows_the_selected_column(self):
        """`confluence_high` is band touch AND overbought, so it must read
        the same column the bare stochastic type does. Two definitions of
        "overbought" inside one module is the failure this guards."""
        bar = _bar(high=106.0)
        (hit,) = sig.detect(bar, _ind(bb_upper=105.0, k_full=50.0, k_fast=85.0), SP_FAST)
        assert hit.signal_type is SignalType.CONFLUENCE_HIGH

    def test_breach_live_reads_the_same_column_as_detect(self):
        """ADR 006: the live and backtest paths must agree on which signals
        exist. A `stoch_source` honoured in `detect` but not in
        `breach_live` would have the poller and the backtest disagree about
        what fired, which is the exact drift invariant 2 exists to prevent.
        """
        out = sig.breach_live(94.0, _bands(bb_lower=95.0, k_full=50.0, k_fast=15.0), SP_FAST)
        assert SignalType.STOCH_OVERSOLD in out

    def test_a_null_in_the_selected_column_fires_nothing(self):
        """Invariant 4's shape: a missing value is never filled. The
        non-selected column being present and extreme must not rescue it."""
        assert sig.detect(_bar(), _ind(k_full=15.0, k_fast=np.nan), SP_FAST) == []

    def test_the_hit_still_reports_k_full_whatever_the_source(self):
        """`SignalHit.k_full` is a recorded field, not the deciding one
        (`core/signals.py:320` reads it unconditionally). Worth pinning:
        under a `k_fast` run the reported number is no longer the value that
        fired the signal, and a reader comparing the two would otherwise
        have no statement of that anywhere.
        """
        (hit,) = sig.detect(_bar(), _ind(k_full=50.0, k_fast=15.0), SP_FAST)
        assert hit.k_full == 50.0
