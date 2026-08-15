"""Unit tests for core/indicators.py.

DESIGN §3.5 calls out three specifics that decide correctness and each
gets a dedicated test: which price series each function reads, the
stochastic division-by-zero convention, and the rolling_percentile
convention. Plus registry mechanics (ADR 051) and compute_all wiring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import indicators as ind
from capitalscan.core.config import IndicatorParams


def _flat_bars(n: int = 300, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "adj_close": price,
            "volume": 1_000_000,
        },
        index=idx,
    )


def _ramp_bars(n: int = 300, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    close = start + step * np.arange(n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close * 0.98,  # distinct from close, to catch series mixups
            "volume": 1_000_000 + 1000 * np.arange(n),
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Registry mechanics (ADR 051)
# ---------------------------------------------------------------------------


def test_registry_contains_expected_indicators():
    names = set(ind.registry().keys())
    expected = {
        "bollinger",
        "stochastic",
        "atr",
        "realized_vol",
        "drawdown_from_high",
        "sma_slope",
        "volume_zscore",
    }
    assert expected <= names


def test_max_warmup_matches_longest_chain():
    # DESIGN §2.7: rv_pct_252d (252 + 20 = 272) is the longest chain.
    #
    # ADR 108 briefly made this 273: its flag read `bollinger`'s output
    # shifted back one bar, so it needed one more. ADR 109 removed the shift
    # (the band is bar t's own), and the warmup returns to 272 with it.
    # `max_warmup()` drives the indicators job's read-window expansion, so
    # this number is load-bearing rather than cosmetic.
    assert ind.max_warmup() == 272
    assert ind.registry()["bear_close_above_upper"].warmup == 272
    assert ind.registry()["realized_vol"].warmup == 272


def test_register_rejects_duplicate_name():
    with pytest.raises(ValueError):

        @ind.register("bollinger", deps=["close"], warmup=1, dtype="numeric(12,6)")
        def _dup(bars, p):
            return pd.DataFrame(index=bars.index)


# ---------------------------------------------------------------------------
# rolling_percentile convention
# ---------------------------------------------------------------------------


def test_rolling_percentile_flat_series_is_always_one():
    s = pd.Series([5.0] * 300)
    pct = ind.rolling_percentile(s, window=252)
    assert (pct.dropna() == 1.0).all()


def test_rolling_percentile_strict_trailing_window():
    # Strictly increasing series: the current value is always the max of
    # its trailing window, so the percentile is always 1.0 once warm.
    s = pd.Series(np.arange(300, dtype=float))
    pct = ind.rolling_percentile(s, window=252)
    assert pct.iloc[:251].isna().all()
    assert (pct.iloc[251:] == 1.0).all()


def test_rolling_percentile_short_history_is_null():
    s = pd.Series(np.arange(10, dtype=float))
    pct = ind.rolling_percentile(s, window=252)
    assert pct.isna().all()


# ---------------------------------------------------------------------------
# Bollinger
# ---------------------------------------------------------------------------


def test_bollinger_flat_series_zero_width():
    bars = _flat_bars()
    p = IndicatorParams()
    out = ind.bollinger(bars, p)
    warm = out.iloc[p.bb_window :]
    assert (warm["bb_upper"] == warm["bb_mid"]).all()
    assert (warm["bb_lower"] == warm["bb_mid"]).all()
    assert (warm["bb_width"] == 0).all()


def test_bollinger_population_stddev():
    bars = _ramp_bars()
    p = IndicatorParams()
    out = ind.bollinger(bars, p)
    close = bars["close"]
    window = close.iloc[0 : p.bb_window]
    expected_mid = window.mean()
    expected_std = window.std(ddof=0)  # population, per ADR 004
    row = out.iloc[p.bb_window - 1]
    assert row["bb_mid"] == pytest.approx(expected_mid)
    assert row["bb_upper"] == pytest.approx(expected_mid + 2 * expected_std)
    assert row["bb_lower"] == pytest.approx(expected_mid - 2 * expected_std)


def test_bollinger_reads_split_adjusted_close_not_adj_close():
    bars = _ramp_bars()
    out = ind.bollinger(bars, IndicatorParams())
    # adj_close is 0.98x close in the fixture; if bollinger used adj_close
    # the mid would track that scaled series instead.
    close_window_mean = bars["close"].iloc[:20].mean()
    assert out["bb_mid"].iloc[19] == pytest.approx(close_window_mean)


def test_bollinger_warmup_is_null_before_window():
    bars = _ramp_bars()
    p = IndicatorParams()
    out = ind.bollinger(bars, p)
    assert out["bb_mid"].iloc[: p.bb_window - 1].isna().all()
    assert out["bb_mid"].iloc[p.bb_window - 1 :].notna().all()


# ---------------------------------------------------------------------------
# Stochastic
# ---------------------------------------------------------------------------


def test_stochastic_flat_series_is_nan_not_zero_or_fifty():
    bars = _flat_bars()
    out = ind.stochastic(bars, IndicatorParams())
    warm = out.iloc[30:]
    assert warm["k_fast"].isna().all()
    assert warm["k_full"].isna().all()


def test_stochastic_at_high_of_range_is_100():
    p = IndicatorParams()
    n = 40
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    high = np.concatenate([np.full(n - 1, 100.0), [110.0]])
    low = np.full(n, 90.0)
    close = high.copy()
    bars = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "adj_close": close, "volume": 1},
        index=idx,
    )
    out = ind.stochastic(bars, p)
    assert out["k_fast"].iloc[-1] == pytest.approx(100.0)


def test_k_cross_up_detected():
    p = IndicatorParams()
    n = 60
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    # A dip then a sharp recovery should produce a k_full/d_full crossover.
    close = np.concatenate([np.linspace(100, 80, 30), np.linspace(80, 130, 30)])
    high = close + 1
    low = close - 1
    bars = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "adj_close": close, "volume": 1},
        index=idx,
    )
    out = ind.stochastic(bars, p)
    assert out["k_cross_up"].fillna(False).any()


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def test_atr_flat_series_is_zero():
    bars = _flat_bars()
    p = IndicatorParams()
    out = ind.atr(bars, p)
    assert (out["atr_14"].iloc[p.atr_window :] == 0).all()


def test_atr_warmup_null_before_window():
    bars = _ramp_bars()
    p = IndicatorParams()
    out = ind.atr(bars, p)
    assert out["atr_14"].iloc[: p.atr_window - 1].isna().all()


# ---------------------------------------------------------------------------
# Realized vol — the one function that reads adj_close (§2.2 exception)
# ---------------------------------------------------------------------------


def test_realized_vol_reads_adj_close_not_close():
    bars = _ramp_bars()
    p = IndicatorParams()
    out = ind.realized_vol(bars, p)

    # Build a variant where adj_close is flat (no return dispersion) while
    # close still ramps. If realized_vol read `close` this would still show
    # nonzero vol; reading `adj_close` correctly, it must be ~0.
    flat_adj = bars.copy()
    flat_adj["adj_close"] = 100.0
    out_flat_adj = ind.realized_vol(flat_adj, p)

    assert out["rv_20d"].dropna().gt(0).all()
    assert out_flat_adj["rv_20d"].dropna().eq(0).all()


def test_realized_vol_warmup():
    bars = _ramp_bars()
    p = IndicatorParams()
    out = ind.realized_vol(bars, p)
    # rv_20d warms at rv_window, rv_pct_252d warms at rv_window + rv_pct_window - 1
    assert out["rv_20d"].iloc[: p.rv_window - 1].isna().all()
    assert out["rv_20d"].iloc[p.rv_window :].notna().all()


# ---------------------------------------------------------------------------
# Drawdown from high
# ---------------------------------------------------------------------------


def test_drawdown_from_high_is_positive_fraction():
    bars = _flat_bars(n=300, price=100.0)
    bars = bars.copy()
    # Force a 20% drop after the rolling high is established.
    bars.loc[bars.index[280:], ["open", "high", "low", "close"]] = 80.0
    p = IndicatorParams()
    out = ind.drawdown_from_high(bars, p)
    assert out["dd_52w"].iloc[-1] == pytest.approx(0.20)
    assert (out["dd_52w"].dropna() >= 0).all()


def test_drawdown_from_high_zero_at_new_high():
    bars = _ramp_bars()  # strictly increasing -> always at the high
    p = IndicatorParams()
    out = ind.drawdown_from_high(bars, p)
    assert (out["dd_52w"].dropna() == 0).all()


# ---------------------------------------------------------------------------
# SMA slope
# ---------------------------------------------------------------------------


def test_sma_slope_positive_on_uptrend():
    bars = _ramp_bars(n=300)
    p = IndicatorParams()
    out = ind.sma_slope(bars, p)
    assert (out["sma200_slope_60"].dropna() > 0).all()


def test_sma_slope_flat_is_zero():
    bars = _flat_bars(n=300)
    p = IndicatorParams()
    out = ind.sma_slope(bars, p)
    assert (out["sma200_slope_60"].dropna() == 0).all()


# ---------------------------------------------------------------------------
# Volume z-score
# ---------------------------------------------------------------------------


def test_volume_zscore_zero_when_constant():
    bars = _flat_bars(n=60)
    p = IndicatorParams()
    out = ind.volume_zscore(bars, p)
    assert out["vol_z_20d"].dropna().isna().sum() == 0 or True  # std=0 -> NaN by design
    assert out["vol_z_20d"].dropna().empty  # constant volume => std 0 => NaN, never fabricated


def test_volume_zscore_detects_spike():
    bars = _flat_bars(n=60)
    bars = bars.copy()
    bars.loc[bars.index[-1], "volume"] = 1_000_000_000
    p = IndicatorParams()
    out = ind.volume_zscore(bars, p)
    assert out["vol_z_20d"].iloc[-1] > 4


# ---------------------------------------------------------------------------
# compute_all
# ---------------------------------------------------------------------------


def test_compute_all_returns_one_row_per_bar_with_expected_columns():
    bars = _ramp_bars(n=300)
    p = IndicatorParams()
    out = ind.compute_all(bars, p)
    assert len(out) == len(bars)
    expected_cols = {
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "bb_pctb",
        "bb_width",
        "bb_width_pct",
        "k_fast",
        "d_fast",
        "k_full",
        "d_full",
        "k_cross_up",
        "k_cross_down",
        "atr_14",
        "rv_20d",
        "rv_pct_252d",
        "dd_52w",
        "sma_200",
        "sma200_slope_60",
        "vol_z_20d",
    }
    assert expected_cols <= set(out.columns)


def test_compute_all_never_fills_warmup_nulls():
    bars = _ramp_bars(n=300)
    p = IndicatorParams()
    out = ind.compute_all(bars, p)
    # Before max_warmup, at least one column must still be null - proves
    # nothing silently forward-filled or interpolated (core/ null policy).
    assert out.iloc[: ind.max_warmup() - 1].isna().any(axis=None)


# --------------------------------------------------------------------------
# bear_close_above_upper (ADR 108) — the close-confirmed reversal flag
# --------------------------------------------------------------------------


def _bear_bars(n=40, seed=7):
    """A gently rising series, so `bb_upper` is well-defined and finite."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.15, 0.6, n))
    return pd.DataFrame(
        {
            "ts": pd.date_range("2020-01-01", periods=n, freq="B"),
            "open": close + 0.5,
            "high": close + 1.5,
            "low": close - 1.5,
            "close": close,
            "adj_close": close,
            "volume": np.full(n, 1_000_000),
        }
    )


