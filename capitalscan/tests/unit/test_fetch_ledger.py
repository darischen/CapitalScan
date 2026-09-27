"""`fetch_ledger` stops `shares` paying for the same Yahoo calls nightly.

~280 depositary, ETF and SEC-stale tickers took the Yahoo fallback every
night at 2 s each (the cache key carries today's date), ~9.5 of the step's
~10.4 minutes, measured 2026-09-27. A ticker fetched within
`SHARES_YAHOO_REFETCH_DAYS` now skips it; its rows are already on file.
"""

from __future__ import annotations

import pandas as pd

from capitalscan.jobs import ingest
from capitalscan.tests.unit.test_shares_yahoo_fallback import (
    TODAY,
    _FakeEngine,
    _stub_sec,
    upserted,  # noqa: F401 - pytest fixture
)


def _setup(monkeypatch, fresh: set[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    calls: list[str] = []
    recorded: list[tuple[str, list[str]]] = []
    _stub_sec(monkeypatch, pd.DataFrame({"ticker": [], "cik": []}), {})
    monkeypatch.setattr(ingest, "_depositary_tickers", lambda engine, tickers: set())

    def _full(ticker, start, end):
        calls.append(ticker)
        return pd.DataFrame(columns=["ticker", "filed_on", "shares"])

    monkeypatch.setattr(ingest.yahoo, "fetch_shares_full", _full)
    monkeypatch.setattr(ingest, "_ledger_fresh", lambda e, source, t, days: set(fresh))
    monkeypatch.setattr(
        ingest, "_ledger_record", lambda e, source, t: recorded.append((source, list(t)))
    )
    return calls, recorded


def test_a_recently_fetched_ticker_skips_the_yahoo_call(monkeypatch, upserted):  # noqa: F811
    calls, recorded = _setup(monkeypatch, fresh={"OLDA"})
    report = ingest.run_shares(["OLDA", "OLDB"], engine=_FakeEngine(), as_of=TODAY)
    assert calls == ["OLDB"], "OLDA was fetched within the window"
    assert "skipped the Yahoo fallback" in (report.notes or "")


def test_every_attempt_that_did_not_raise_is_recorded(monkeypatch, upserted):  # noqa: F811
    _calls, recorded = _setup(monkeypatch, fresh=set())
    ingest.run_shares(["OLDA", "OLDB"], engine=_FakeEngine(), as_of=TODAY)
    assert recorded == [(ingest.LEDGER_YAHOO_SHARES, ["OLDA", "OLDB"])]


def test_a_raise_is_not_recorded_so_it_retries(monkeypatch, upserted):  # noqa: F811
    _calls, recorded = _setup(monkeypatch, fresh=set())

    def _boom(ticker, start, end):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(ingest.yahoo, "fetch_shares_full", _boom)
    ingest.run_shares(["OLDA"], engine=_FakeEngine(), as_of=TODAY)
    assert recorded == [(ingest.LEDGER_YAHOO_SHARES, [])]


def test_the_window_is_short_against_a_quarterly_universe():
    assert 1 <= ingest.SHARES_YAHOO_REFETCH_DAYS <= 30
