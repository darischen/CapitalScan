"""Stooq fetcher: independent daily-bar cross-check (DESIGN §5.8).

`cscan validate --report` runs this against a 20-ticker sample to catch a
Yahoo-side data error that would otherwise look like ground truth. Rate
limited to 2 req/s per DESIGN §4.2's table.
"""

from __future__ import annotations

from datetime import date
from io import StringIO

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import NotFoundError, cached, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 2.0
DAILY_URL = "https://stooq.com/q/d/l/"


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _get_csv(ticker: str, start: date, end: date) -> str:
    resp = requests.get(
        DAILY_URL,
        params={
            "s": f"{ticker.lower()}.us",
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        },
        timeout=30,
    )
    if resp.status_code == 404:
        raise NotFoundError(ticker)
    resp.raise_for_status()
    text = resp.text
    # Stooq returns HTTP 200 with the literal body "No data" for an unknown
    # symbol rather than a 4xx status.
    if text.strip() in ("", "No data") or not text.startswith("Date,"):
        raise NotFoundError(ticker)
    return text


def _key(ticker: str, start: date, end: date) -> str:
    return f"{ticker}_{start}_{end}"


@cached(source="stooq_daily", key_fn=_key)
def fetch_daily(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV for cross-checking against the Yahoo-sourced series."""
    csv_text = _get_csv(ticker, start, end)
    df = pd.read_csv(StringIO(csv_text))
    df.columns = [c.lower() for c in df.columns]
    df["ticker"] = ticker
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[["ticker", "date", "open", "high", "low", "close", "volume"]]
