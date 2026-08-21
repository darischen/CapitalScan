"""Nasdaq fetcher: currently-listed names above a market-cap floor.

The second source of tickers, beside `wikipedia.py`'s S&P 500 membership.
Where that one supplies a *union over time* — every historical member,
including the failures, which is what ADR 035 depends on — this one supplies
a **snapshot of what is listed today**. The difference is the whole reason
ADR 143 exists, and it is stated here rather than only there because this is
the file someone will reach for when they want to add more tickers.

Nasdaq's screener endpoint returns every listed security with a market cap
in one request, so the floor is applied here rather than by ingesting 4,193
names and filtering later. Measured 2026-08-21:

    listed rows   4,193
    >= $30B         158
    >= $20B         212
    >= $10B         336
    >= $5B          529
    rank #1000     $1.60B

That last line is why the floor and a "top N" rule are not the same request.
"""

from __future__ import annotations

import re
from typing import Any, cast

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import cached, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 1.0

SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"

# The endpoint refuses a default `python-requests` agent. Not scraping
# defeat -- the same string the browser sends, and one request per run.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}

# **The ingest floor, deliberately below the trade floor.**
#
# `UniverseParams.min_mcap_usd` decides tradeability quarter by quarter from
# point-in-time shares and price. This decides only which tickers are worth
# holding bars for, and it sits lower on purpose: a name at $12B today may
# have been above the trade floor earlier in the window, and ingesting it is
# the only way those quarters can ever be measured. Raising this to match
# the trade floor would quietly delete that history.
#
# **$5B, chosen 2026-08-21.** Measured against the live listing that day,
# after preferred series, warrants and units are removed:
#
#     >= $30B   142     >= $10B   307
#     >= $20B   190     >= $5B    492
#
# The trade floor is $20B / 190 names, so this carries 302 names that are
# not tradeable today and exist to cover the quarters when they were. A
# name at $6B now was plausibly above $20B inside a sixteen-year window;
# one at $600M was not, which is where the floor stops falling.
INGEST_MIN_MCAP_USD = 5e9


# **Securities that are not the company's common stock.**
#
# The screener lists every security on the exchange and gives each one the
# *issuer's* market cap. So AGNC Investment appears seven times -- the common
# plus six depositary-preferred series -- each reporting the same $10B+, and
# an unfiltered ingest would carry six extra "companies" whose bars are a
# preferred share's price and whose market cap belongs to something else.
# 29 of 176 new names on 2026-08-21 were this.
#
# **An ADR is not one of these and must survive.** ADR 011 admits non-US
# exposure through US-listed ADRs specifically (TSM, ASML, SAP, NVO), and
# ARM, ARGX, BNTX and 13 others reach the $10B floor that way. Matching on
# "depositary" alone would have dropped all 16 -- it did, on the first
# attempt, which is why the pattern names what makes a security *preferred*
# rather than what makes it depositary.
_NOT_COMMON = re.compile(
    r"preferred|warrant|\bunits?\b|representing a 1/|% series|fixed-rate|fixed/float",
    re.I,
)


def _is_common(row: dict[str, Any]) -> bool:
    """Is this the issuer's common stock (or an ADR of it)?"""
    return not _NOT_COMMON.search(str(row.get("name") or ""))


def _market_cap(row: dict[str, Any]) -> float:
    """Market cap as a float, or 0.0 when the field is absent or unparseable.

    The endpoint returns it as a string, empty for names it has no cap for
    (recent listings, some ADRs). Zero rather than a raise: a missing cap is
    a name that fails the floor, not a fetch failure.
    """
    raw = row.get("marketCap") or ""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _fetch_rows() -> list[dict[str, Any]]:
    resp = requests.get(
        SCREENER_URL,
        params={"tableonly": "true", "limit": "25", "exchange": "NASDAQ", "download": "true"},
        headers=_HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    return cast(list[dict[str, Any]], payload["data"]["rows"])


@cached(source="nasdaq_screener_v1", key_fn=lambda: "listed_with_mcap")
def fetch_listed() -> pd.DataFrame:
    """Every Nasdaq-listed security, with market cap, sector and country.

    **Returns a frame, not the endpoint's list of dicts.** `@cached` writes
    its result with `to_parquet`, so the cache contract is a DataFrame -- a
    list round-trips through the decorator fine on a miss and raises
    `AttributeError` on the write, which is a failure that only appears the
    first time the function is called for real.

    Cached like every other fetcher, and the key is the source string plus a
    constant, so this returns the same snapshot until the source is bumped.
    That is correct for a job that seeds a ticker list and would otherwise
    change the universe silently between two runs of one command. Bump
    `nasdaq_screener_v1` to take a fresh snapshot deliberately.
    """
    return pd.DataFrame(_fetch_rows())


def tickers_above(min_mcap_usd: float = INGEST_MIN_MCAP_USD) -> list[str]:
    """Listed Nasdaq tickers at or above `min_mcap_usd`, sorted.

    Symbols are normalised the way `run_tickers_refresh` normalises
    Wikipedia's: upper-cased with `.` rewritten to `-`, so a class share
    arrives as `BRK-B` and matches the rest of `tickers`. `stockcharts.ts`
    translates that back to `BRK/B` for display, which is the only place the
    two spellings meet.

    Preferred series, warrants and units are excluded: the screener gives
    every security the issuer's market cap, so they clear the floor on their
    parent's size while their bars are a different instrument entirely.
    Ordinary ADRs are kept -- see `_NOT_COMMON`.

    Sorted rather than in screener order so two runs over one cached
    snapshot produce identical lists -- the same determinism `run_events`
    depends on, reached one layer earlier.
    """
    frame = fetch_listed()
    if frame.empty:
        return []
    out = {
        str(row["symbol"]).strip().upper().replace(".", "-")
        for row in frame.to_dict("records")
        if _market_cap(row) >= min_mcap_usd and _is_common(row)
    }
    # A blank symbol is not a ticker. The endpoint has returned one.
    return sorted(t for t in out if t)
