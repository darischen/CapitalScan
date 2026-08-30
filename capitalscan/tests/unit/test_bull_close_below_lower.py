"""ADR 144: the long-side close-confirmed flag, and its mirror properties.

    close > open  AND  close <= bb_lower[t]

The interesting tests are not "it fires when it should". They are that it is
a *mirror* -- same band lag, same same-day band, same null-through-warmup
treatment -- because any asymmetry between the two sides that nobody chose is
a claim ADR 016 says must be measured rather than assumed.

And that it is **dormant**: defined, computed, and unable to fire, because
`SignalParams.enabled_signal_types` does not list it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core import indicators as ind_mod
from capitalscan.core import signals as sig
from capitalscan.core.config import Config, IndicatorParams, SignalParams
from capitalscan.core.types import Bands, Side, SignalType
from capitalscan.jobs.config import config_hash
from capitalscan.jobs.poll import bull_reversal_state, is_bear_reversal, is_bull_reversal
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """`(open, high, low, close)` per bar, dated daily from 2024-01-01."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# The indicator
# ---------------------------------------------------------------------------


class TestTheFlagIsNullThroughWarmup:
    def test_never_false_before_a_band_exists(self):
        """Invariant 4's shape. "No band yet" is not "did not fire", and a
        False there would read as a measured negative.

        This is also the sharpest trap on the read side: `bool(float("nan"))`
        is `True` in Python, so a naive reader fires on every warmup bar.
        """
        bars = _bars([(100.0, 101.0, 99.0, 100.5)] * 30)
        out = ind_mod.bull_close_below_lower(bars, IndicatorParams())
        col = out["bull_close_below_lower"]
        # 20-bar Bollinger window: the first 19 have no band at all.
        assert col.iloc[:19].isna().all()
        assert not (col.iloc[:19] == False).any()  # noqa: E712 - the point


class TestTheRule:
    def _fire_frame(self) -> pd.DataFrame:
        # Twenty flat bars build a band, then one bar drops far below it and
        # closes above its own open: down through the band, bought back.
        rows = [(100.0, 100.5, 99.5, 100.0)] * 20
        rows.append((90.0, 95.0, 89.0, 94.0))  # close 94 > open 90
        return _bars(rows)

    def test_fires_on_an_up_bar_closing_below_the_band(self):
        out = ind_mod.bull_close_below_lower(self._fire_frame(), IndicatorParams())
        assert bool(out["bull_close_below_lower"].iloc[-1]) is True

    def test_does_not_fire_when_the_bar_closed_down(self):
        rows = [(100.0, 100.5, 99.5, 100.0)] * 20
        rows.append((95.0, 95.0, 89.0, 90.0))  # close 90 < open 95
        out = ind_mod.bull_close_below_lower(_bars(rows), IndicatorParams())
        assert bool(out["bull_close_below_lower"].iloc[-1]) is False

    def test_does_not_fire_when_the_close_stayed_inside_the_band(self):
        rows = [(100.0, 100.5, 99.5, 100.0)] * 20
        rows.append((99.6, 100.2, 99.4, 100.1))  # up bar, but inside
        out = ind_mod.bull_close_below_lower(_bars(rows), IndicatorParams())
        assert bool(out["bull_close_below_lower"].iloc[-1]) is False


class TestItMirrorsTheBearFlag:
    """Any difference between the two that nobody chose is an accidental
    asymmetry, and ADR 016 rejects assumed long/short symmetry in *returns*
    precisely so that structural asymmetries have to be deliberate."""

    def test_same_warmup(self):
        bull = ind_mod._REGISTRY["bull_close_below_lower"]
        bear = ind_mod._REGISTRY["bear_close_above_upper"]
        assert bull.warmup == bear.warmup

    def test_same_dependencies_and_dtype(self):
        bull = ind_mod._REGISTRY["bull_close_below_lower"]
        bear = ind_mod._REGISTRY["bear_close_above_upper"]
        assert set(bull.deps) == set(bear.deps) == {"open", "close"}
        assert bull.dtype == bear.dtype == "boolean"

    def test_both_obey_the_one_shared_band_lag(self):
        """One field governs both, not two.

        The lag says how a close-confirmed band is read; it is not a
        statement about which side is being read. Two fields would let the
        sides silently disagree, which is invariant 9's failure mode.
        """
        rows = [(100.0, 100.5, 99.5, 100.0)] * 20
        rows.append((90.0, 95.0, 89.0, 94.0))
        bars = _bars(rows)
        shifted = IndicatorParams(bear_close_band_lag=1)
        a = ind_mod.bull_close_below_lower(bars, IndicatorParams())
        b = ind_mod.bull_close_below_lower(bars, shifted)
        # The lag is honoured on this side too -- the two frames differ
        # somewhere, which is all this needs to show.
        assert not a["bull_close_below_lower"].equals(b["bull_close_below_lower"])


# ---------------------------------------------------------------------------
# Dormancy — the property that lets this ship mid-backtest
# ---------------------------------------------------------------------------