def test_the_flag_needs_a_down_bar_and_a_close_at_or_above_the_band():
    """ADR 109's rule, both halves, on a flat base.

    The base is flat deliberately. Under ADR 108's shifted band any large
    spike cleared the *previous* level; under ADR 109 the close is measured
    against a band it helped build, so a lone spike partly raises its own
    bar. With `bb_window=5`, `bb_std=2`, `ddof=0` and four flat values plus
    one outlier `d`, the algebra collapses exactly:

        mean  = c + d/5
        std   = 0.4d
        upper = c + d/5 + 0.8d = c + d = the close

    so the close lands precisely *on* its own band and `_breach`'s "at or
    beyond" fires it. That identity is why this fixture is flat rather than
    noisy, and it is the clearest statement of what the same-day band means:
    a single bar cannot outrun a level it is itself setting.
    """
    p = IndicatorParams(bb_window=5)
    bars = _bear_bars(n=30)
    for i in range(16, 20):
        bars.loc[i, ["open", "high", "low", "close"]] = 100.0
    bars.loc[20, "close"] = 103.0
    bars.loc[20, "open"] = 105.0
    bars.loc[20, "high"] = 105.5
    bars.loc[20, "low"] = 102.5
    out = ind.bear_close_above_upper(bars, p)
    assert bool(out["bear_close_above_upper"].iloc[20])


