"""`run_earnings` must collapse same-day 8-K filings before the upsert.

`earnings` is keyed `PRIMARY KEY (ticker, report_date)`. A company routinely
files more than one 8-K on a single day, so the ADR 036 proxy — treat every
8-K filing date as an earnings date — naturally produces duplicate
`(ticker, report_date)` pairs. Postgres rejects an `INSERT ... ON CONFLICT`
whose proposed rows collide with each other:

    psycopg.errors.CardinalityViolation: ON CONFLICT DO UPDATE command
    cannot affect row a second time

Observed against the real 633-ticker universe: the job ran 389s, hit this,
and wrote zero rows.

No database here — the engine and both fetchers are stubbed, so this is safe
to run while an ingest job holds the real one.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import pytest

from capitalscan.jobs import ingest


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _FakeEngine:
    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()


@pytest.fixture()
def upserted(monkeypatch) -> list[dict]:
    """Captures the rows handed to `db_io.upsert` for the `earnings` table."""
    seen: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "earnings":
            seen.extend(data)
        return len(data)

    monkeypatch.setattr(ingest.db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    return seen


def _stub_sec(monkeypatch, filings: dict[str, list[str]]) -> None:
    lookup = pd.DataFrame(
        {"ticker": list(filings), "cik": list(range(1, len(filings) + 1))}
    )
    monkeypatch.setattr(ingest.sec, "fetch_cik_lookup", lambda: lookup)

    by_cik = {i + 1: dates for i, dates in enumerate(filings.values())}
    monkeypatch.setattr(
        ingest.sec,
        "fetch_8k_dates",
        lambda cik: pd.DataFrame({"filed_on": by_cik[cik]}),
    )


def test_same_day_filings_collapse_to_one_row(monkeypatch, upserted):
    """Two 8-Ks on one day are one earnings date, not a primary-key collision."""
    _stub_sec(monkeypatch, {"AAA": ["2024-03-01", "2024-03-01", "2024-05-01"]})

    ingest.run_earnings(["AAA"], historical=True, engine=_FakeEngine())

    keys = [(r["ticker"], r["report_date"]) for r in upserted]
    assert keys == [("AAA", "2024-03-01"), ("AAA", "2024-05-01")]


def test_same_date_across_tickers_is_kept(monkeypatch, upserted):
    """The key is (ticker, report_date) — a shared date is not a duplicate."""
    _stub_sec(monkeypatch, {"AAA": ["2024-03-01"], "BBB": ["2024-03-01"]})

    ingest.run_earnings(["AAA", "BBB"], historical=True, engine=_FakeEngine())

    keys = sorted((r["ticker"], r["report_date"]) for r in upserted)
    assert keys == [("AAA", "2024-03-01"), ("BBB", "2024-03-01")]


def test_blank_filing_dates_are_dropped(monkeypatch, upserted):
    """Existing behaviour: a filing with no date contributes nothing."""
    _stub_sec(monkeypatch, {"AAA": ["", "2024-03-01"]})

    ingest.run_earnings(["AAA"], historical=True, engine=_FakeEngine())

    assert [(r["ticker"], r["report_date"]) for r in upserted] == [("AAA", "2024-03-01")]
