"""Yahoo Finance fetcher: daily bars, hourly bars, actions, quotes, chains.

DESIGN §4.3 (`bars_daily`) and §4.4 (`bars_hourly`) pin the exact call
shape. This module returns tidy frames only — validation (DESIGN §2.3)
and upserts belong to the `jobs/ingest` layer (BUILD session 5), not here.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import pandas as pd
import yfinance as yf

from capitalscan.jobs.fetch.base import cached, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 0.5
DAILY_BATCH_SIZE = 50
HOURLY_WINDOW_DAYS = 60

_BARS_COLUMNS = [
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
_HOURLY_COLUMNS = ["ticker", "ts", "open", "high", "low", "close", "volume"]
_ACTIONS_COLUMNS = ["ticker", "ts", "action_type", "value"]


def _empty_bars_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_BARS_COLUMNS)


def _empty_hourly_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_HOURLY_COLUMNS)


def _split_batches(tickers: list[str], size: int = DAILY_BATCH_SIZE) -> list[list[str]]:
    return [tickers[i : i + size] for i in range(0, len(tickers), size)]


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_daily(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    # threads=False: yfinance's internal threading trips rate limiting
    # faster than sequential batching, and the batch is already the
    # parallelism (DESIGN §4.3). auto_adjust=False is mandatory — the
    # default overwrites OHLC with adjusted values and destroys the
    # split-adjusted series band levels are computed from.
    return cast(
        pd.DataFrame,
        yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=False,
            actions=True,
            group_by="ticker",
            threads=False,
            progress=False,
        ),
    )


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        return cast(pd.DataFrame, raw[ticker])
    return raw


def _has_no_data(sub: pd.DataFrame) -> bool:
    return bool(sub.empty or "Close" not in sub.columns or sub["Close"].isna().all())


def _tidy_daily(sub: pd.DataFrame, ticker: str) -> pd.DataFrame:
    sub = sub.dropna(subset=["Close"])
    index = cast(pd.DatetimeIndex, sub.index)
    idx = index.tz_localize(None) if index.tz is not None else index
    out = pd.DataFrame(
        {
            "ticker": ticker,
            "ts": idx,
            "open": sub["Open"].to_numpy(),
            "high": sub["High"].to_numpy(),
            "low": sub["Low"].to_numpy(),
            "close": sub["Close"].to_numpy(),
            "adj_close": sub["Adj Close"].to_numpy(),
            "volume": sub["Volume"].to_numpy(),
        }
    )
    out["adj_factor"] = out["adj_close"] / out["close"]
    return out.reset_index(drop=True)


def _batch_key(tickers: list[str], start: date, end: date) -> str:
    return f"{'-'.join(sorted(tickers))}_{start}_{end}"


@cached(source="yahoo_daily", key_fn=_batch_key)
def _fetch_daily_batch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """One batch, with the partial-batch-failure retry (DESIGN §4.3)."""
    raw = _download_daily(tickers, start, end)
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for ticker in tickers:
        sub = _extract_ticker_frame(raw, ticker)
        if _has_no_data(sub):
            failed.append(ticker)
            continue
        frames.append(_tidy_daily(sub, ticker))

    # yfinance returns NaN columns for failed tickers inside an otherwise
    # successful batch rather than raising; retry those individually.
    for ticker in failed:
        raw_single = _download_daily([ticker], start, end)
        sub = _extract_ticker_frame(raw_single, ticker)
        if not _has_no_data(sub):
            frames.append(_tidy_daily(sub, ticker))

    return pd.concat(frames, ignore_index=True) if frames else _empty_bars_frame()


def fetch_bars_daily(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV plus `adj_factor` for every ticker with data in range.

    A ticker silently missing from the result means Yahoo returned nothing
    for it (delisting, wrong symbol, or full-batch failure after the
    individual retry) — the caller records that against `first_bar` /
    `last_bar` coverage, per DESIGN §4.3's silent-truncation note.
    """
    batches = [_fetch_daily_batch(batch, start, end) for batch in _split_batches(tickers)]
    non_empty = [b for b in batches if not b.empty]
    if not non_empty:
        return _empty_bars_frame()
    return cast(
        pd.DataFrame,
        pd.concat(non_empty, ignore_index=True)
        .sort_values(["ticker", "ts"])
        .reset_index(drop=True),
    )


