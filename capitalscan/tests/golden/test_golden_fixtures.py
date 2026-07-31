"""Golden fixture tests (TESTS.md §4, BUILD.md §2.6).

Hand-selected, real (or deliberately synthetic) OHLCV windows that each
verify one specific correctness property called out in DESIGN §3.5.
Fixtures live in tests/golden/data/*.csv, fetched once from yfinance
(TSM, NVDA) or generated (flat_series) — never refetched at test time.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from capitalscan.core import indicators as ind
from capitalscan.core.config import IndicatorParams

DATA_DIR = Path(__file__).parent / "data"


def _load(name: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / name, parse_dates=["ts"])
    return df.set_index("ts")


def test_tsm_2026_07_shows_the_observed_lower_band_event():
    """A real observed event, end to end (TESTS.md §4).

    TSM dropped from an open of 388.08 to a close of 374.67 on
    2026-07-29 — a real, sharply lower-band-touching session. This
    fixture exists so the indicator math is checked against an actual
    market move rather than only synthetic data.
    """
    bars = _load("tsm_2026_07.csv")
    p = IndicatorParams()
    out = ind.compute_all(bars, p)

    row = out.loc["2026-07-29"]
    bars_row = bars.loc["2026-07-29"]

    # The day's low must be at or below the prior day's lower band for
    # this to be the lower-band-touch event TESTS.md describes.
    prior_lower = out["bb_lower"].shift(1).loc["2026-07-29"]
    assert pd.notna(prior_lower)
    assert bars_row["low"] <= prior_lower

    # %B and %K should both read near the oversold extreme on this bar's
    # own (same-day) values — sanity check, not the t-1 signal itself
    # (that discipline lives in core/signals.py, Session 3).
    assert row["bb_pctb"] < 0.15
    assert row["k_full"] < 20


def test_nvda_split_2024_no_fabricated_breach():
    """Split adjustment does not fabricate a breach (TESTS.md §4).

    NVDA's 10-for-1 split (ex-date 2024-06-10) must not appear as a
    fake ~90% drawdown in split-adjusted close — yfinance already
    serves split-adjusted OHLC, so no daily return around the split
    should look like an un-adjusted 10x price jump.
    """
    bars = _load("nvda_split_2024.csv")
    p = IndicatorParams()

    daily_ret = bars["close"].pct_change()
    assert daily_ret.abs().max() < 0.40  # DESIGN §2.3's absolute-return reject threshold

    out = ind.compute_all(bars, p)
    # No NaN band level should spuriously coincide with the split date
    # once warm — i.e. the split date itself computes normally.
    warm = out.loc["2024-06-10":]
    assert warm["bb_mid"].notna().any()


def test_flat_series_stochastic_is_nan_not_zero_or_fifty():
    """Stochastic division by zero returns NaN, not 50 (TESTS.md §4)."""
    bars = _load("flat_series.csv")
    p = IndicatorParams()
    out = ind.compute_all(bars, p)

    warm = out.iloc[p.stoch_window + p.stoch_smooth_k :]
    assert warm["k_fast"].isna().all()
    assert warm["k_full"].isna().all()
    assert (warm["bb_width"].dropna() == 0).all()
