"""DEFECT 4 regression: `scan()` must display the t-1 indicator row, never t.

CLAUDE.md invariant 3: indicators are read at t-1, never t. `run_events`
already gets this right when it computes `bb_pctb`/`k_full` onto the event
row (it reads `ind_group.index[ind_group.index < bar_date]`); `scan()`'s
display join used to disagree with its own data by joining
`e.signal_date = i.ts` — the same-day (t) bands — which looks internally
consistent and is not.

Confirmed against live data (read-only SELECT, not reproduced here): for
TSM, `indicators` holds

    2026-07-29  bb_upper=456.523644  bb_mid=418.677000  bb_lower=380.830356
    2026-07-30  bb_upper=453.131161  bb_mid=416.631000  bb_lower=380.130839

A TSM event with `signal_date = 2026-07-30` must display the 07-29 row.

No database here: `pd.read_sql` is monkeypatched to capture the query text
and to hand back a canned frame, so this test proves the *query construction*
is correct (LATERAL join on `ts < signal_date`) without touching Postgres.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import pytest

from capitalscan.jobs import compute


class _FakeConn:
    pass


class _FakeEngine:
    @contextmanager
    def connect(self):
        yield _FakeConn()


@pytest.fixture()
def captured_query(monkeypatch):
    captured: dict[str, str] = {}

    def fake_read_sql(stmt, conn, params=None):
        captured["sql"] = str(stmt)
        return pd.DataFrame(
            {
                "ticker": ["TSM"],
                "signal_date": [pd.Timestamp("2026-07-30")],
                "signal_type": ["confluence_low"],
                "signal_types_all": [["confluence_low"]],
                "signal_strength": [1],
                "side": ["long"],
                "bb_upper": [456.523644],
                "bb_mid": [418.677000],
                "bb_lower": [380.830356],
                "bb_pctb": [0.1],
                "k_full": [12.0],
                "k_fast": [10.0],
                "k_cross_up": [True],
                "k_cross_down": [False],
                "dd_bucket": ["0-10"],
                "above_sma200": [True],
            }
        )

    monkeypatch.setattr(compute.pd, "read_sql", fake_read_sql)
    return captured


class TestScanIndicatorLagQuery:
    def test_join_condition_requires_indicator_ts_strictly_before_signal_date(self, captured_query):
        compute.scan(tickers=["TSM"], engine=_FakeEngine())
        sql = captured_query["sql"]

        assert "i2.ts < e.signal_date" in sql, (
            "the join must select the indicator row strictly before signal_date (t-1)"
        )
        assert "e.signal_date = i.ts" not in sql, (
            "must not re-introduce the same-day (t) join this defect fixed"
        )

    def test_join_is_lateral_so_it_resolves_per_row_not_per_ticker(self, captured_query):
        compute.scan(tickers=["TSM"], engine=_FakeEngine())
        sql = captured_query["sql"]

        assert "LEFT JOIN LATERAL" in sql
        assert "ORDER BY i2.ts DESC" in sql
        assert "LIMIT 1" in sql

    def test_scan_still_returns_the_documented_columns(self, captured_query):
        df = compute.scan(tickers=["TSM"], engine=_FakeEngine())

        assert list(df.columns) == compute._SCAN_COLUMNS
        row = df.iloc[0]
        assert row["bb_upper"] == pytest.approx(456.523644)
        assert row["bb_mid"] == pytest.approx(418.677000)
        assert row["bb_lower"] == pytest.approx(380.830356)