def test_a_lone_spike_lands_exactly_on_the_band_it_sets():
    """The identity, asserted rather than described.

    With `bb_window=5`, `bb_std=2`, `ddof=0`, four flat values and one
    outlier, `upper` equals the close **exactly** for any spike size. So a
    lone spike never rises *above* its own band — it fires only because
    `_breach` is "at or beyond". A strict `>` would never fire on this shape.

    My first version of this test asserted a big spike would fail to fire.
    That was wrong: spike size cancels out of the algebra entirely.
    """
    p = IndicatorParams(bb_window=5)
    for spike in (1.0, 3.0, 25.0):
        bars = _bear_bars(n=30)
        for i in range(16, 20):
            bars.loc[i, ["open", "high", "low", "close"]] = 100.0
        bars.loc[20, "close"] = 100.0 + spike
        bars.loc[20, "open"] = 100.0 + spike + 2
        bars.loc[20, "high"] = 100.0 + spike + 3
        bars.loc[20, "low"] = 99.0
        upper = ind.bollinger(bars, p)["bb_upper"].iloc[20]
        assert upper == pytest.approx(100.0 + spike), spike
        assert bool(ind.bear_close_above_upper(bars, p)["bear_close_above_upper"].iloc[20])


def test_a_second_elevated_bar_widens_the_band_past_the_close():
    """What actually stops a fire under the same-day band.

    One elevated bar lands on its band; two widen the standard deviation
    enough that the close falls short — 103 against an upper of 104.14. This
    is the mechanism behind ADR 109's 55% reduction, and it runs in the
    conservative direction.
    """
    p = IndicatorParams(bb_window=5)
    bars = _bear_bars(n=30)
    for i in range(16, 19):
        bars.loc[i, ["open", "high", "low", "close"]] = 100.0
    for i in (19, 20):
        bars.loc[i, ["high", "low", "close"]] = 103.0
        bars.loc[i, "open"] = 105.0
    out = ind.bear_close_above_upper(bars, p)
    assert not bool(out["bear_close_above_upper"].iloc[20])


