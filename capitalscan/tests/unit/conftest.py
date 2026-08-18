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


# ---------------------------------------------------------------------------
# Handler layer (session 15)
# ---------------------------------------------------------------------------
#
# The handlers are the first modules in `capitalscan/` whose whole job is to
# turn database rows into typed results, and the fast tier has no database.
# Stubbing `_db.rows` rather than the individual handler fetches is
# deliberate: it leaves the predicate building, the parameter binding, and
# the row mapping under test, and replaces only the connection. A fixture
# that stubbed `_fetch_feed` would test the mapper and skip the query, which
# is where the ADR-shaped decisions live.


class FakeDb:
    """A `_db.rows` stand-in that routes on SQL substrings.

    Register a response with `.on("FROM cell_stats", [row, ...])`. Later
    registrations win, so a test can override a fixture default. Every call
    is recorded on `.calls` as `(sql, params)`, which is how the tests
    assert on predicates a result cannot show - `split_key = ANY(:splits)`
    never appears in an `EventList`, and it is the thing that keeps holdout
    out.
    """

    def __init__(self, config_hash="testhash", first_bar=None, last_bar=None, stale_days=0):
        import datetime as _dt

        self.config_hash = config_hash
        self.first_bar = first_bar or _dt.date(2010, 1, 4)
        self.last_bar = last_bar or _dt.date(2026, 8, 17)
        self.stale_days = stale_days
        self.calls: list[tuple[str, dict]] = []
        self._routes: list[tuple[str, object]] = []

    def on(self, fragment: str, rows):
        self._routes.insert(0, (fragment, rows))
        return self

    def __call__(self, engine, sql, params=None):
        self.calls.append((sql, dict(params or {})))
        for fragment, rows in self._routes:
            if fragment in sql:
                return list(rows) if not callable(rows) else list(rows(sql, params or {}))
        if "current_setting" in sql:
            return [{"chash": self.config_hash}]
        if "min(ts)" in sql:
            return [{"first_ts": self.first_bar, "last_ts": self.last_bar}]
        if "FROM trading_days" in sql:
            return [{"n": self.stale_days}]
        if "count(*)" in sql:
            return [{"n": 0}]
        return []

    def sql_containing(self, fragment: str) -> list[str]:
        return [sql for sql, _ in self.calls if fragment in sql]


@pytest.fixture
def fake_db(monkeypatch):
    """`handlers._db.rows` replaced by a `FakeDb`, engine lookup disabled.

    `engine_or_default` is stubbed too. Without it every handler call would
    reach `db_io.get_engine()` and fail on a missing `DATABASE_URL_RESEARCH`
    in CI, which is a slower and less informative failure than the assertion
    the test is actually making.
    """
    from capitalscan.handlers import _db

    db = FakeDb()
    monkeypatch.setattr(_db, "rows", db)
    monkeypatch.setattr(_db, "engine_or_default", lambda engine: engine or object())
    return db
