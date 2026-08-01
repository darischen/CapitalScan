"""SEC EDGAR fetcher: CIK lookup, XBRL company facts, 8-K submissions.

Used for `shares` (XBRL `shares outstanding`, DESIGN §4.1) and for
historical earnings dates via 8-K filing dates (ADR 036). Rate limited to
8 req/s per DESIGN §4.2's table; `SEC_USER_AGENT` is mandatory — EDGAR
blocks requests without one at the IP level, persistently, and the block
does not clear on its own.
"""

from __future__ import annotations

import logging
import os
from typing import cast

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import NotFoundError, cached, rate_limited, with_retry

logger = logging.getLogger(__name__)

RATE_LIMIT_PER_SEC = 8.0
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# `filings.files[]` entries carry only a bare `name`; shards live as
# sibling files to the submissions JSON itself, not under a versioned path.
SUBMISSIONS_HOST = "https://data.sec.gov/submissions/"

_SHARES_UNIT = "shares"
# `dei:EntityCommonStockSharesOutstanding` is the cover-page fact and the
# preferred source: DESIGN §4.1 assumed every filer tags it every quarter.
# In practice a filer stops appearing in this concept's `units` array the
# moment its share count becomes dimensioned (reported per class of stock
# rather than as one entity-level number) — SEC's companyfacts endpoint
# does not expose dimensioned members at all, so the fact does not
# degrade, it vanishes. Confirmed directly against data.sec.gov for BRK-B
# (stops in 2011), MA and V (stop in 2010) and disappears entirely for
# GOOGL/META, all coinciding with each filer's move to per-class cover-page
# reporting. `us-gaap:CommonStockSharesOutstanding` is the fallback: some
# filers (e.g. Alphabet) tag a non-dimensioned combined total there even
# after `dei:EntityCommonStockSharesOutstanding` goes dimensioned-only, so
# it recovers real rows for those issuers. It recovers nothing for BRK-B,
# MA or V — those filers have no non-dimensional shares-outstanding fact
# anywhere in the feed, in either namespace, and that is a real gap in the
# source data, not a parsing bug. `run_shares` (jobs/ingest.py) logs a
# ticker that comes back empty after both tags rather than silently
# dropping it.
_SHARES_TAG_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dei", ("EntityCommonStockSharesOutstanding",)),
    ("us-gaap", ("CommonStockSharesOutstanding",)),
)


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


def _shares_rows(cik: int, taxonomy: dict, tag: str) -> list[dict]:
    units = taxonomy.get(tag, {}).get("units", {}).get(_SHARES_UNIT, [])
    return [
        {
            "cik": cik,
            "tag": tag,
            "filed_on": entry.get("filed"),
            "end": entry.get("end"),
            "value": entry.get("val"),
            "form": entry.get("form"),
            "accn": entry.get("accn"),
        }
        for entry in units
    ]


@cached(source="sec_facts", key_fn=lambda cik: str(cik))
def fetch_company_facts(cik: int) -> pd.DataFrame:
    """Shares-outstanding history from XBRL `companyfacts`, point-in-time.

    Point-in-time semantics (DESIGN §2.4) live in the caller: use the
    latest filing with `filed_on < as_of`. This fetcher returns every
    filed value with its `filed_on` date and lets the caller pick.

    Tries `_SHARES_TAG_SOURCES` in order and stops at the first namespace
    that yields any row. The two are not merged: mixing an entity-level
    dei fact with a us-gaap balance-sheet fact for the same filer would
    combine two different reporting conventions into one history, which
    is worse than picking one and documenting the gap (see the comment
    above `_SHARES_TAG_SOURCES`).
    """
    raw = _fetch_json(FACTS_URL.format(cik=cik))
    facts = raw.get("facts", {})
    rows: list[dict] = []
    for namespace, tags in _SHARES_TAG_SOURCES:
        taxonomy = facts.get(namespace, {})
        for tag in tags:
            rows.extend(_shares_rows(cik, taxonomy, tag))
        if rows:
            break
    return pd.DataFrame(rows, columns=["cik", "tag", "filed_on", "end", "value", "form", "accn"])


def _filing_rows(cik: int, payload: dict) -> pd.DataFrame:
    """Build the (cik, form, filed_on, accn) frame from one filings payload.

    `filings.recent` and each `filings.files[]` shard share the same shape
    (three parallel arrays: `form`, `filingDate`, `accessionNumber`) — the
    shard is just the same structure fetched from a different URL instead
    of nested under `recent`. One helper avoids writing the same three
    `.get(...)` lines twice.
    """
    forms = payload.get("form", [])
    dates = payload.get("filingDate", [])
    accns = payload.get("accessionNumber", [])
    return pd.DataFrame({"cik": cik, "form": forms, "filed_on": dates, "accn": accns})


@cached(source="sec_submissions", key_fn=lambda cik: str(cik))
def fetch_submissions(cik: int) -> pd.DataFrame:
    """Filing history for a CIK, including 8-K filing dates (ADR 036).

    `filings.recent` caps out around the most recent 1,000 filings — fine
    for a ticker onboarded in the last few years, silently wrong for one
    with a decade-plus history. The overflow lives in `filings.files[]`,
    each entry a shard fetched by `name` from the same host
    (e.g. "CIK0000320193-submissions-001.json"). Missing this is precisely
    what left most of the universe's 8-K history starting in 2014-2016
    instead of reaching ADR 036's 2010 target, and it does not fail loudly:
    `_merge_days_to_earnings` (jobs/compute.py) happily attaches the
    nearest *future* filing it can find to a bar a decade earlier, so the
    gap shows up as a plausible-looking wrong number, not an error.

    A shard that fails (404, exhausted retries, whatever) must raise rather
    than be logged and skipped. This function is wrapped in `@cached`
    (`base.py`): on a cache miss, whatever this function returns gets
    written to `data/cache/sec_submissions/<cik>.parquet` and served to
    every future call, including the repair run meant to fix a bad fetch.
    Swallowing a shard failure here would persist a partial filing history
    that looks complete and is not — precisely the failure mode this whole
    fix exists to eliminate (the original missing-shards bug was invisible
    until a distribution check on `days_to_earnings` caught it). Raising
    before `to_parquet` runs means nothing is cached and the next run
    genuinely retries. `@with_retry` already gives each shard its retries,
    so anything still failing here is real and worth surfacing loudly.
    """
    raw = _fetch_json(SUBMISSIONS_URL.format(cik=cik))
    filings = raw.get("filings", {})
    frames = [_filing_rows(cik, filings.get("recent", {}))]

    for shard in filings.get("files", []):
        name = shard.get("name")
        if not name:
            continue
        try:
            shard_payload = _fetch_json(SUBMISSIONS_HOST + name)
        except Exception as exc:
            raise RuntimeError(
                f"SEC submissions shard fetch failed for CIK {cik} ({name}); "
                "refusing to cache a partial filing history"
            ) from exc
        frames.append(_filing_rows(cik, shard_payload))

    return cast(
        pd.DataFrame,
        pd.concat(frames, ignore_index=True)[["cik", "form", "filed_on", "accn"]],
    )


def fetch_8k_dates(cik: int) -> pd.DataFrame:
    """8-K filing dates only — the earnings-date proxy per ADR 036."""
    submissions = fetch_submissions(cik)
    eight_ks = submissions.loc[submissions["form"] == "8-K"].copy()
    eight_ks["source"] = "sec_8k"
    return cast(
        pd.DataFrame, eight_ks[["cik", "filed_on", "accn", "source"]].reset_index(drop=True)
    )
