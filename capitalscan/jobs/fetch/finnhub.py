"""Finnhub fetcher: forward earnings calendar (ADR 036).

Finnhub's free tier caps historical earnings depth near 4 years, which is
why it is forward-only here; EDGAR (`sec.py`) covers history back to 2009.
Rate limited to 0.8 req/s per DESIGN §4.2's table.
"""

from __future__ import annotations

import os
from datetime import date
from typing import cast

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import NotFoundError, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 0.8
CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"


class FinnhubConfigError(Exception):
    """FINNHUB_API_KEY is unset."""


def _api_key() -> str:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        raise FinnhubConfigError("FINNHUB_API_KEY is not set; forward earnings cannot be fetched.")
    return key


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _get(params: dict) -> dict:
    resp = requests.get(CALENDAR_URL, params={**params, "token": _api_key()}, timeout=30)
    if resp.status_code == 404:
        raise NotFoundError(CALENDAR_URL)
    resp.raise_for_status()
    return cast(dict, resp.json())


def fetch_forward_calendar(start: date, end: date, symbol: str | None = None) -> pd.DataFrame:
    """Earnings dates in `[start, end]`. Never cached — this is a rolling
    forward window refreshed weekly, and caching it would serve stale
    dates as new ones roll in.
    """
    params: dict = {"from": start.isoformat(), "to": end.isoformat()}
    if symbol:
        params["symbol"] = symbol
    raw = _get(params)
    rows = raw.get("earningsCalendar", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "date",
                "hour",
                "eps_estimate",
                "revenue_estimate",
                "source",
                "confidence",
            ]
        )
    return pd.DataFrame(
        {
            "ticker": df["symbol"].str.upper(),
            "date": df["date"],
            "hour": df.get("hour"),
            "eps_estimate": df.get("epsEstimate"),
            "revenue_estimate": df.get("revenueEstimate"),
            "source": "finnhub",
            "confidence": "estimate",
        }
    )