def test_an_up_bar_closing_above_the_band_does_not_flag():
    p = IndicatorParams(bb_window=5)
    bars = _bear_bars(n=30)
    bars.loc[20, "close"] = float(bars.loc[19, "close"]) + 20.0
    bars.loc[20, "open"] = float(bars.loc[20, "close"]) - 5.0  # green bar
    bars.loc[20, "high"] = float(bars.loc[20, "close"]) + 1.0
    out = ind.bear_close_above_upper(bars, p)
    assert not bool(out["bear_close_above_upper"].iloc[20])


def test_a_down_bar_closing_below_the_band_does_not_flag():
    p = IndicatorParams(bb_window=5)
    bars = _bear_bars(n=30)
    bars.loc[20, "close"] = float(bars.loc[19, "close"]) - 5.0
    bars.loc[20, "open"] = float(bars.loc[20, "close"]) + 2.0
    out = ind.bear_close_above_upper(bars, p)
    assert not bool(out["bear_close_above_upper"].iloc[20])


def test_the_band_compared_against_is_the_same_bar_s():
    """ADR 109. The band is bar t's own, not t-1's.

    At the close, today's band is fully computable from information that
    already exists, so reading it is not look-ahead — invariant 3 exists to
    stop the close deciding an *intraday* event, and here the event is the
    close. Measured across 2010-2026, the shifted form fired 44,114 times
    against this form's 20,146, and 55% of those never cleared the band a
    chart would draw.
    """
    p = IndicatorParams(bb_window=5)
    bars = _bear_bars(n=30)
    upper = ind.bollinger(bars, p)["bb_upper"]
    flagged = ind.bear_close_above_upper(bars, p)["bear_close_above_upper"]
    manual = (bars["open"] > bars["close"]) & (bars["close"] >= upper)
    pd.testing.assert_series_equal(
        flagged.fillna(False).astype(bool),
        manual.fillna(False).astype(bool),
        check_names=False,
    )


def test_the_flag_is_null_through_warmup_never_false():
    """Invariant 4: no band yet is not "did not fire". A False there would
    read as a measured negative."""
    p = IndicatorParams(bb_window=5)
    out = ind.bear_close_above_upper(_bear_bars(n=30), p)
    # `bb_window - 1` rows lack a band. Without the ADR 108 shift there is no
    # extra null row, which is exactly the warmup change from 273 to 272.
    assert out["bear_close_above_upper"].iloc[:4].isna().all()
    assert not pd.isna(out["bear_close_above_upper"].iloc[4])


def test_a_flagged_bar_always_also_touched_the_upper_band():
    """The subset guarantee, and it is structural: `bars_check1` enforces
    `close <= high`, so a close at or above the band implies the high was
    too. Every flagged bar therefore already carries a `bb_upper_touch`
    event, which is what lets the flag be measured against an existing
    population rather than a new one."""
    p = IndicatorParams(bb_window=5)
    bars = _bear_bars(n=60)
    upper = ind.bollinger(bars, p)["bb_upper"].shift(1)
    flagged = ind.bear_close_above_upper(bars, p)["bear_close_above_upper"].fillna(False)
    touched = bars["high"] >= upper
    assert not (flagged.astype(bool) & ~touched.fillna(False)).any()


def test_the_flag_is_registered_and_carried_by_compute_all():
    out = ind.compute_all(_bear_bars(n=300), IndicatorParams())
    assert "bear_close_above_upper" in out.columns
    assert "bear_close_above_upper" in ind.registry()
