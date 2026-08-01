"""yfinance current-shares-outstanding fallback for `run_shares` (BUILD follow-up).

Background: SEC's `dei:EntityCommonStockSharesOutstanding` feed does not
degrade, it disappears the quarter a filer starts reporting shares per
class of stock (`fetch/sec.py` `_SHARES_TAG_SOURCES`). Across the live
629-ticker universe this leaves 41 tickers with zero SEC rows and 28 with
only pre-2024 rows — BRK-B's latest is 2011-05-06, MA's 2010-11-02, V's
2010-02-03. A 2011 share count multiplied by a 2026 price turned BRK-B's
~$1T market cap into ~$480M, silently failing ADR 014's `crit_mcap` test
(`min_mcap_usd = 200e9`) it actually clears by 5x.

`yfinance.Ticker.get_shares_full(start, end)` was verified live (see the
session notes) to return a dated `pd.Series` of share counts reaching back
to ~2015 for BRK-B, MA, V, and META alike — a genuine point-in-time series,
not "current, held constant backward" (the exact anti-pattern DESIGN §2.4
names and rejects for AAPL's buybacks). That is why `fetch_shares_full`
(jobs/fetch/yahoo.py) wraps `get_shares_full` rather than
`Ticker.info["sharesOutstanding"]`, and why every point in the window is
written, not only the most recent one.

No database and no network here: the engine, `sec.*`, and `yahoo.*` are all
stubbed, per this suite's `capitalscan/tests/unit` scope (integration tests
own real IO and truncate tables — never run those, per this session's
constraints).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import ingest
from capitalscan.jobs.fetch import yahoo as _yahoo_module

# `capitalscan/tests/unit/conftest.py` autouse-patches
# `ingest.yahoo.fetch_shares_full` (the same module object as `_yahoo_module`
# here) to an empty stub for every test in this suite, so a fixture whose SEC
# dates are years stale can't make a live yfinance call by accident. The two
# tests below exercise `fetch_shares_full` itself, so they restore the real
# implementation, captured here at import time before any fixture runs.
_REAL_FETCH_SHARES_FULL = _yahoo_module.fetch_shares_full


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


def _stub_sec(monkeypatch, cik_lookup: pd.DataFrame, facts_by_cik: dict[int, pd.DataFrame]) -> None:
    monkeypatch.setattr(ingest.sec, "fetch_cik_lookup", lambda: cik_lookup)
    monkeypatch.setattr(
        ingest.sec, "fetch_company_facts", lambda cik: facts_by_cik.get(cik, pd.DataFrame())
    )


TODAY = date(2026, 8, 1)


# ============ 1. yahoo.fetch_shares_full: the fetch-layer fetcher ============


def test_fetch_shares_full_tidies_a_dated_series(monkeypatch, tmp_path):
    """`get_shares_full` returns a `pd.Series` indexed by date; the fetcher
    must tidy it to `ticker`/`filed_on`/`shares` columns, one row per point,
    not collapse it to a single current value.
    """
    from capitalscan.jobs.fetch import base, yahoo

    # `@cached` resolves `CACHE_ROOT` from `jobs.fetch.base` at call time
    # (see `base.py`'s `cached` docstring) precisely so this redirect works
    # even though `fetch_shares_full` was decorated at import time.
    monkeypatch.setattr(base, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(yahoo, "fetch_shares_full", _REAL_FETCH_SHARES_FULL)
    series = pd.Series(
        [2465050000, 2463840000, 2157334755],
        index=pd.to_datetime(["2015-11-06", "2016-03-11", "2026-03-07"]),
    )

    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_shares_full(self, start, end):
            return series

    monkeypatch.setattr(yahoo.yf, "Ticker", _FakeTicker)

    out = yahoo.fetch_shares_full("BRK-B", date(2015, 1, 1), TODAY)

    assert list(out.columns) == ["ticker", "filed_on", "shares"]
    assert len(out) == 3
    assert out["filed_on"].tolist() == [date(2015, 11, 6), date(2016, 3, 11), date(2026, 3, 7)]
    assert out["shares"].tolist() == [2465050000, 2463840000, 2157334755]
    assert (out["ticker"] == "BRK-B").all()


def test_fetch_shares_full_empty_when_yahoo_has_nothing(monkeypatch, tmp_path):
    """`get_shares_full` returns `None` for a symbol Yahoo has no data for
    (confirmed live against an invalid symbol) — must come back as an empty
    frame, not raise, so `run_shares` can log a skip instead of failing.
    """
    from capitalscan.jobs.fetch import base, yahoo

    monkeypatch.setattr(base, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(yahoo, "fetch_shares_full", _REAL_FETCH_SHARES_FULL)

    class _FakeTicker:
        def __init__(self, symbol):
            pass

        def get_shares_full(self, start, end):
            return None

    monkeypatch.setattr(yahoo.yf, "Ticker", _FakeTicker)

    out = yahoo.fetch_shares_full("ZZZZ", date(2015, 1, 1), TODAY)
    assert out.empty
    assert list(out.columns) == ["ticker", "filed_on", "shares"]


# ============ 2. run_shares: staleness-gated fallback ============


def test_missing_sec_data_triggers_yahoo_fallback(monkeypatch, upserted):
    """A ticker with zero SEC rows (BRK-B/MA/V's actual situation) must be
    backed by the yfinance fallback, tagged with its own `source` value.
    """
    _stub_sec(monkeypatch, pd.DataFrame({"ticker": ["BRKB"], "cik": [1]}), {1: pd.DataFrame()})
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_shares_full",
        lambda ticker, start, end: pd.DataFrame(
            {
                "ticker": [ticker, ticker],
                "filed_on": [date(2025, 11, 8), date(2026, 5, 5)],
                "shares": [2157520735, 2156853797],
            }
        ),
    )

    report = ingest.run_shares(["BRKB"], engine=_FakeEngine(), as_of=TODAY)

    assert len(upserted) == 2
    assert {r["source"] for r in upserted} == {ingest.SHARES_YAHOO_SOURCE}
    assert {r["filed_on"] for r in upserted} == {date(2025, 11, 8), date(2026, 5, 5)}
    assert "BRKB" in report.tickers
    assert "yahoo_shares_full" in (report.notes or "")


def test_stale_sec_data_triggers_yahoo_fallback(monkeypatch, upserted):
    """SEC's only fact is older than `SHARES_STALENESS_DAYS` before `as_of`
    — the exact BRK-B shape (latest filing 2011-05-06, `as_of` 2026-08-01,
    ~15 years stale, nowhere near the ~15-month threshold). Must fall back.
    """
    _stub_sec(
        monkeypatch,
        pd.DataFrame({"ticker": ["BRKB"], "cik": [1]}),
        {
            1: pd.DataFrame(
                {
                    "filed_on": ["2011-05-06"],
                    "end": ["2011-03-31"],
                    "value": [1650000000],
                    "accn": ["0001-11-000001"],
                }
            )
        },
    )
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_shares_full",
        lambda ticker, start, end: pd.DataFrame(
            {"ticker": [ticker], "filed_on": [date(2026, 5, 5)], "shares": [2156853797]}
        ),
    )

    report = ingest.run_shares(["BRKB"], engine=_FakeEngine(), as_of=TODAY)

    sources = {r["source"] for r in upserted}
    assert sources == {"sec_xbrl", ingest.SHARES_YAHOO_SOURCE}
    fresh = [r for r in upserted if r["source"] == ingest.SHARES_YAHOO_SOURCE]
    assert fresh == [
        {
            "ticker": "BRKB",
            "filed_on": date(2026, 5, 5),
            "period_end": None,
            "shares": 2156853797,
            "source": ingest.SHARES_YAHOO_SOURCE,
        }
    ]
    assert "BRKB" in report.tickers


def test_fresh_sec_data_does_not_trigger_yahoo_fallback(monkeypatch, upserted):
    """SEC's latest filing is within `SHARES_STALENESS_DAYS` of `as_of` —
    the fallback must not fire (no wasted network call, no risk of a Yahoo
    row overwriting a fresh SEC one).
    """
    _stub_sec(
        monkeypatch,
        pd.DataFrame({"ticker": ["AAPL"], "cik": [2]}),
        {
            2: pd.DataFrame(
                {
                    "filed_on": ["2026-05-01"],
                    "end": ["2026-03-31"],
                    "value": [15000000000],
                    "accn": ["0002-26-000001"],
                }
            )
        },
    )

    def _boom(ticker, start, end):
        raise AssertionError("fetch_shares_full must not be called when SEC data is fresh")

    monkeypatch.setattr(ingest.yahoo, "fetch_shares_full", _boom)

    report = ingest.run_shares(["AAPL"], engine=_FakeEngine(), as_of=TODAY)

    assert len(upserted) == 1
    assert upserted[0]["source"] == "sec_xbrl"
    assert ingest.SHARES_YAHOO_SOURCE not in (report.notes or "")


def test_yahoo_row_never_overwrites_an_sec_row_sharing_filed_on(monkeypatch, upserted):
    """A same-dated collision between a stale SEC row and a Yahoo row is
    resolved in favor of the SEC row: `db_io.upsert` has no per-column
    merge policy, and a single upsert batch containing both would collide
    on `ON CONFLICT (ticker, filed_on)` against itself.
    """
    _stub_sec(
        monkeypatch,
        pd.DataFrame({"ticker": ["MA"], "cik": [3]}),
        {
            3: pd.DataFrame(
                {
                    "filed_on": ["2010-11-02"],
                    "end": ["2010-09-30"],
                    "value": [500000000],
                    "accn": ["0003-10-000001"],
                }
            )
        },
    )
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_shares_full",
        lambda ticker, start, end: pd.DataFrame(
            {
                "ticker": [ticker, ticker],
                "filed_on": [date(2010, 11, 2), date(2026, 5, 5)],
                "shares": [999999999, 883583855],
            }
        ),
    )

    ingest.run_shares(["MA"], engine=_FakeEngine(), as_of=TODAY)

    collision_row = next(
        r for r in upserted if r["source"] == "sec_xbrl" and r["filed_on"] == "2010-11-02"
    )
    assert collision_row["source"] == "sec_xbrl"
    assert collision_row["shares"] == 500000000
    assert len(upserted) == 2  # the SEC row plus the one non-colliding Yahoo row


def test_yahoo_fetch_failure_is_a_skip_not_a_job_failure(monkeypatch, upserted):
    """A yfinance network error must land in `report.notes`, not propagate
    and fail the whole `shares` run for every other ticker in the batch.
    """
    _stub_sec(monkeypatch, pd.DataFrame({"ticker": ["ZZZ"], "cik": [4]}), {4: pd.DataFrame()})

    def _raise(ticker, start, end):
        raise RuntimeError("yfinance is down")

    monkeypatch.setattr(ingest.yahoo, "fetch_shares_full", _raise)

    report = ingest.run_shares(["ZZZ"], engine=_FakeEngine(), as_of=TODAY)

    assert upserted == []
    assert "ZZZ" in (report.notes or "")
    assert "yfinance is down" in (report.notes or "")
