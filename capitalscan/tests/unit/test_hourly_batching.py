"""Hourly bars are fetched one request per batch per window (2026-08-26).

`_download_hourly` asked for a single ticker, so a nightly cost one request
per ticker per window. Measured on 1,470 tickers at
`RATE_LIMIT_PER_SEC = 0.5`: **54.5 minutes**, against **4.9 minutes** for
the batched daily path over the same universe. The difference was the
request shape, not the data or the 60-day cap.

Verified against live Yahoo before the call site changed: `AAPL`, `MSFT`,
`JPM`, `XOM` over one window returned frames **identical row for row** to
`fetch_bars_hourly`, in 0.1s against 9.7s.

No network here -- `_download_hourly_batch` is stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from capitalscan.jobs.fetch import yahoo


def _raw(tickers: list[str], n: int = 3) -> pd.DataFrame:
    """A frame shaped like `yf.download(..., group_by='ticker')` output:
    tz-naive index in exchange-local time, column MultiIndex per ticker."""
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-08-03 10:30") + timedelta(hours=i) for i in range(n)]
    )
    cols, data = [], {}
    for t in tickers:
        for field in ("Open", "High", "Low", "Close", "Volume"):
            cols.append((t, field))
            data[(t, field)] = [1.0 + i for i in range(n)]
    return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))


# ---------------------------------------------------------------------------
# The request shape, which is the whole point
# ---------------------------------------------------------------------------


def test_one_request_covers_the_whole_batch(monkeypatch):
    """The property that turns 54.5 minutes into ~1. If this ever regresses
    to one call per ticker, the nightly silently gets an hour longer."""
    calls: list[list[str]] = []

    def _dl(tickers, start, end):
        calls.append(list(tickers))
        return _raw(list(tickers))

    monkeypatch.setattr(yahoo, "_download_hourly_batch", _dl)
    monkeypatch.setattr(yahoo, "_download_hourly", lambda *a, **k: pd.DataFrame())

    end = date(2026, 8, 20)
    yahoo._fetch_hourly_window_batch.__wrapped__(
        ["AAA", "BBB", "CCC"], end - timedelta(days=5), end
    )
    assert len(calls) == 1
    assert calls[0] == ["AAA", "BBB", "CCC"]


def test_each_ticker_is_separated_out_of_the_batch(monkeypatch):
    monkeypatch.setattr(yahoo, "_download_hourly_batch", lambda t, s, e: _raw(list(t)))
    monkeypatch.setattr(yahoo, "_download_hourly", lambda *a, **k: pd.DataFrame())
    end = date(2026, 8, 20)
    got = yahoo._fetch_hourly_window_batch.__wrapped__(["AAA", "BBB"], end - timedelta(days=5), end)
    assert set(got["ticker"]) == {"AAA", "BBB"}


def test_a_ticker_missing_from_the_batch_is_retried_alone(monkeypatch):
    """Matches `_fetch_daily_batch`'s partial-failure retry. A symbol absent
    from a multi-ticker response is indistinguishable from one that has no
    bars, so one bad symbol must not cost the other forty-nine."""
    retried: list[str] = []

    monkeypatch.setattr(yahoo, "_download_hourly_batch", lambda t, s, e: _raw(["AAA"]))

    def _single(ticker, start, end):
        # `_download_hourly` asks for one ticker, and yfinance returns flat
        # columns for that -- not the MultiIndex a batch gets. The stub has
        # to match, or the test proves nothing about the retry path.
        retried.append(ticker)
        flat = _raw([ticker])
        flat.columns = flat.columns.get_level_values(1)
        return flat

    monkeypatch.setattr(yahoo, "_download_hourly", _single)
    end = date(2026, 8, 20)
    got = yahoo._fetch_hourly_window_batch.__wrapped__(["AAA", "BBB"], end - timedelta(days=5), end)
    assert retried == ["BBB"]
    assert set(got["ticker"]) == {"AAA", "BBB"}


# ---------------------------------------------------------------------------
# The cache key, which is where the last two of these went wrong
# ---------------------------------------------------------------------------


def test_the_source_string_moved_to_v3():
    """`_v2` keys one ticker; this keys a *set*. Reusing the source would
    make a `_v2` entry answer a different question than the one asked --
    the failure CLAUDE.md records, where a correct fix merged, passed CI,
    and never ran."""
    src = getattr(yahoo._fetch_hourly_window_batch, "__wrapped__", None)
    assert src is not None, "expected @cached to wrap the batch fetcher"
    import inspect

    assert "yahoo_hourly_v3" in inspect.getsource(yahoo)


def test_the_key_distinguishes_different_ticker_sets():
    end = date(2026, 8, 20)
    start = end - timedelta(days=5)
    a = yahoo._hourly_batch_key(["AAA", "BBB"], start, end)
    b = yahoo._hourly_batch_key(["AAA", "CCC"], start, end)
    assert a != b


def test_the_key_is_order_independent():
    """The same set requested in a different order is the same request. A
    key that disagreed would double every cache entry."""
    end = date(2026, 8, 20)
    start = end - timedelta(days=5)
    assert yahoo._hourly_batch_key(["BBB", "AAA"], start, end) == yahoo._hourly_batch_key(
        ["AAA", "BBB"], start, end
    )


def test_the_key_distinguishes_windows():
    end = date(2026, 8, 20)
    assert yahoo._hourly_batch_key(
        ["AAA"], end - timedelta(days=5), end
    ) != yahoo._hourly_batch_key(["AAA"], end - timedelta(days=60), end)


def test_the_key_is_filename_safe():
    """50 symbols joined would exceed a sane filename, so the set is
    hashed."""
    key = yahoo._hourly_batch_key(
        [f"TICK{i}" for i in range(50)], date(2026, 1, 1), date(2026, 3, 1)
    )
    assert len(key) < 80
    assert "/" not in key and "\\\\" not in key


# ---------------------------------------------------------------------------
# The shaping, which must not drift from the per-ticker path
# ---------------------------------------------------------------------------


def test_only_regular_session_bars_survive():
    """Pre-market and after-hours compare meaninglessly against a
    prior-close band (DESIGN §4.4)."""
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-08-03 08:00"),  # pre-market
            pd.Timestamp("2026-08-03 10:30"),  # regular
            pd.Timestamp("2026-08-03 18:00"),  # after hours
        ]
    )
    raw = pd.DataFrame(
        {
            "Open": [1.0] * 3,
            "High": [1.0] * 3,
            "Low": [1.0] * 3,
            "Close": [1.0] * 3,
            "Volume": [1] * 3,
        },
        index=idx,
    )
    got = yahoo._shape_hourly(raw, "AAA")
    assert len(got) == 1


def test_an_empty_raw_frame_yields_the_empty_shape():
    got = yahoo._shape_hourly(pd.DataFrame(), "AAA")
    assert got.empty
    assert list(got.columns) == list(yahoo._empty_hourly_frame().columns)


def test_the_windows_are_unchanged_by_batching():
    """Yahoo caps hourly *history per request*, not tickers per request, so
    the 60-day walk is untouched. Batching collapses the ticker dimension
    only."""
    end = date(2026, 8, 20)
    windows = yahoo._hourly_windows(end - timedelta(days=180), end)
    assert len(windows) == 3
    assert all((w_end - w_start).days <= yahoo.HOURLY_WINDOW_DAYS for w_start, w_end in windows)


# ---------------------------------------------------------------------------
# The many-ticker wrapper
# ---------------------------------------------------------------------------


def test_a_ticker_with_no_bars_is_absent_from_the_result(monkeypatch):
    """`fetch_bars_hourly` returned an empty frame for those; the dict
    version omits the key, and the ingest call site treats absence the same
    way it treated empty."""
    monkeypatch.setattr(
        yahoo, "_fetch_hourly_window_batch", lambda t, s, e: yahoo._empty_hourly_frame()
    )
    end = date(2026, 8, 20)
    got = yahoo.fetch_bars_hourly_many(["AAA"], end - timedelta(days=5), end)
    assert got == {}


def test_windows_are_concatenated_and_deduplicated(monkeypatch):
    """Adjacent windows share their boundary day, so the same bar arrives
    twice."""
    end = date(2026, 8, 20)
    frame = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "ts": [pd.Timestamp("2026-08-03 10:30", tz="America/New_York")] * 2,
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1, 1],
        }
    )
    monkeypatch.setattr(yahoo, "_fetch_hourly_window_batch", lambda t, s, e: frame)
    got = yahoo.fetch_bars_hourly_many(["AAA"], end - timedelta(days=180), end)
    assert len(got["AAA"]) == 1
