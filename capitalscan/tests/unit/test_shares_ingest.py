"""Tests for the `shares` ingest fixes (this session's follow-up to
`test_shares_dedup.py`).

Background: the CardinalityViolation fix in `test_shares_dedup.py` made
`run_shares` complete, but the resulting data was badly wrong for several
mega-caps. Diagnosis (verified directly against `data.sec.gov`, not
guessed):

1. **Not a per-filing multi-class collision.** No real filer in the
   633-ticker universe has a single filing reporting two different share
   classes as two facts with the same `filed_on` *and* the same
   `period_end`. What is real is a same-day original/amendment pair (a
   10-Q immediately corrected by a 10-Q/A covering the identical period,
   observed for real CIKs 745732 and 885725) — same `period_end`, two
   different `value`s, and the amendment always carries the later `accn`.
   `_period_end_key` alone can't break that tie, so it silently fell back
   to whatever order `facts.iterrows()` produced, which happened to work
   by accident. `test_amendment_supersedes_original_on_a_period_end_tie`
   below locks in the `_accn_key` fix that makes it a rule instead of luck.

2. **The BRK-B/MA/V "wrong market cap" symptom is a genuine data gap, not
   a parsing bug.** `dei:EntityCommonStockSharesOutstanding` is an
   entity-level (non-dimensioned) cover-page fact. Once a filer starts
   reporting shares outstanding per class of stock (a dimensioned fact),
   SEC's companyfacts endpoint drops it from this concept's `units` array
   entirely — it does not degrade, it disappears. Confirmed live: BRK-B's
   feed stops in 2011, MA's and V's stop in 2010, and GOOGL's/META's have
   zero entries ever. No amount of dedup logic recovers data that was
   never exposed. `fetch_company_facts`'s `us-gaap:CommonStockSharesOutstanding`
   fallback (`test_fetch_company_facts_falls_back_to_us_gaap`, in this
   file) recovers real rows for issuers like Alphabet that tag a combined
   total there; it recovers nothing for BRK-B/MA/V because they have no
   non-dimensional fact in *either* namespace, which is exactly why
   `run_shares` must log the ticker instead of pretending the gap isn't
   there (`test_a_ticker_with_no_usable_facts_is_logged_not_silent`).

3. **58 tickers landed zero shares rows** for two distinct, confirmed
   reasons: a ticker missing entirely from `fetch_cik_lookup` (18 of them
   — old/delisted names dropped from SEC's current ticker file) and a
   ticker with a valid CIK but an empty facts concept (40 of them,
   including plain single-class names — this is not only a multi-class
   phenomenon). Both must show up in `report.notes` with which ticker and
   which reason, rather than only being visible later as a silent gap in
   `universe`'s market-cap numbers.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pandas as pd
import pytest
import requests

from capitalscan.jobs import ingest
from capitalscan.jobs.fetch import sec


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _FakeEngine:
    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()


@pytest.fixture()
def upserted(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "shares_outstanding":
            seen.extend(data)
        return len(data)

    monkeypatch.setattr(ingest.db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    return seen


def _stub(monkeypatch, cik_lookup: pd.DataFrame, facts_by_cik: dict[int, pd.DataFrame]) -> None:
    monkeypatch.setattr(ingest.sec, "fetch_cik_lookup", lambda: cik_lookup)
    monkeypatch.setattr(
        ingest.sec, "fetch_company_facts", lambda cik: facts_by_cik.get(cik, pd.DataFrame())
    )


# ============ 1. period_end tie-break must use accn, not row order ============


def test_amendment_supersedes_original_on_a_period_end_tie(monkeypatch, upserted):
    """Same `filed_on`, same `period_end`, corrected `value` — the 10-Q/A wins.

    Modeled directly on real CIK 885725's 2013-05-07 filing pair: a 10-Q
    (accn ...-000013, value 1550351484) restated by a 10-Q/A filed the same
    day (accn ...-000015, value 1349092384) covering the same period-end.
    The amendment's accession number sorts later, which is the rule
    `_accn_key` encodes — not an accident of dict/iteration order.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["AAA"], "cik": [1]}),
        {
            1: pd.DataFrame(
                {
                    "filed_on": ["2013-05-07", "2013-05-07"],
                    "end": ["2013-04-30", "2013-04-30"],
                    "value": [1550351484, 1349092384],
                    "accn": ["0000885725-13-000013", "0000885725-13-000015"],
                }
            )
        },
    )

    ingest.run_shares(["AAA"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["shares"] == 1349092384
    assert "accn" not in upserted[0]  # not a shares_outstanding column


def test_tie_break_is_order_independent(monkeypatch, upserted):
    """The same pair, facts listed in the opposite order, must resolve identically.

    This is the actual regression: before the fix, `>=` on `_period_end_key`
    alone meant "last dict-insertion wins," so reordering the input rows
    silently changed which value got kept.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["AAA"], "cik": [1]}),
        {
            1: pd.DataFrame(
                {
                    "filed_on": ["2013-05-07", "2013-05-07"],
                    "end": ["2013-04-30", "2013-04-30"],
                    "value": [1349092384, 1550351484],  # amendment listed first this time
                    "accn": ["0000885725-13-000015", "0000885725-13-000013"],
                }
            )
        },
    )

    ingest.run_shares(["AAA"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["shares"] == 1349092384  # still the higher-accn (amendment) row


# ============ 2. many-filing issuer keeps one row per filing ============


def test_issuer_with_many_filings_keeps_a_row_per_filing(monkeypatch, upserted):
    """A ticker filing every quarter for years must retain every filing's row.

    Regression target: MA and V collapsed to 4 and 2 rows respectively
    against a real ~60-quarter history for a filer of that age (like WMT's
    69). That collapse turned out not to be caused by dedup at all (see
    module docstring, point 2) but this still needs to be locked in: distinct
    `filed_on` values must never be merged into each other.
    """
    filed_dates = [f"20{y:02d}-0{q}-01" for y in range(9, 19) for q in (3, 6, 9, 12)]
    ends = [d for d in filed_dates]
    values = list(range(100_000_000, 100_000_000 + len(filed_dates)))
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["WMT"], "cik": [104169]}),
        {104169: pd.DataFrame({"filed_on": filed_dates, "end": ends, "value": values})},
    )

    ingest.run_shares(["WMT"], engine=_FakeEngine())

    assert len(upserted) == len(filed_dates)
    assert sorted(r["filed_on"] for r in upserted) == sorted(filed_dates)


# ============ 3. empty-result tickers are logged, not silently dropped ============


def test_a_ticker_missing_from_cik_lookup_is_logged(monkeypatch, upserted):
    """No CIK found (an old/delisted ticker dropped from SEC's current file)."""
    _stub(monkeypatch, pd.DataFrame({"ticker": [], "cik": []}), {})

    report = ingest.run_shares(["XOM"], engine=_FakeEngine())

    assert upserted == []
    assert report.tickers == []
    assert report.notes is not None
    assert "XOM" in report.notes
    assert "no CIK" in report.notes


def test_a_ticker_with_no_usable_facts_is_logged_not_silent(monkeypatch, upserted):
    """A valid CIK whose facts concept is empty (BRK-B/MA/V/GOOGL/META shape).

    `fetch_company_facts` returning empty is a real, confirmed shape for
    several real issuers (see module docstring) — not a fetch error. The
    fix isn't to conjure rows that don't exist; it's to make the gap show
    up in `report.notes` instead of only surfacing later as a wrong
    market-cap number with no explanation.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["META"], "cik": [1326801]}),
        {1326801: pd.DataFrame(columns=["filed_on", "end", "value", "accn"])},
    )

    report = ingest.run_shares(["META"], engine=_FakeEngine())

    assert upserted == []
    assert report.tickers == []
    assert report.notes is not None
    assert "META" in report.notes
    assert "1326801" in report.notes


def test_one_bad_ticker_does_not_block_a_good_one(monkeypatch, upserted):
    """A skip must not stop other tickers in the same run from writing rows."""
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["META", "WMT"], "cik": [1326801, 104169]}),
        {
            1326801: pd.DataFrame(columns=["filed_on", "end", "value", "accn"]),
            104169: pd.DataFrame(
                {"filed_on": ["2020-01-01"], "end": ["2019-12-31"], "value": [100]}
            ),
        },
    )

    report = ingest.run_shares(["META", "WMT"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["ticker"] == "WMT"
    assert report.tickers == ["WMT"]
    assert "META" in report.notes


# ============ 4. sec.py: dei-first, us-gaap fallback ============


def _response(payload: dict) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = json.dumps(payload).encode("utf-8")
    resp.encoding = "utf-8"
    return resp


@pytest.fixture(autouse=True)
def _sec_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "CapitalScan tests test@example.com")


def test_fetch_company_facts_prefers_dei_when_present(monkeypatch, tmp_path):
    cik = 104169
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{"filed": "2020-01-01", "end": "2019-12-31", "val": 42}]}
                }
            },
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {"shares": [{"filed": "2020-01-01", "end": "2019-12-31", "val": 999}]}
                }
            },
        }
    }

    def fake_get(url: str, **kwargs: Any) -> requests.Response:
        return _response(payload)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

    df = sec.fetch_company_facts(cik)

    assert list(df["value"]) == [42]
    assert list(df["tag"]) == ["EntityCommonStockSharesOutstanding"]


def test_fetch_company_facts_falls_back_to_us_gaap(monkeypatch, tmp_path):
    """Alphabet's real shape: `dei` empty, `us-gaap:CommonStockSharesOutstanding` populated."""
    cik = 1652044
    payload = {
        "facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {}}},
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "filed": "2016-02-11",
                                "end": "2014-12-31",
                                "val": 680172000,
                                "form": "10-K",
                                "accn": "0001652044-16-000012",
                            }
                        ]
                    }
                }
            },
        }
    }

    def fake_get(url: str, **kwargs: Any) -> requests.Response:
        return _response(payload)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

    df = sec.fetch_company_facts(cik)

    assert list(df["value"]) == [680172000]
    assert list(df["tag"]) == ["CommonStockSharesOutstanding"]


def test_fetch_company_facts_empty_when_neither_namespace_has_it(monkeypatch, tmp_path):
    """BRK-B's real shape: neither concept has a non-dimensional fact at all."""
    cik = 1067983
    payload = {"facts": {"dei": {}, "us-gaap": {}}}

    def fake_get(url: str, **kwargs: Any) -> requests.Response:
        return _response(payload)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

    df = sec.fetch_company_facts(cik)

    assert df.empty
