"""Finnhub fetcher: forward earnings calendar (ADR 036).

Finnhub's free tier caps historical earnings depth near 4 years, which is
why it is forward-only here; EDGAR (`sec.py`) covers history back to 2009.
Rate limited to 0.8 req/s per DESIGN §4.2's table.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import date, timedelta
from typing import cast

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import NotFoundError, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 0.8
CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"

# **Finnhub truncates a calendar response at 1,500 entries, and says
# nothing.** Measured 2026-08-26: a 90-day window returned exactly 1,500
# rows across 1,496 tickers and AAPL was *not among them*, while a
# five-day window covering AAPL's own report date returned 905 rows with
# AAPL present and complete. There is no `hasMore` flag, no error, and no
# HTTP status to distinguish a full answer from a clipped one -- only the
# row count sitting exactly on a round number.
CALENDAR_PAGE_LIMIT = 1500

# Days per request. Peak earnings weeks measured ~180 rows/day, so five
# days is ~900 -- comfortably under the cap with room for a heavier season.
# `_fetch_calendar_range` halves any chunk that hits the limit anyway, so
# this is a starting point rather than a guarantee.
CALENDAR_CHUNK_DAYS = 5


class CalendarTruncated(Exception):
    """A single-day calendar request came back at the cap.

    Unsplittable, so the data is knowably incomplete and there is nothing
    honest to return. Raised rather than logged: a missing earnings date
    silently produces a wrong `days_to_earnings` on every event near it,
    and invariant 4 says drop and report rather than guess.
    """


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


def _fetch_calendar_range(start: date, end: date, depth: int = 0) -> list[pd.DataFrame]:
    """One window, split in half as many times as the cap demands.

    A response at `CALENDAR_PAGE_LIMIT` is assumed clipped, because there is
    no other signal. Halving is the cheapest way to be sure: two smaller
    windows either come back under the limit, in which case they are
    complete, or they split again.
    """
    frame = fetch_forward_calendar(start, end)
    if len(frame) < CALENDAR_PAGE_LIMIT:
        return [frame]
    if start >= end:
        raise CalendarTruncated(
            f"{start} alone returned {len(frame)} rows, at the {CALENDAR_PAGE_LIMIT} cap; "
            "cannot split a single day further"
        )
    mid = start + (end - start) // 2
    return _fetch_calendar_range(start, mid, depth + 1) + _fetch_calendar_range(
        mid + timedelta(days=1), end, depth + 1
    )


def fetch_forward_calendar_many(
    start: date, end: date, tickers: Iterable[str] | None = None
) -> pd.DataFrame:
    """The whole forward window in a handful of requests, not one per ticker.

    **The endpoint is bulk and was being used per symbol.** `run_earnings`
    called `fetch_forward_calendar(..., symbol=ticker)` in a loop: 1,470
    requests at `RATE_LIMIT_PER_SEC = 0.8` is 30.6 minutes of pure rate
    limiting, measured at **43.5 minutes** end to end on 2026-08-26. Omitting
    `symbol` returns every company in the range, so a 90-day window is ~18
    requests -- about 22 seconds.

    `tickers` filters the result to a universe. The calendar covers every US
    listing, and the caller wants its own names.
    """
    frames = [f for f in _walk(start, end) if not f.empty]
    if not frames:
        return fetch_forward_calendar(start, start)  # the empty shape
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ticker", "date"])
    if tickers is not None:
        wanted = {t.upper() for t in tickers}
        out = out.loc[out["ticker"].isin(wanted)]
    return out.reset_index(drop=True)


def _walk(start: date, end: date) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=CALENDAR_CHUNK_DAYS - 1), end)
        frames.extend(_fetch_calendar_range(cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return frames


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
