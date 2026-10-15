"""The two `tickers` writers must not blank columns they do not compute.

Found 2026-10-15. `run_tickers_refresh` has run in every nightly since
2026-09-01 and upserted each S&P constituent through `db_io.upsert` with
`update_columns=None`, which overwrites every non-key column from EXCLUDED.
Columns absent from its row dicts arrive as NULL, so twice a day it erased
`exchange`, `first_bar`, `last_bar` and `delisted_on` for ~500 tickers, and
`_update_ticker_coverage` then rebuilt `first_bar` from one night's lookback
window. On `wivie` 503 tickers carried a `first_bar` after 2026-09-01 over
years of bars.

`ensure_tickers` had the same shape with only `ticker` and `is_active`, so a
backfill naming an existing ticker would have blanked its CIK, name and
sector too. `run_exchange_expansion` already documented this hazard and
inserts with `DO NOTHING`; these tests hold the other two to the same rule.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd

from capitalscan.jobs import db_io, ingest

NOT_COMPUTED_BY_REFRESH = {"exchange", "first_bar", "last_bar", "delisted_on"}


@contextmanager
def _fake_run_job(engine, job, params):
    yield SimpleNamespace(rows_written=0, tickers=[], notes=None, run_id="test")


def _capture(monkeypatch):
    calls: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols, update_columns=None):
        calls.append(
            {"table": table_name, "rows": data, "conflict": conflict_cols, "update": update_columns}
        )
        return len(data)

    def fake_insert_new(engine, table_name, data, conflict_cols):
        calls.append(
            {"table": table_name, "rows": data, "conflict": conflict_cols, "update": "NONE"}
        )
        return len(data)

    monkeypatch.setattr(db_io, "upsert", fake_upsert)
    monkeypatch.setattr(db_io, "insert_new", fake_insert_new)
    monkeypatch.setattr(ingest, "run_job", _fake_run_job)
    return calls


def test_refresh_updates_only_the_columns_it_computes(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        ingest.wikipedia,
        "fetch_current_constituents",
        lambda: pd.DataFrame(
            [
                {
                    "ticker": "BE",
                    "security": "Bloom Energy",
                    "gics_sector": "Industrials",
                    "gics_sub-industry": "Heavy Electrical Equipment",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        ingest.sec, "fetch_cik_lookup", lambda: pd.DataFrame({"ticker": ["BE"], "cik": ["1664703"]})
    )

    ingest.run_tickers_refresh(engine=object())

    (call,) = calls
    assert call["table"] == "tickers"
    assert call["update"] is not None, "update_columns=None overwrites every column with NULL"
    assert not NOT_COMPUTED_BY_REFRESH & set(call["update"])
    sent = set(call["rows"][0])
    missing = set(call["update"]) - (sent - {"ticker"})
    assert not missing, "every updated column must be one the row carries"


def test_ensure_tickers_never_updates_an_existing_row(monkeypatch):
    calls = _capture(monkeypatch)

    ingest.ensure_tickers(["be"], engine=object())

    (call,) = calls
    assert call["table"] == "tickers"
    assert call["update"] == "NONE", "a stub row for an FK must insert or do nothing"
    assert call["rows"] == [{"ticker": "BE", "is_active": True}]
