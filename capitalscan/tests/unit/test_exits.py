"""Unit tests for core/exits.py.

BUILD.md §3.2 names four details that decide correctness, and each gets its
own section below: gap checks run first, band levels come from bar i-1,
`ambiguous` is set on a stop-and-target collision, and the stochastic exit
is evaluated last because a close-triggered exit cannot preempt an intraday
fill that already happened.

Priority order is pinned in DESIGN §5.5 and is not re-derived here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import exits
from capitalscan.core.config import ExitParams
from capitalscan.core.types import ExitReason, Side

# **Pinned, not inherited.** These tests check the exit *mechanics* -- which
# level fills, which side wins a tie, where a gap fills -- against hand-worked
# arithmetic, so they must not move when the production defaults move. They
# did on 2026-08-29, when the sweep set `stop_atr_k` 1.5 -> 2.0 and six tests
# failed with the engine computing the right answer for the new default
# (a short's stop went 100 + 1.5*2 = 103 to 100 + 2.0*2 = 104).
#
# `test_backtest_cli.py::test_exit_params_defaults_match_adr_059` is the test
# that *should* track the production values; this file should not.
EP = ExitParams(target_pct=0.04, stop_mode="atr", stop_atr_k=1.5)
ATR = 2.0
ENTRY = 100.0

# Default config: target 4% -> 104.0, ATR stop k=1.5 -> 100 - 3.0 = 97.0.
TARGET = 104.0
STOP = 97.0


def _fwd(opens, highs, lows, closes, start="2026-07-30"):
    idx = pd.date_range(start, periods=len(opens), freq="B")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def _quiet(n=5):
    """Five forward bars that trigger nothing: no stop, no target, no band."""
    return _fwd(opens=[100.0] * n, highs=[101.0] * n, lows=[99.0] * n, closes=[100.0] * n)


def _ind(bars, bb_upper=200.0, bb_mid=150.0, bb_lower=1.0, k_full=50.0):
    """Indicator rows aligned to `bars`. Bands default far out of reach so a
    test only sees the condition it is exercising."""
    return pd.DataFrame(
        {
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "k_full": k_full,
        },
        index=bars.index,
    )


def _resolve(bars, ind=None, ep=EP, side=Side.LONG, entry=ENTRY, **kw):
    return exits.resolve_exit(
        entry_price=entry,
        entry_idx=0,
        side=side,
        fwd_bars=bars,
        fwd_ind=_ind(bars) if ind is None else ind,
        atr_at_entry=ATR,
        ep=ep,
        **kw,
    )


# ---------------------------------------------------------------------------
# Timeout — the terminal case
# ---------------------------------------------------------------------------


def test_quiet_path_times_out_on_the_final_bar():
    r = _resolve(_quiet())
    assert r.reason is ExitReason.TIMEOUT
    assert r.exit_idx == 4
    assert r.holding_days == 5


def test_timeout_fills_at_the_final_bar_close():
    bars = _quiet()
    bars.iloc[-1, bars.columns.get_loc("close")] = 100.5
    r = _resolve(bars)
    assert r.exit_price == pytest.approx(100.5)


def test_a_short_window_times_out_at_its_own_end():
    # A delisting truncates the window; the position exits at the last close.
    r = _resolve(_quiet(2))
    assert r.reason is ExitReason.TIMEOUT
    assert r.holding_days == 2


def test_max_hold_days_truncates_a_longer_window():
    r = _resolve(_quiet(5), ep=ExitParams(max_hold_days=3))
    assert r.holding_days == 3
    assert r.exit_idx == 2


# ---------------------------------------------------------------------------
# Detail 1 — gap checks run first (DESIGN §5.5)
# ---------------------------------------------------------------------------


def test_gap_through_the_stop_fills_at_the_open_not_the_stop():
    # Opened at 92, eight points below the 97 stop. Filling at 97 would book
    # a 3% loss on a move that was already 8% against.
    bars = _fwd(opens=[92.0, 100.0], highs=[93.0, 101.0], lows=[91.0, 99.0], closes=[92.5, 100.0])
    r = _resolve(bars)
    assert r.reason is ExitReason.STOP
    assert r.exit_price == pytest.approx(92.0)


def test_gap_through_the_target_fills_at_the_open_not_the_target():
    bars = _fwd(
        opens=[108.0, 100.0], highs=[109.0, 101.0], lows=[107.0, 99.0], closes=[108.5, 100.0]
    )
    r = _resolve(bars)
    assert r.reason is ExitReason.TARGET
    assert r.exit_price == pytest.approx(108.0)


def test_gap_stop_beats_gap_target_on_the_same_bar():
    # A bar opening below the stop cannot also open above the target, but the
    # ordering is asserted so a future edit cannot quietly reverse it.
    bars = _fwd(opens=[92.0], highs=[110.0], lows=[91.0], closes=[105.0])
    assert _resolve(bars).reason is ExitReason.STOP


def test_no_gap_check_when_the_open_is_between_stop_and_target():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.0])
    assert _resolve(bars).reason is ExitReason.TIMEOUT


# ---------------------------------------------------------------------------
# Intraday stop and target
# ---------------------------------------------------------------------------


def test_intraday_stop_fills_at_the_stop_level():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[96.0], closes=[98.0])
    r = _resolve(bars)
    assert r.reason is ExitReason.STOP
    assert r.exit_price == pytest.approx(STOP)


def test_intraday_target_fills_at_the_target_level():
    bars = _fwd(opens=[100.0], highs=[105.0], lows=[99.0], closes=[104.5])
    r = _resolve(bars)
    assert r.reason is ExitReason.TARGET
    assert r.exit_price == pytest.approx(TARGET)


def test_stop_mode_fixed_uses_the_percentage_stop():
    ep = ExitParams(stop_mode="fixed", stop_fixed_pct=0.03)
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[96.5], closes=[97.5])
    r = _resolve(bars, ep=ep)
    assert r.reason is ExitReason.STOP
    assert r.exit_price == pytest.approx(97.0)


def test_stop_mode_none_lets_a_deep_drawdown_run_to_timeout():
    ep = ExitParams(stop_mode="none")
    bars = _fwd(opens=[100.0] * 5, highs=[101.0] * 5, lows=[80.0] * 5, closes=[85.0] * 5)
    r = _resolve(bars, ep=ep)
    assert r.reason is ExitReason.TIMEOUT


def test_stop_is_skipped_when_atr_is_null():
    # No fabricated stop level from a missing ATR (DESIGN §3.11).
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[80.0], closes=[85.0])
    r = exits.resolve_exit(
        entry_price=ENTRY,
        entry_idx=0,
        side=Side.LONG,
        fwd_bars=bars,
        fwd_ind=_ind(bars),
        atr_at_entry=np.nan,
        ep=EP,
    )
    assert r.reason is ExitReason.TIMEOUT


# ---------------------------------------------------------------------------
# Detail 3 — ambiguous on a stop-and-target collision (ADR 010)
# ---------------------------------------------------------------------------


def test_stop_wins_when_both_levels_breach_on_one_bar():
    bars = _fwd(opens=[100.0], highs=[105.0], lows=[96.0], closes=[104.0])
    r = _resolve(bars)
    assert r.reason is ExitReason.STOP
    assert r.exit_price == pytest.approx(STOP)


def test_ambiguous_is_set_when_both_levels_breach_on_one_bar():
    bars = _fwd(opens=[100.0], highs=[105.0], lows=[96.0], closes=[104.0])
    assert _resolve(bars).ambiguous is True


def test_ambiguous_is_not_set_on_a_clean_stop():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[96.0], closes=[98.0])
    assert _resolve(bars).ambiguous is False


def test_ambiguous_is_not_set_on_a_clean_target():
    bars = _fwd(opens=[100.0], highs=[105.0], lows=[99.0], closes=[104.5])
    assert _resolve(bars).ambiguous is False


def test_a_gapped_stop_is_not_ambiguous():
    # The fill happened at the open; nothing about the ordering is in doubt.
    bars = _fwd(opens=[92.0], highs=[110.0], lows=[91.0], closes=[105.0])
    assert _resolve(bars).ambiguous is False


# ---------------------------------------------------------------------------
# Detail 2 — band levels come from bar i-1 (DESIGN §5.5)
# ---------------------------------------------------------------------------


def test_upper_band_exit_uses_the_prior_bars_band_level():
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 103.0], lows=[99.0, 99.0], closes=[100.0, 102.0]
    )
    ind = _ind(bars)
    ind.iloc[0, ind.columns.get_loc("bb_upper")] = 102.0  # bar 0's band
    ind.iloc[1, ind.columns.get_loc("bb_upper")] = 200.0  # bar 1's own band
    r = _resolve(bars, ind=ind)
    # Bar 1's high of 103 clears bar 0's band of 102, so it fills there.
    # Reading bar 1's own band (200) would have produced a timeout.
    assert r.reason is ExitReason.UPPER_BAND
    assert r.exit_price == pytest.approx(102.0)


def test_the_first_forward_bar_uses_the_entry_bars_band_level():
    bars = _fwd(opens=[100.0], highs=[103.0], lows=[99.0], closes=[102.0])
    entry_ind = pd.Series({"bb_upper": 102.0, "bb_mid": 150.0, "bb_lower": 1.0, "k_full": 50.0})
    r = _resolve(bars, ind_at_entry=entry_ind)
    assert r.reason is ExitReason.UPPER_BAND
    assert r.exit_price == pytest.approx(102.0)


def test_the_first_forward_bar_has_no_band_exit_without_the_entry_indicator_row():
    # No i-1 row available means no band level, so the band exit is skipped
    # rather than substituting bar i's own level and re-introducing lookahead.
    bars = _fwd(opens=[100.0], highs=[103.0], lows=[99.0], closes=[102.0])
    ind = _ind(bars, bb_upper=102.0)
    assert _resolve(bars, ind=ind).reason is ExitReason.TIMEOUT


def test_gap_above_the_band_fills_at_the_open():
    bars = _fwd(
        opens=[100.0, 105.0], highs=[101.0, 106.0], lows=[99.0, 104.0], closes=[100.0, 105.5]
    )
    ind = _ind(bars, bb_upper=200.0)
    ind.iloc[0, ind.columns.get_loc("bb_upper")] = 102.0
    ep = ExitParams(target_pct=0.50)  # keep the target out of the way
    r = _resolve(bars, ind=ind, ep=ep)
    assert r.reason is ExitReason.UPPER_BAND
    assert r.exit_price == pytest.approx(105.0)


def test_upper_band_exit_can_be_disabled():
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 103.0], lows=[99.0, 99.0], closes=[100.0, 102.0]
    )
    ind = _ind(bars)
    ind.iloc[0, ind.columns.get_loc("bb_upper")] = 102.0
    r = _resolve(bars, ind=ind, ep=ExitParams(exit_on_upper_band=False))
    assert r.reason is ExitReason.TIMEOUT


# ---------------------------------------------------------------------------
# Mid-band exit — optional, default off (ADR 046)
# ---------------------------------------------------------------------------


def test_mid_band_exit_is_off_by_default():
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 103.0], lows=[99.0, 99.0], closes=[100.0, 102.0]
    )
    ind = _ind(bars, bb_mid=102.0, bb_upper=200.0)
    assert _resolve(bars, ind=ind).reason is ExitReason.TIMEOUT


def test_mid_band_exit_fires_when_enabled():
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 103.0], lows=[99.0, 99.0], closes=[100.0, 102.0]
    )
    ind = _ind(bars, bb_mid=102.0, bb_upper=200.0)
    r = _resolve(bars, ind=ind, ep=ExitParams(exit_on_mid_band=True))
    assert r.reason is ExitReason.MID_BAND
    assert r.exit_price == pytest.approx(102.0)


def test_mid_band_takes_priority_over_the_upper_band():
    # It is the nearer level, so it is reached first (ADR 046).
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 110.0], lows=[99.0, 99.0], closes=[100.0, 109.0]
    )
    ind = _ind(bars, bb_mid=102.0, bb_upper=106.0)
    ep = ExitParams(exit_on_mid_band=True, target_pct=0.50)
    r = _resolve(bars, ind=ind, ep=ep)
    assert r.reason is ExitReason.MID_BAND


def test_target_takes_priority_over_the_mid_band():
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 110.0], lows=[99.0, 99.0], closes=[100.0, 109.0]
    )
    ind = _ind(bars, bb_mid=106.0, bb_upper=200.0)
    r = _resolve(bars, ind=ind, ep=ExitParams(exit_on_mid_band=True))
    assert r.reason is ExitReason.TARGET


# ---------------------------------------------------------------------------
# Detail 4 — the stochastic exit is close-based and evaluated last
# ---------------------------------------------------------------------------


def test_stoch_80_exit_fills_at_that_bars_close():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    ind = _ind(bars, k_full=85.0)
    r = _resolve(bars, ind=ind)
    assert r.reason is ExitReason.STOCH_80
    assert r.exit_price == pytest.approx(100.5)


def test_stoch_80_reads_bar_i_not_bar_i_minus_1():
    # It is close-based, so it evaluates on the bar whose close it describes.
    bars = _fwd(
        opens=[100.0, 100.0], highs=[101.0, 101.0], lows=[99.0, 99.0], closes=[100.5, 100.7]
    )
    ind = _ind(bars, k_full=50.0)
    ind.iloc[1, ind.columns.get_loc("k_full")] = 85.0
    r = _resolve(bars, ind=ind)
    assert r.reason is ExitReason.STOCH_80
    assert r.exit_idx == 1


def test_an_intraday_stop_preempts_the_stochastic_exit_on_the_same_bar():
    # The stop filled during the session; a close-based signal cannot undo it.
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[96.0], closes=[100.5])
    ind = _ind(bars, k_full=85.0)
    assert _resolve(bars, ind=ind).reason is ExitReason.STOP


def test_an_intraday_target_preempts_the_stochastic_exit_on_the_same_bar():
    bars = _fwd(opens=[100.0], highs=[105.0], lows=[99.0], closes=[104.5])
    ind = _ind(bars, k_full=85.0)
    assert _resolve(bars, ind=ind).reason is ExitReason.TARGET


def test_stoch_exit_can_be_disabled():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    ind = _ind(bars, k_full=85.0)
    r = _resolve(bars, ind=ind, ep=ExitParams(exit_on_stoch_80=False))
    assert r.reason is ExitReason.TIMEOUT


def test_null_k_full_does_not_trigger_the_stochastic_exit():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    ind = _ind(bars, k_full=np.nan)
    assert _resolve(bars, ind=ind).reason is ExitReason.TIMEOUT


# ---------------------------------------------------------------------------
# Path metrics on the result (DESIGN §5.6)
# ---------------------------------------------------------------------------


def test_path_metrics_stop_at_the_exit_bar():
    # A 20% spike after the stop fired must not appear in MFE.
    bars = _fwd(opens=[100.0, 100.0], highs=[101.0, 120.0], lows=[96.0, 99.0], closes=[98.0, 119.0])
    r = _resolve(bars)
    assert r.exit_idx == 0
    assert r.mfe == pytest.approx(0.01)


def test_mae_reflects_the_worst_low_up_to_the_exit():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[96.0], closes=[98.0])
    assert _resolve(bars).mae == pytest.approx(-0.04)


def test_time_to_mfe_is_one_based():
    bars = _fwd(opens=[100.0] * 3, highs=[101.0, 103.0, 102.0], lows=[99.0] * 3, closes=[100.0] * 3)
    assert _resolve(bars).time_to_mfe == 2


def test_exit_date_matches_the_exit_bars_index_label():
    bars = _quiet()
    r = _resolve(bars)
    assert r.exit_date == bars.index[r.exit_idx].date()


# ---------------------------------------------------------------------------
# Short side — the sign flip (ADR 058)
# ---------------------------------------------------------------------------


def test_short_target_is_below_entry():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[95.0], closes=[96.0])
    r = _resolve(bars, side=Side.SHORT)
    assert r.reason is ExitReason.TARGET
    assert r.exit_price == pytest.approx(96.0)


def test_short_stop_is_above_entry():
    bars = _fwd(opens=[100.0], highs=[104.0], lows=[99.0], closes=[103.5])
    r = _resolve(bars, side=Side.SHORT)
    assert r.reason is ExitReason.STOP
    assert r.exit_price == pytest.approx(103.0)


def test_short_gaps_through_the_stop_at_the_open():
    bars = _fwd(opens=[108.0], highs=[109.0], lows=[107.0], closes=[108.5])
    r = _resolve(bars, side=Side.SHORT)
    assert r.reason is ExitReason.STOP
    assert r.exit_price == pytest.approx(108.0)


def test_short_exits_on_the_lower_band():
    bars = _fwd(opens=[100.0, 100.0], highs=[101.0, 101.0], lows=[99.0, 97.0], closes=[100.0, 98.0])
    ind = _ind(bars, bb_lower=1.0)
    ind.iloc[0, ind.columns.get_loc("bb_lower")] = 98.0
    ep = ExitParams(target_pct=0.50)
    r = _resolve(bars, ind=ind, ep=ep, side=Side.SHORT)
    assert r.reason is ExitReason.UPPER_BAND  # the far band, mirrored
    assert r.exit_price == pytest.approx(98.0)


def test_short_exits_on_stochastic_oversold():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[99.5])
    ind = _ind(bars, k_full=15.0)
    r = _resolve(bars, ind=ind, side=Side.SHORT)
    assert r.reason is ExitReason.STOCH_80


def test_short_mfe_is_positive_when_price_falls():
    bars = _fwd(opens=[100.0] * 2, highs=[101.0] * 2, lows=[98.0] * 2, closes=[99.0] * 2)
    r = _resolve(bars, side=Side.SHORT)
    assert r.mfe == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_an_empty_forward_window_raises():
    with pytest.raises(ValueError):
        _resolve(_fwd(opens=[], highs=[], lows=[], closes=[]))


def test_an_unknown_stop_mode_raises():
    with pytest.raises(ValueError):
        _resolve(_quiet(), ep=ExitParams(stop_mode="trailing"))


# ---------------------------------------------------------------------------
# No magic numbers (CLAUDE.md invariant 9)
# ---------------------------------------------------------------------------


def test_stoch_exit_thresholds_are_exit_params():
    """ADR 092: the levels are exit policy, so they live on ExitParams.

    Independent of SignalParams.stoch_overbought even though the long side
    defaults to the same 80.0 — an exit-rule sweep must not require a
    signal-config change.
    """
    assert ExitParams().exit_stoch_threshold == 80.0
    assert ExitParams().exit_stoch_threshold_short == 20.0


def test_the_two_sides_are_independently_sweepable():
    """ADR 016: the short is not a mirror of the long.

    Deriving the short level as `100 - long` makes this combination
    unreachable, so every sweep cell would move two things at once and the
    Phase 4 long-vs-short asymmetry comparison would be biased before
    anyone looked at it.
    """
    ep = ExitParams(exit_stoch_threshold=70.0, exit_stoch_threshold_short=20.0)
    # Long exits at 70 ...
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    assert _resolve(bars, ind=_ind(bars, k_full=72.0), ep=ep).reason is ExitReason.STOCH_80
    # ... while the short still exits at 20, not at 30.
    short_bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[99.5])
    r = _resolve(short_bars, ind=_ind(short_bars, k_full=25.0), ep=ep, side=Side.SHORT)
    assert r.reason is ExitReason.TIMEOUT
    r2 = _resolve(short_bars, ind=_ind(short_bars, k_full=19.0), ep=ep, side=Side.SHORT)
    assert r2.reason is ExitReason.STOCH_80


def test_sweeping_the_long_threshold_leaves_the_short_untouched():
    ep = ExitParams(exit_stoch_threshold=60.0)
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[99.5])
    # A short at %K=35 must not exit just because the long level moved to 60.
    assert _resolve(bars, ind=_ind(bars, k_full=35.0), ep=ep, side=Side.SHORT).reason is (
        ExitReason.TIMEOUT
    )


def test_sweeping_the_short_threshold_leaves_the_long_untouched():
    ep = ExitParams(exit_stoch_threshold_short=40.0)
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    assert _resolve(bars, ind=_ind(bars, k_full=65.0), ep=ep).reason is ExitReason.TIMEOUT
    # ... but the short now exits at 40.
    sb = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[99.5])
    assert _resolve(sb, ind=_ind(sb, k_full=38.0), ep=ep, side=Side.SHORT).reason is (
        ExitReason.STOCH_80
    )


def test_the_exit_path_derives_no_threshold_from_the_other_side():
    """The `100 - threshold` coupling must not come back."""
    import inspect

    source = inspect.getsource(exits)
    assert "100.0 - ep." not in source and "100 - ep." not in source
    assert "_STOCH_SCALE" not in source


def test_a_swept_exit_threshold_changes_the_exit():
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    ind = _ind(bars, k_full=72.0)
    assert _resolve(bars, ind=ind).reason is ExitReason.TIMEOUT
    ep = ExitParams(exit_stoch_threshold=70.0)
    assert _resolve(bars, ind=ind, ep=ep).reason is ExitReason.STOCH_80


def test_the_exit_threshold_is_independent_of_signal_params():
    """Sweeping SignalParams must not move the exit, and vice versa."""
    from capitalscan.core.config import SignalParams

    assert SignalParams(stoch_overbought=60.0).stoch_overbought == 60.0
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    # Exit still uses ExitParams' 80.0, unmoved by the signal config.
    assert _resolve(bars, ind=_ind(bars, k_full=65.0)).reason is ExitReason.TIMEOUT


def test_exits_module_contains_no_bare_stochastic_literal():
    """ADR 092's enforcement, replaced (Session 14 task 14.5).

    The substring check this replaced (`"80.0" not in body and "20.0" not
    in body`) covered one module and two exact float spellings — ADR 095
    found it blind to `db/schema.sql`'s `(s.k_full >= (80)::numeric)`, which
    is neither substring. `capitalscan.jobs.threshold_lint` is the real
    matcher: a numeric literal on either side of a comparison operator
    against a threshold-bearing column (`core/config.py::THRESHOLD_COLUMNS`
    equivalent, named once in `threshold_lint.THRESHOLD_COLUMNS`). See
    `test_threshold_lint.py` for the matcher's own test suite, including the
    regression test proving it catches the ADR 095 SQL spelling this
    substring check missed.
    """
    import inspect
    from pathlib import Path

    from capitalscan.jobs import threshold_lint

    source = inspect.getsource(exits)
    findings = threshold_lint.scan_python_source(source, path=Path(exits.__file__))
    assert findings == []


def test_long_stoch_exit_fires_exactly_at_the_configured_level():
    level = ExitParams().exit_stoch_threshold
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[100.5])
    assert _resolve(bars, ind=_ind(bars, k_full=level)).reason is ExitReason.STOCH_80
    assert _resolve(bars, ind=_ind(bars, k_full=level - 0.01)).reason is ExitReason.TIMEOUT


def test_short_stoch_exit_fires_exactly_at_the_configured_level():
    level = ExitParams().exit_stoch_threshold_short
    bars = _fwd(opens=[100.0], highs=[101.0], lows=[99.0], closes=[99.5])
    r = _resolve(bars, ind=_ind(bars, k_full=level), side=Side.SHORT)
    assert r.reason is ExitReason.STOCH_80
    r2 = _resolve(bars, ind=_ind(bars, k_full=level + 0.01), side=Side.SHORT)
    assert r2.reason is ExitReason.TIMEOUT
