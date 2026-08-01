"""Unit-suite guard: no test may reach real yfinance through `run_shares`'s
`yahoo.fetch_shares_full` fallback.

`run_shares` (jobs/ingest.py) calls this whenever a ticker's SEC data is
missing or older than `SHARES_STALENESS_DAYS` — a description most of this
suite's stubbed SEC fixtures satisfy by construction (dates from 2009 to
2020, well past the ~15-month staleness threshold measured against the
real wall-clock date). Without this guard, tests that were never written
to know about the fallback silently start making live HTTP requests to
Yahoo the moment their fixture data goes stale relative to today, which is
exactly the kind of test-suite time bomb this fixture exists to defuse.

Autouse rather than opt-in: a network call inside "unit" tests is a bug in
the test's isolation, not a scenario worth exercising here.
`test_shares_yahoo_fallback.py` overrides this per test via its own
`monkeypatch.setattr` to exercise the fallback deliberately.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.jobs import ingest


@pytest.fixture(autouse=True)
def _no_real_yahoo_shares_fallback(monkeypatch):
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_shares_full",
        lambda ticker, start, end: pd.DataFrame(columns=["ticker", "filed_on", "shares"]),
    )