class TestItIsDefinedButDisabled:
    def test_not_in_the_enabled_set(self):
        assert "bull_close_below_lower" not in SignalParams().enabled_signal_types

    def test_enabled_types_does_not_resolve_it(self):
        assert SignalType.BULL_CLOSE_BELOW_LOWER not in sig.enabled_types(SignalParams())

    def test_it_cannot_fire_while_disabled(self):
        """The end-to-end guarantee, not just the config value.

        `_types_fired` filters by `allowed`, so even with the flag set on the
        bar the type is dropped before it reaches a hit.
        """
        fired = sig._types_fired(
            low=90.0,
            high=95.0,
            ind=_ind_row(),
            sp=SignalParams(),
            bull_close=True,
        )
        assert SignalType.BULL_CLOSE_BELOW_LOWER not in fired[Side.LONG]

    def test_it_does_fire_once_enabled(self):
        """The other half: dormancy is a config choice, not a broken wire.

        Without this, every test above would pass on an implementation that
        never worked at all.
        """
        sp = SignalParams(
            enabled_signal_types=SignalParams().enabled_signal_types + ("bull_close_below_lower",)
        )
        fired = sig._types_fired(low=90.0, high=95.0, ind=_ind_row(), sp=sp, bull_close=True)
        assert SignalType.BULL_CLOSE_BELOW_LOWER in fired[Side.LONG]

    def test_adding_the_enum_member_did_not_move_config_hash(self):
        """ADR 108's trap, used deliberately.

        `config_hash` is over `dataclasses.asdict(Config)` and an enum member
        is not a field, so defining the type costs nothing. That is what let
        this land while a backtest was running under this exact hash.
        Enabling it is the deliberate act that moves it.
        """
        assert config_hash(Config()) == DEFAULT_CONFIG_HASH


def _ind_row(**kw) -> pd.Series:
    base = {
        "bb_lower": 95.0,
        "bb_mid": 100.0,
        "bb_upper": 105.0,
        "bb_pctb": 0.5,
        "k_full": 50.0,
        "d_full": 50.0,
        "k_fast": 50.0,
        "atr_14": 2.0,
    }
    base.update(kw)
    return pd.Series(base)


# ---------------------------------------------------------------------------
# The live half
# ---------------------------------------------------------------------------


def _bands(**kw) -> Bands:
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


class TestIsBullReversal:
    def test_below_the_band_and_above_the_open_confirms(self):
        assert is_bull_reversal(price=94.0, day_open=90.0, bands=_bands()) is True

    def test_below_the_band_but_under_the_open_does_not(self):
        assert is_bull_reversal(price=94.0, day_open=96.0, bands=_bands()) is False

    def test_above_the_band_never_confirms(self):
        assert is_bull_reversal(price=99.0, day_open=90.0, bands=_bands()) is False

    def test_a_missing_open_is_not_a_fire(self):
        """ "Cannot evaluate" is not "did not fire" is not "fired". A quote
        without `regularMarketOpen` must not produce a signal."""
        assert is_bull_reversal(price=94.0, day_open=float("nan"), bands=_bands()) is False

    def test_the_two_live_predicates_are_mutually_exclusive(self):
        """One price cannot be below the lower band and above the upper one.

        Not a tautology worth skipping: it is what makes storing both blocks
        on every report safe, since at most one can ever confirm.
        """
        for price in (85.0, 94.0, 100.0, 106.0, 120.0):
            both = is_bull_reversal(price, 90.0, _bands()) and is_bear_reversal(
                price, 90.0, _bands()
            )
            assert not both


class TestBullReversalState:
    def test_above_band_means_beyond_the_band_on_this_side(self):
        """The field name is reused, and its meaning is side-relative.

        For a long-side state, "the band condition holds" means price is
        *below* the lower band. Reusing the name keeps one shape across both
        sides so the badge, the notification and the CSV read one set of
        fields.
        """
        st = bull_reversal_state(price=94.0, day_open=90.0, bands=_bands())
        assert st.above_band is True
        assert st.confirmed is True

    def test_open_gap_atr_keeps_its_sign_convention(self):
        """Always `(price - open) / ATR`.

        For a bear reversal negative confirms; here positive does. Negating
        it so "negative always confirms" would make one column mean two
        things depending on a sibling field.
        """
        st = bull_reversal_state(price=94.0, day_open=90.0, bands=_bands(atr_14=2.0))
        assert st.open_gap_atr == pytest.approx(2.0)

    def test_distances_are_none_rather_than_zero_when_unknown(self):
        st = bull_reversal_state(price=94.0, day_open=float("nan"), bands=_bands())
        assert st.open_gap is None
        assert st.open_gap_atr is None
        assert st.band_gap is not None  # this one is still computable

    def test_a_zero_atr_yields_none_not_a_division_error(self):
        st = bull_reversal_state(price=94.0, day_open=90.0, bands=_bands(atr_14=0.0))
        assert st.open_gap_atr is None

    def test_label_reads_n_a_when_the_band_condition_fails(self):
        st = bull_reversal_state(price=100.0, day_open=90.0, bands=_bands())
        assert st.label == "n/a"


# ---------------------------------------------------------------------------
# Specificity
# ---------------------------------------------------------------------------


class TestSpecificity:
    def test_the_long_side_ranks_it_first(self):
        assert sig._SPECIFICITY[Side.LONG][0] is SignalType.BULL_CLOSE_BELOW_LOWER

    def test_both_sides_now_rank_the_same_way(self):
        long_kinds = [t.value.split("_")[0] for t in sig._SPECIFICITY[Side.LONG]]
        short_kinds = [t.value.split("_")[0] for t in sig._SPECIFICITY[Side.SHORT]]
        assert long_kinds == ["bull", "confluence", "bb", "stoch"]
        assert short_kinds == ["bear", "confluence", "bb", "stoch"]
