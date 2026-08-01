"""`run_shares` must collapse same-filing XBRL facts before the upsert.

`shares_outstanding` is keyed `PRIMARY KEY (ticker, filed_on)`, but one SEC
filing reports the share count for several periods, so a single `filed_on`
yields several facts with different `period_end` values. Postgres rejects an
`INSERT ... ON CONFLICT` whose proposed rows collide with each other:

    psycopg.errors.CardinalityViolation: ON CONFLICT DO UPDATE command
    cannot affect row a second time

Observed against the real 633-ticker universe: the job ran 540s and wrote
zero rows. Same defect shape as `run_earnings` — see test_earnings_dedup.py.

The survivor is the fact with the latest `period_end`, which is the most
current share count that filing reports. DESIGN §2.4 defers the
"latest filing with filed_on < as_of" selection to the `universe` job, so
what this table needs is one correct row per filing, not every fact.

No database here — the engine and both fetchers are stubbed.
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
    seen: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "shares_outstanding":
            seen.extend(data)
        return len(data)

    monkeypatch.setattr(ingest.db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    return seen


def _stub_facts(monkeypatch, facts: pd.DataFrame) -> None:
    monkeypatch.setattr(
        ingest.sec, "fetch_cik_lookup", lambda: pd.DataFrame({"ticker": ["AAA"], "cik": [1]})
    )
    monkeypatch.setattr(ingest.sec, "fetch_company_facts", lambda cik: facts)


def test_one_filing_yields_one_row_keeping_the_latest_period(monkeypatch, upserted):
    """Three facts filed the same day collapse to the most recent period."""
    _stub_facts(
        monkeypatch,
        pd.DataFrame(
            {
                "filed_on": ["2019-05-02", "2019-05-02", "2019-05-02"],
                "end": ["2018-10-18", "2019-04-18", "2019-01-18"],
                "value": [100, 300, 200],
            }
        ),
    )

    ingest.run_shares(["AAA"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["filed_on"] == "2019-05-02"
    assert upserted[0]["period_end"] == "2019-04-18"
    assert upserted[0]["shares"] == 300


def test_distinct_filings_are_all_kept(monkeypatch, upserted):
    """Deduplication is per filing, not per ticker."""
    _stub_facts(
        monkeypatch,
        pd.DataFrame(
            {
                "filed_on": ["2019-05-02", "2019-07-31"],
                "end": ["2019-04-18", "2019-07-17"],
                "value": [300, 400],
            }
        ),
    )

    ingest.run_shares(["AAA"], engine=_FakeEngine())

    assert sorted(r["filed_on"] for r in upserted) == ["2019-05-02", "2019-07-31"]


def test_a_missing_period_end_loses_to_a_real_one(monkeypatch, upserted):
    """A dated fact is more useful than an undated one from the same filing."""
    _stub_facts(
        monkeypatch,
        pd.DataFrame(
            {
                "filed_on": ["2019-05-02", "2019-05-02"],
                "end": [None, "2019-04-18"],
                "value": [100, 300],
            }
        ),
    )

    ingest.run_shares(["AAA"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["shares"] == 300
