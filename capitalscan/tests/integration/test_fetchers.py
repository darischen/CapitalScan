"""Integration test for the fetcher layer (BUILD session 4 acceptance).

Mocks the network boundary (`yfinance.download` / `yfinance.Ticker`)
rather than hitting Yahoo for real, so the suite is deterministic and
doesn't depend on network availability or on Yahoo's live data changing
under it. What it exercises end to end is the real thing the acceptance
criterion cares about: batching three tickers through one fetch, a cache
hit on the second call, and the rate limiter being invoked on every
network-bound call.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs.fetch import base as fetch_base
from capitalscan.jobs.fetch import yahoo

TICKERS = ["TSM", "NVDA", "AAPL"]
START = date(2026, 1, 2)
END = date(2026, 1, 9)


def _synthetic_daily_frame(tickers: list[str]) -> pd.DataFrame:
    """A MultiIndex frame shaped like `yf.download(..., group_by='ticker')`."""
    idx = pd.date_range(START, periods=5, freq="B")
    import numpy as np

    offsets = np.arange(5, dtype=float)
    frames = {}
    for i, ticker in enumerate(tickers):
        base_price = 100.0 + i * 10
        frames[ticker] = pd.DataFrame(
            {
                "Open": base_price + offsets,
                "High": base_price + 1 + offsets,
                "Low": base_price - 1 + offsets,
                "Close": base_price + 0.5 + offsets,
                "Adj Close": base_price + 0.5 + offsets,
                "Volume": [1_000_000] * 5,
            },
            index=idx,
        )
    return pd.concat(frames, axis=1)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_base, "CACHE_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Rate limiting and retry backoff both call time.sleep; keep the test
    # fast without disabling the throttle logic itself.
    monkeypatch.setattr(fetch_base.time, "sleep", lambda seconds: None)


def test_fetch_bars_daily_three_tickers(monkeypatch):
    calls = {"n": 0}

    def fake_download(tickers, **kwargs):
        calls["n"] += 1
        return _synthetic_daily_frame(list(tickers))

    monkeypatch.setattr(yahoo.yf, "download", fake_download)

    result = yahoo.fetch_bars_daily(TICKERS, START, END)

    assert calls["n"] == 1  # one batch of three tickers, under the 50-ticker cap
    assert set(result["ticker"]) == set(TICKERS)
    assert list(result.columns) == [
        "ticker",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
        "adj_factor",
    ]
    assert (result["adj_factor"] == 1.0).all()  # no split in this synthetic window
    assert len(result) == 5 * len(TICKERS)


def test_second_call_is_a_cache_hit(monkeypatch):
    calls = {"n": 0}

    def fake_download(tickers, **kwargs):
        calls["n"] += 1
        return _synthetic_daily_frame(list(tickers))

    monkeypatch.setattr(yahoo.yf, "download", fake_download)

    first = yahoo.fetch_bars_daily(TICKERS, START, END)
    second = yahoo.fetch_bars_daily(TICKERS, START, END)

    assert calls["n"] == 1  # second call served entirely from disk
    pd.testing.assert_frame_equal(first, second)


def test_rate_limiter_is_invoked_on_every_network_call(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(fetch_base.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    clock = {"t": 0.0}
    monkeypatch.setattr(fetch_base.time, "monotonic", lambda: clock["t"])

    def fake_download(tickers, **kwargs):
        clock["t"] += 0.001
        return _synthetic_daily_frame(list(tickers))

    monkeypatch.setattr(yahoo.yf, "download", fake_download)

    # `_download_daily`'s rate limiter keeps its last-call timestamp in a
    # module-level closure that outlives this test, so an earlier test's
    # call (under the real clock) leaves state behind. Prime it under
    # *this* test's fake clock first, then clear what that call recorded,
    # so the assertion below reflects only the measured call.
    yahoo.fetch_bars_daily(TICKERS, date(2025, 1, 1), date(2025, 1, 8))
    sleep_calls.clear()

    # A distinct date range forces a real network call (different cache
    # key), which is what makes the rate limiter's wait observable.
    yahoo.fetch_bars_daily(TICKERS, START, END)

    assert sleep_calls, "network call should have waited for the 0.5 req/s limit"
    assert sleep_calls[0] == pytest.approx(1.0 / yahoo.RATE_LIMIT_PER_SEC, rel=0.05)


def test_partial_batch_failure_retries_the_failed_ticker_individually(monkeypatch):
    """DESIGN §4.3: yfinance can return NaN columns for one ticker inside an
    otherwise successful batch. The fetcher must retry that ticker alone
    rather than dropping it or failing the whole batch.
    """
    batch_calls = []

    def fake_download(tickers, **kwargs):
        tickers = list(tickers)
        batch_calls.append(tickers)
        if len(tickers) == 1:
            # Individual retry succeeds.
            return _synthetic_daily_frame(tickers)
        frame = _synthetic_daily_frame(tickers)
        # Simulate NVDA failing inside the batch: all-NaN close series.
        frame[("NVDA", "Close")] = float("nan")
        return frame

    monkeypatch.setattr(yahoo.yf, "download", fake_download)

    result = yahoo.fetch_bars_daily(TICKERS, START, END)

    assert set(result["ticker"]) == set(TICKERS)
    assert [len(c) for c in batch_calls] == [3, 1]  # one batch call, one individual retry


def test_fetch_bars_daily_returns_empty_frame_for_delisted_ticker(monkeypatch):
    def fake_download(tickers, **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(yahoo.yf, "download", fake_download)

    result = yahoo.fetch_bars_daily(["DELISTEDXYZ"], START, END)

    assert result.empty
    assert list(result.columns) == [
        "ticker",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
        "adj_factor",
    ]
