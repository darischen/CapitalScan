"""SEC EDGAR fetcher: CIK lookup, XBRL company facts, 8-K submissions.

Used for `shares` (XBRL `shares outstanding`, DESIGN §4.1) and for
historical earnings dates via 8-K filing dates (ADR 036). Rate limited to
8 req/s per DESIGN §4.2's table; `SEC_USER_AGENT` is mandatory — EDGAR
blocks requests without one at the IP level, persistently, and the block
does not clear on its own.
"""

from __future__ import annotations

import os
from typing import cast

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import NotFoundError, cached, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 8.0
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

_SHARES_TAGS = ("EntityCommonStockSharesOutstanding",)
_SHARES_UNIT = "shares"


class SecConfigError(Exception):
    """SEC_USER_AGENT is unset. Fix this before any SEC fetch, not after."""


def _user_agent() -> str:
    agent = os.getenv("SEC_USER_AGENT")
    if not agent:
        raise SecConfigError(
            "SEC_USER_AGENT is not set. SEC EDGAR blocks requests without a "
            "real contact email in the User-Agent header, at the IP level, "
            "persistently. Set it in .env before fetching from SEC."
        )
    return agent


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": _user_agent()}, timeout=30)
    if resp.status_code == 404:
        raise NotFoundError(url)
    resp.raise_for_status()
    return resp


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _fetch_json(url: str) -> dict:
    return cast(dict, _get(url).json())


@cached(source="sec_tickers", key_fn=lambda: "company_tickers")
def fetch_cik_lookup() -> pd.DataFrame:
    """Ticker -> CIK mapping from SEC's published `company_tickers.json`."""
    raw = _fetch_json(TICKERS_URL)
    rows = [
        {"ticker": entry["ticker"].upper(), "cik": int(entry["cik_str"]), "name": entry["title"]}
        for entry in raw.values()
    ]
    return pd.DataFrame(rows, columns=["ticker", "cik", "name"])


@cached(source="sec_facts", key_fn=lambda cik: str(cik))
def fetch_company_facts(cik: int) -> pd.DataFrame:
    """Shares-outstanding history from XBRL `companyfacts`, point-in-time.

    Point-in-time semantics (DESIGN §2.4) live in the caller: use the
    latest filing with `filed_on < as_of`. This fetcher returns every
    filed value with its `filed_on` date and lets the caller pick.
    """
    raw = _fetch_json(FACTS_URL.format(cik=cik))
    facts = raw.get("facts", {}).get("dei", {})
    rows = []
    for tag in _SHARES_TAGS:
        units = facts.get(tag, {}).get("units", {}).get(_SHARES_UNIT, [])
        for entry in units:
            rows.append(
                {
                    "cik": cik,
                    "tag": tag,
                    "filed_on": entry.get("filed"),
                    "end": entry.get("end"),
                    "value": entry.get("val"),
                    "form": entry.get("form"),
                    "accn": entry.get("accn"),
                }
            )
    return pd.DataFrame(rows, columns=["cik", "tag", "filed_on", "end", "value", "form", "accn"])


@cached(source="sec_submissions", key_fn=lambda cik: str(cik))
def fetch_submissions(cik: int) -> pd.DataFrame:
    """Filing history for a CIK, including 8-K filing dates (ADR 036)."""
    raw = _fetch_json(SUBMISSIONS_URL.format(cik=cik))
    recent = raw.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    return pd.DataFrame(
        {
            "cik": cik,
            "form": forms,
            "filed_on": dates,
            "accn": accns,
        }
    )


def fetch_8k_dates(cik: int) -> pd.DataFrame:
    """8-K filing dates only — the earnings-date proxy per ADR 036."""
    submissions = fetch_submissions(cik)
    eight_ks = submissions.loc[submissions["form"] == "8-K"].copy()
    eight_ks["source"] = "sec_8k"
    return cast(
        pd.DataFrame, eight_ks[["cik", "filed_on", "accn", "source"]].reset_index(drop=True)
    )
