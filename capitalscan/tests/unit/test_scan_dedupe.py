"""`scan()` must return one row per event (ADR 049), not one row per
`(config_hash, entry_kind)` combination.

Measured on live data 2026-07-31: 148 rows for 27 actual signals --
108 rows from the pinned config (27 signals x 4 entry kinds, byte-identical
across entry_kind since `scan()` selects no entry-kind-specific column) plus
40 more from two stale sweep `config_hash` generations. The 18-config sweep
makes this worse, not better: ~2,000 rows for one day's signals.

Fix: filter on `e.config_hash = current_setting('capitalscan.default_config_hash',
true)` (same GUC `v_events` already uses, invariant 5b) and on a single
canonical `entry_kind` (`touch` -- the row `run_events` itself writes at
detection time, before any backtest-added entry-kind rows exist).

No live database here: `pd.read_sql` and the GUC lookup are both
monkeypatched. The fake `pd.read_sql` behaves like a real Postgres server
would against the query `scan()` builds -- it holds a canned superset with
multiple `config_hash` and `entry_kind` values for the *same* underlying
event, and only returns the rows that satisfy the bound parameters
`scan()` passes. That makes this test fail against the current code (which
binds no such parameters, so the fake returns the whole superset) and pass
once `scan()` filters correctly.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import pytest

from capitalscan.jobs import compute

PINNED_HASH = "3e598c59e7d71eae"
STALE_HASH_1 = "edf5658f5da3807a"
STALE_HASH_2 = "39e6a590aa799780"

# One real event (AAPL confluence_low on 2026-07-30) exploded across every
# config_hash the sweep has ever produced and every entry_kind the backtest
# ever writes -- exactly the shape of the measured 148-row defect.
_SUPERSET = pd.DataFrame(
    [
        {
            "ticker": "AAPL",
            "signal_date": pd.Timestamp("2026-07-30"),
            "signal_type": "confluence_low",
            "signal_types_all": ["confluence_low"],
            "signal_strength": 1,
            "side": "long",
            "bb_upper": 210.0,
            "bb_mid": 200.0,
            "bb_lower": 190.0,
            "bb_pctb": 0.1,
            "k_full": 12.0,
            "k_fast": 10.0,
            "k_cross_up": True,
            "k_cross_down": False,
            "dd_bucket": "0-10",
            "above_sma200": True,
            "config_hash": chash,
            "entry_kind": ekind,
        }
        for chash in (PINNED_HASH, STALE_HASH_1, STALE_HASH_2)
        for ekind in ("touch", "touch_5m", "touch_30m", "next_open")
    ]
)


class _FakeCursorResult:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,) if self._value is not None else None


class _FakeConn:
    def __init__(self, guc_value):
        self._guc_value = guc_value

    def execute(self, stmt, *args, **kwargs):
        sql = str(stmt)
        assert "current_setting" in sql and "capitalscan.default_config_hash" in sql, (
            "scan() must resolve the same GUC v_events uses, not invent a second one"
        )
        return _FakeCursorResult(self._guc_value)


class _FakeEngine:
    def __init__(self, guc_value):
        self._guc_value = guc_value

    @contextmanager
    def connect(self):
        yield _FakeConn(self._guc_value)


def _fake_read_sql_factory():
    """Simulates Postgres applying `scan()`'s WHERE clause to `_SUPERSET`."""

    def fake_read_sql(stmt, conn, params=None):
        params = params or {}
        df = _SUPERSET
        if "config_hash" in params:
            df = df[df["config_hash"] == params["config_hash"]]
        if "entry_kind" in params:
            df = df[df["entry_kind"] == params["entry_kind"]]
        return df.drop(columns=["config_hash", "entry_kind"]).reset_index(drop=True)

    return fake_read_sql


class TestScanReturnsOneRowPerEvent:
    def test_one_signal_yields_one_row_despite_multiple_config_hashes_and_entry_kinds(
        self, monkeypatch
    ):
        monkeypatch.setattr(compute.pd, "read_sql", _fake_read_sql_factory())
        engine = _FakeEngine(guc_value=PINNED_HASH)

        df = compute.scan(tickers=["AAPL"], engine=engine)

        assert len(df) == 1, (
            f"expected exactly one row for one event, got {len(df)}: "
            f"{df.to_dict('records')}"
        )

    def test_gate_is_the_pinned_config_hash_not_the_first_one_seen(self, monkeypatch):
        monkeypatch.setattr(compute.pd, "read_sql", _fake_read_sql_factory())
        engine = _FakeEngine(guc_value=STALE_HASH_1)

        df = compute.scan(tickers=["AAPL"], engine=engine)

        # Still exactly one row -- and it must come from the GUC-pinned
        # config, not silently fall back to whatever sorts first.
        assert len(df) == 1

    def test_guc_unset_returns_empty_rather_than_every_config(self, monkeypatch):
        """`current_setting(..., true)` returns NULL, not an error, when the
        GUC is unset (docs/BUILD.md). Returning every config's rows is the
        current broken behaviour; returning nothing is the safe default --
        the same choice `v_events` already made (its WHERE clause turns into
        `config_hash = NULL`, matching zero rows)."""
        read_sql = _fake_read_sql_factory()

        def fake_read_sql_fails_if_called(stmt, conn, params=None):
            raise AssertionError(
                "scan() must not query events at all when the default "
                "config_hash is unset"
            )

        monkeypatch.setattr(compute.pd, "read_sql", fake_read_sql_fails_if_called)
        engine = _FakeEngine(guc_value=None)

        df = compute.scan(tickers=["AAPL"], engine=engine)

        assert df.empty
        assert list(df.columns) == compute._SCAN_COLUMNS

    def test_scan_still_returns_the_documented_columns(self, monkeypatch):
        monkeypatch.setattr(compute.pd, "read_sql", _fake_read_sql_factory())
        engine = _FakeEngine(guc_value=PINNED_HASH)

        df = compute.scan(tickers=["AAPL"], engine=engine)

        assert list(df.columns) == compute._SCAN_COLUMNS