def _hourly_windows(start: date, end: date) -> list[tuple[date, date]]:
    # Yahoo caps hourly data at ~730 days; cap start date conservatively at 725 days (DESIGN §4.4)
    max_start = end - timedelta(days=725)
    clamped_start = max(start, max_start)

    windows = []
    cur = clamped_start
    while cur < end:
        window_end = min(cur + timedelta(days=HOURLY_WINDOW_DAYS), end)
        windows.append((cur, window_end))
        cur = window_end
    return windows


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_hourly(ticker: str, start: date, end: date) -> pd.DataFrame:
    return cast(
        pd.DataFrame,
        yf.download(
            ticker,
            start=start,
            end=end,
            interval="1h",
            auto_adjust=False,
            actions=False,
            threads=False,
            progress=False,
        ),
    )


def _window_key(ticker: str, start: date, end: date) -> str:
    return f"{ticker}_{start}_{end}"


@cached(source="yahoo_hourly", key_fn=_window_key)
def _fetch_hourly_window(ticker: str, start: date, end: date) -> pd.DataFrame:
    raw = _download_hourly(ticker, start, end)
    if raw.empty:
        return _empty_hourly_frame()

    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Normalize exchange-local timestamps to America/New_York, then keep
    # the regular session only (09:30-16:00). Pre-market and after-hours
    # bars compare meaninglessly against a prior-close band (DESIGN §4.4).
    idx = df.index
    idx = (
        idx.tz_localize("America/New_York")
        if idx.tz is None
        else idx.tz_convert("America/New_York")
    )
    df.index = idx
    df = df.between_time("09:30", "16:00")

    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": df.index,
            "open": df["Open"].to_numpy(),
            "high": df["High"].to_numpy(),
            "low": df["Low"].to_numpy(),
            "close": df["Close"].to_numpy(),
            "volume": df["Volume"].to_numpy(),
        }
    ).reset_index(drop=True)


