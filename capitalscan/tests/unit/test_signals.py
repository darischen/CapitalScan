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

SP = SignalParams()


def _bar(open_=100.0, high=101.0, low=99.0, close=100.0, ts="2026-07-29", ticker="TSM"):
    """A bar as `read_bars` returns one: `ticker` is a column, `ts` the index
    label. Matches the golden fixtures' `ticker,ts,open,high,low,close,...`
    shape, so tests exercise the real caller contract."""
    fields = {"open": open_, "high": high, "low": low, "close": close}
    if ticker is not None:
        fields["ticker"] = ticker
    return pd.Series(fields, name=pd.Timestamp(ts))


def _ind(
    bb_lower=95.0,
    bb_mid=100.0,
    bb_upper=105.0,
    bb_pctb=0.5,
    k_full=50.0,
    d_full=50.0,
    k_fast=50.0,
    atr_14=2.0,
):
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
    base.update(kw)
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
    hits = sig.detect(_bar(), _ind(k_full=15.0), SP)
    assert [h.signal_type for h in hits] == [SignalType.STOCH_OVERSOLD]


def test_stoch_overbought_fires_from_k_full():
    hits = sig.detect(_bar(), _ind(k_full=85.0), SP)
    assert [h.signal_type for h in hits] == [SignalType.STOCH_OVERBOUGHT]


def test_stoch_thresholds_are_inclusive():
    assert sig.detect(_bar(), _ind(k_full=20.0), SP)
    assert sig.detect(_bar(), _ind(k_full=80.0), SP)


def test_stoch_only_hit_has_null_touch_level():
    # None, never NaN. A stochastic-only signal touched no band, and DESIGN
    # §3.4 pins that absence as None so equality and hashing stay coherent.
    (hit,) = sig.detect(_bar(), _ind(k_full=15.0), SP)
    assert hit.touch_level is None


# ---------------------------------------------------------------------------
# detect — confluence (pinned in DESIGN §3.6)
# ---------------------------------------------------------------------------


def test_confluence_low_requires_both_conditions():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0), SP)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_confluence_low_does_not_fire_on_band_touch_alone():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=50.0), SP)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


def test_confluence_high_requires_both_conditions():
    bar = _bar(high=106.0)
    (hit,) = sig.detect(bar, _ind(bb_upper=105.0, k_full=85.0), SP)
    assert hit.signal_type is SignalType.CONFLUENCE_HIGH


# ---------------------------------------------------------------------------
# detect — multi-signal collapse (ADR 057)
# ---------------------------------------------------------------------------


def test_concurrent_signals_emit_one_hit_with_the_most_specific_type():
    bar = _bar(low=94.0)
    hits = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0), SP)
    assert len(hits) == 1
    assert hits[0].signal_type is SignalType.CONFLUENCE_LOW


def test_signal_types_all_carries_every_concurrent_signal():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0), SP)
    assert set(hit.signal_types_all) == {
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    }


def test_signal_types_all_is_ordered_most_specific_first():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0), SP)
    assert hit.signal_types_all == (
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    )


def test_signal_strength_counts_concurrent_signals():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0), SP)
    assert hit.signal_strength == 3


def test_both_sides_can_fire_on_one_wide_bar():
    # A bar spanning both bands with %K neutral: one long hit, one short hit.
    bar = _bar(low=94.0, high=106.0)
    hits = sig.detect(bar, _ind(bb_lower=95.0, bb_upper=105.0, k_full=50.0), SP)
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
    assert sig.detect(_bar(), _ind(k_full=np.nan), SP) == []


def test_null_k_full_still_allows_a_band_touch():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=np.nan), SP)
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


def test_fast_agreement_off_by_default_lets_confluence_fire():
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0, k_fast=90.0), SP)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_fast_agreement_blocks_confluence_when_fast_disagrees():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0, k_fast=90.0), sp)
    assert hit.signal_type is SignalType.BB_LOWER_TOUCH


def test_fast_agreement_allows_confluence_inside_tolerance():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    bar = _bar(low=94.0)
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0, k_fast=19.0), sp)
    assert hit.signal_type is SignalType.CONFLUENCE_LOW


def test_crossover_flags_never_change_the_signal_set():
    # ADR 045: computed and stored, but they never enter detect().
    bar = _bar(low=94.0)
    base = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0), SP)
    ind_with_cross = _ind(bb_lower=95.0, k_full=15.0)
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
    out = sig.breach_live(94.0, _bands(bb_lower=95.0, k_full=15.0), SP)
    assert SignalType.CONFLUENCE_LOW in out
    assert SignalType.STOCH_OVERSOLD in out


def test_breach_live_returns_every_concurrent_type_not_just_the_most_specific():
    out = sig.breach_live(94.0, _bands(bb_lower=95.0, k_full=15.0), SP)
    assert set(out) == {
        SignalType.CONFLUENCE_LOW,
        SignalType.BB_LOWER_TOUCH,
        SignalType.STOCH_OVERSOLD,
    }


def test_breach_live_respects_fast_agreement_flag():
    sp = SignalParams(require_fast_agreement=True, fast_agreement_tol=5.0)
    out = sig.breach_live(94.0, _bands(bb_lower=95.0, k_full=15.0, k_fast=90.0), sp)
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
    (hit,) = sig.detect(bar, _ind(bb_lower=95.0, k_full=15.0, k_fast=np.nan), sp)
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
    (a,) = sig.detect(_bar(), _ind(k_full=15.0), SP)
    (b,) = sig.detect(_bar(), _ind(k_full=15.0), SP)
    assert a == b


def test_identical_stoch_only_hits_collapse_in_a_set():
    """Hashing and equality must agree.

    Frozen dataclasses derive `__hash__` from their fields and `hash(nan)`
    is stable, so under the old NaN encoding two identical hits hashed the
    same and compared unequal — a set kept both. That is precisely the
    silent duplicate a debounce set would have let through.
    """
    (a,) = sig.detect(_bar(), _ind(k_full=15.0), SP)
    (b,) = sig.detect(_bar(), _ind(k_full=15.0), SP)
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_a_band_hit_still_carries_a_float_touch_level():
    (hit,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0), SP)
    assert isinstance(hit.touch_level, float)
    assert hit.touch_level == 95.0


def test_no_hit_ever_carries_a_nan_touch_level():
    """Sweep the signal space: every reachable hit is None or a real float."""
    cases = [
        (_bar(low=94.0), _ind(bb_lower=95.0, k_full=50.0)),  # band only
        (_bar(), _ind(k_full=15.0)),  # stoch only
        (_bar(low=94.0), _ind(bb_lower=95.0, k_full=15.0)),  # confluence low
        (_bar(high=106.0), _ind(bb_upper=105.0, k_full=50.0)),  # upper band
        (_bar(), _ind(k_full=85.0)),  # stoch overbought
        (_bar(high=106.0), _ind(bb_upper=105.0, k_full=85.0)),  # confluence high
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
    (band,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0, k_full=50.0), SP)
    (conf,) = sig.detect(_bar(low=94.0), _ind(bb_lower=95.0, k_full=15.0), SP)
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
    (hit,) = sig.detect(_bar(ticker="TSM"), _ind(k_full=15.0), SP)
    assert sig.debounce_key(hit) == ("TSM", pd.Timestamp("2026-07-29").date(), Bound.LOWER)
