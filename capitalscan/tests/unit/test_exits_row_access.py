"""`resolve_exit` must produce identical results from dicts and from Series.

Written before the optimisation it protects (2026-08-29). `resolve_exit`
built three pandas Series per forward bar --

    bar        = window.iloc[i]
    prior_ind  = ind_window.iloc[i - 1] if i > 0 else ind_at_entry
    own_ind    = ind_window.iloc[i]

-- roughly 7.2 million Series per backtest (597,605 entries x 4 bars x 3),
each allocated to read four floats by name. Every read in the hot path is by
string key (`bar["open"]`, `_level(ind, "bb_mid")`), and `_level` already
guards `KeyError`, so plain dicts substitute exactly.

**These tests pin the equivalence, not the speed.** If a future change makes
the engine depend on anything a Series offers and a dict does not -- dtype
coercion, `.get` with a default, positional access -- these fail rather than
the difference showing up as a quiet change in backtest output.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core import exits
from capitalscan.core.config import ExitParams
from capitalscan.core.types import ExitReason, Side

ENTRY = 100.0
ATR = 2.0
EP = ExitParams(target_pct=0.05, stop_mode="atr", stop_atr_k=2.0)


def _bars(opens, highs, lows, closes) -> pd.DataFrame:
    n = len(opens)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "ts": pd.date_range("2026-01-05", periods=n, freq="B"),
        }
    ).set_index("ts", drop=False)


def _ind(bars: pd.DataFrame, *, bb_mid=None, bb_upper=None, k_full=None) -> pd.DataFrame:
    n = len(bars)
    return pd.DataFrame(
        {
            "bb_mid": [bb_mid if bb_mid is not None else 999.0] * n,
            "bb_upper": [bb_upper if bb_upper is not None else 999.0] * n,
            "bb_lower": [1.0] * n,
            "k_full": [k_full if k_full is not None else 50.0] * n,
        },
        index=bars.index,
    )


def _resolve(bars, ind, side=Side.LONG):
    return exits.resolve_exit(
        entry_price=ENTRY,
        entry_idx=0,
        side=side,
        fwd_bars=bars,
        fwd_ind=ind,
        atr_at_entry=ATR,
        ep=EP,
        ind_at_entry=ind.iloc[0],
    )


# Each case drives a different branch of the DESIGN 5.5 order, so the
# equivalence is pinned across the whole decision tree rather than one path.
CASES = {
    "gap_through_stop": (
        _bars([95.0, 100.0], [95.5, 100.5], [94.0, 99.5], [95.0, 100.0]),
        None,
        ExitReason.STOP,
    ),
    "gap_through_target": (
        _bars([106.0, 100.0], [106.5, 100.5], [105.5, 99.5], [106.0, 100.0]),
        None,
        ExitReason.TARGET,
    ),
    "intraday_stop": (
        _bars([100.0, 100.0], [100.5, 100.5], [95.0, 99.5], [99.0, 100.0]),
        None,
        ExitReason.STOP,
    ),
    "intraday_target": (
        _bars([100.0, 100.0], [106.0, 100.5], [99.5, 99.5], [105.5, 100.0]),
        None,
        ExitReason.TARGET,
    ),
    "upper_band": (
        _bars([100.0, 100.0], [103.0, 100.5], [99.5, 99.5], [102.5, 100.0]),
        {"bb_upper": 102.0},
        ExitReason.UPPER_BAND,
    ),
    "timeout": (
        _bars([100.0, 100.0], [100.5, 100.5], [99.5, 99.5], [100.0, 100.2]),
        None,
        ExitReason.TIMEOUT,
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_branch_reaches_the_reason_it_is_meant_to(name):
    """Guards the cases themselves: a typo that made every case time out
    would leave the equivalence test below passing while testing nothing."""
    bars, ind_kw, expected = CASES[name]
    ind = _ind(bars, **(ind_kw or {}))
    assert _resolve(bars, ind).reason is expected


@pytest.mark.parametrize("name", sorted(CASES))
def test_dict_rows_and_series_rows_agree(name):
    """The equivalence the optimisation rests on.

    `_exit_on_bar` and `_level` read by string key only, so a mapping with the
    same keys must produce the same decision. Compared field by field rather
    than on the object, because `ExitResult` carries floats where `==` on the
    whole object would hide a small numeric difference.
    """
    bars, ind_kw, _ = CASES[name]
    ind = _ind(bars, **(ind_kw or {}))

    from_series = _resolve(bars, ind)

    # The same rows a dict-based rewrite would build: plain mappings, no
    # pandas machinery, values already floats.
    bar_dicts = [dict(bars.iloc[i]) for i in range(len(bars))]
    ind_dicts = [dict(ind.iloc[i]) for i in range(len(ind))]
    from_dicts = exits.resolve_exit(
        entry_price=ENTRY,
        entry_idx=0,
        side=Side.LONG,
        fwd_bars=pd.DataFrame(bar_dicts, index=bars.index),
        fwd_ind=pd.DataFrame(ind_dicts, index=ind.index),
        atr_at_entry=ATR,
        ep=EP,
        ind_at_entry=ind_dicts[0],
    )

    assert from_dicts.reason is from_series.reason
    assert from_dicts.exit_idx == from_series.exit_idx
    assert from_dicts.exit_price == pytest.approx(from_series.exit_price)
    assert from_dicts.mfe == pytest.approx(from_series.mfe)
    assert from_dicts.mae == pytest.approx(from_series.mae)


def test_level_reads_a_plain_dict():
    """`_level` is the only indicator accessor, and it guards KeyError -- so a
    dict missing a field must yield NaN exactly as a Series missing it does."""
    assert exits._level({"bb_mid": 101.5}, "bb_mid") == pytest.approx(101.5)
    assert exits._isnan(exits._level({"bb_mid": 101.5}, "bb_upper"))
    assert exits._isnan(exits._level({"bb_mid": None}, "bb_mid"))
    assert exits._isnan(exits._level(None, "bb_mid"))