def fetch_bars_hourly(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Hourly regular-session bars, walked in 60-day windows (DESIGN §4.4)."""
    windows = [
        _fetch_hourly_window(ticker, w_start, w_end)
        for w_start, w_end in _hourly_windows(start, end)
    ]
    non_empty = [w for w in windows if not w.empty]
    if not non_empty:
        return _empty_hourly_frame()
    return cast(
        pd.DataFrame,
        pd.concat(non_empty, ignore_index=True)
        .drop_duplicates(subset=["ticker", "ts"])
        .sort_values("ts")
        .reset_index(drop=True),
    )


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_actions(ticker: str) -> pd.DataFrame:
    return cast(pd.DataFrame, yf.Ticker(ticker).actions)


@cached(source="yahoo_actions", key_fn=lambda ticker: ticker)
def fetch_actions(ticker: str) -> pd.DataFrame:
    """Splits and dividends, tidied to one row per event."""
    raw = _download_actions(ticker)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=_ACTIONS_COLUMNS)

    df = raw.reset_index().rename(columns={"Date": "ts"})
    records: list[dict] = []
    if "Stock Splits" in df.columns:
        splits = df.loc[df["Stock Splits"] != 0]
        for _, row in splits.iterrows():
            records.append(
                {"ticker": ticker, "ts": row["ts"], "action_type": "split", "value": row["Stock Splits"]}
            )
    if "Dividends" in df.columns:
        divs = df.loc[df["Dividends"] != 0]
        for _, row in divs.iterrows():
            records.append(
                {"ticker": ticker, "ts": row["ts"], "action_type": "dividend", "value": row["Dividends"]}
            )
    return pd.DataFrame(records, columns=_ACTIONS_COLUMNS)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def fetch_quotes(tickers: list[str]) -> pd.DataFrame:
    """Batch live quote for the poller (DESIGN §4.8: 1-2 requests total).

    Never cached — a stale quote is a wrong signal, not a saved network
    call.
    """
    raw = yf.download(
        tickers,
        period="1d",
        interval="1m",
        group_by="ticker",
        threads=False,
        progress=False,
    )
    rows = []
    for ticker in tickers:
        sub = _extract_ticker_frame(raw, ticker)
        if _has_no_data(sub):
            continue
        last = sub.dropna(subset=["Close"]).iloc[-1]
        rows.append({"ticker": ticker, "ts": sub.index[-1], "price": float(last["Close"])})
    return pd.DataFrame(rows, columns=["ticker", "ts", "price"])


_SHARES_FULL_COLUMNS = ["ticker", "filed_on", "shares"]


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_shares_full(ticker: str, start: date, end: date) -> pd.Series | None:
    # `Ticker.get_shares_full` hits Yahoo's own "quoteSummary" endpoint, not
    # `yf.download` — a 404 (bad symbol) surfaces as a caught-and-logged
    # error inside yfinance itself, which returns `None` rather than
    # raising. `_has_no_data`-style emptiness handling lives in the caller;
    # this wrapper exists only to give the `None` case the same
    # rate-limit/retry treatment as every other network call in this module.
    return cast(
        "pd.Series | None",
        yf.Ticker(ticker).get_shares_full(start=start, end=end),
    )


def _shares_full_key(ticker: str, start: date, end: date) -> str:
    return f"{ticker}_{start}_{end}"


@cached(source="yahoo_shares_full", key_fn=_shares_full_key)
def fetch_shares_full(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Dated shares-outstanding series, the current-shares fallback (BUILD follow-up).

    `Ticker(ticker).info["sharesOutstanding"]` was the obvious first fallback
    for the ~69 tickers whose SEC XBRL fact is missing or stale (see the
    `SHARES_STALENESS_DAYS` comment in `jobs/ingest.py`), and it was
    rejected: it is one number, true only *today*, and DESIGN §2.4 already
    names the failure mode this would reproduce ("the rejected alternative
    was holding current shares constant backward... AAPL's 2016 cap would
    come out ~50% too low"). Stamping today's count onto every historical
    `as_of` is exactly that mistake with a different data source.

    `get_shares_full(start, end)` is different in kind, not just a wider
    window: verified live against BRK-B, MA, V, and META, it returns a
    `pd.Series` of Yahoo's own dated share-count estimates reaching back to
    ~2015 for all four, each value tied to the date Yahoo attributes it to
    (buybacks and issuances move the count between points). That is a
    genuine point-in-time series, not "current, applied backward" — a row
    dated 2016 in this frame describes 2016, the same contract SEC's
    `filed_on` rows carry. It is why this is the fallback used, and why
    `run_shares` inserts every point in the requested window rather than
    only the latest one: a single-most-recent write would silently
    re-introduce the "current constant held backward" problem for any
    `as_of` between two Yahoo observation dates prior to the most recent one.

    Returns an empty frame (not raising) when Yahoo has nothing for
    `ticker` in `[start, end)` — a delisted or never-covered symbol is a
    `run_shares` skip note, not a fetch failure.
    """
    raw = _download_shares_full(ticker, start, end)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=_SHARES_FULL_COLUMNS)
    index = cast(pd.DatetimeIndex, raw.index)
    idx = index.tz_localize(None) if index.tz is not None else index
    return pd.DataFrame(
        {
            "ticker": ticker,
            "filed_on": idx.date,
            "shares": raw.to_numpy(dtype="int64"),
        }
    ).reset_index(drop=True)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def fetch_option_expirations(ticker: str) -> list[str]:
    """Every available expiration date, nearest first (ADR 050's call
    overlay needs the full list to pick "nearest expiry past day 5";
    `fetch_chain` below only returns one expiry's calls at a time). Never
    cached — new expiries list weekly.
    """
    return list(yf.Ticker(ticker).options)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def fetch_chain(ticker: str, expiration: str | None = None) -> pd.DataFrame:
    """Live option chain for the call overlay (ADR 050). Never cached."""
    tk = yf.Ticker(ticker)
    exp = expiration or (tk.options[0] if tk.options else None)
    if exp is None:
        return pd.DataFrame()
    calls = cast(pd.DataFrame, tk.option_chain(exp).calls.copy())
    calls["ticker"] = ticker
    calls["expiration"] = exp
    return calls
