"""Hourly bars must be back-adjusted for splits the way daily bars already
are (Session 9 hourly-split-adjust task).

Measured against the live database: 17 tickers carry 4,424 (ticker, day)
pairs where the unadjusted hourly range sits at an exact multiple of the
adjusted daily range — KLAC ~10x (its 2026-06-12 10:1 split), CRWD ~4x,
AMCR ~0.2x (a 1:5 reverse split). `run_bars_hourly` wrote hourly bars
straight from Yahoo with no adjustment and `corporate_actions=None`, which
also disabled `validate_bars`'s split-explained-large-move rule.

No database here, same reasoning as `test_bars_hourly_checkpoint.py`: these
stub the fetcher and the two new reads (`_read_corporate_actions`,
`_read_daily_range`) so they can run alongside a real backfill.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import ingest

START = date(2024, 1, 1)
END = date(2024, 3, 1)


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _FakeEngine:
    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()


def _hourly_frame(ticker: str, ts: pd.DatetimeIndex, price_offset: float = 0.0) -> pd.DataFrame:
    """A clean, monotonically rising bar series — no validate_bars noise."""
    n = len(ts)
    closes = [100.0 + price_offset + i for i in range(n)]
    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": ts,
            "open": [c - 0.2 for c in closes],
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000 + i for i in range(n)],
        }
    )


def _splits(rows: list[dict]) -> pd.DataFrame:
    cols = ["ticker", "ex_date", "action_type", "ratio", "amount"]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


@pytest.fixture()
def written(monkeypatch) -> dict[str, pd.DataFrame]:
    """Captures every frame `db_io.upsert` receives for `bars`, keyed by call order."""
    calls: list[pd.DataFrame] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "bars":
            calls.append(data.reset_index(drop=True))
        return len(data)

    rejects: list[dict] = []

    def fake_append(engine, table_name, rows):  # noqa: ANN001, ANN202
        if table_name == "bar_rejects":
            rejects.extend(rows)

    monkeypatch.setattr(ingest.db_io, "append", fake_append)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    monkeypatch.setattr(ingest, "_read_daily_range", lambda engine, tickers: pd.DataFrame(
        columns=["ticker", "d", "high", "low"]
    ))
    return {"bars": calls, "rejects": rejects}


def test_a_10_for_1_split_back_adjusts_pre_split_bars_only(monkeypatch, written):
    """The KLAC shape: bars before the ex-date divide by 10, bars on/after do not."""
    ts = pd.to_datetime(
        ["2024-01-02 09:30", "2024-01-02 10:30", "2024-01-05 09:30", "2024-01-05 10:30"]
    )
    raw = _hourly_frame("KLAC", ts)
    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", lambda t, s, e: raw)
    monkeypatch.setattr(
        ingest,
        "_read_corporate_actions",
        lambda engine, tickers: _splits(
            [{"ticker": "KLAC", "ex_date": date(2024, 1, 4), "action_type": "split", "ratio": 10}]
        ),
    )

    ingest.run_bars_hourly(["KLAC"], START, END, engine=_FakeEngine())

    out = written["bars"][0].set_index("ts")
    pre = out.loc["2024-01-02 09:30":"2024-01-02 10:30"]
    post = out.loc["2024-01-05 09:30":"2024-01-05 10:30"]

    raw_by_ts = raw.set_index("ts")
    for ts_val, row in pre.iterrows():
        assert row["close"] == pytest.approx(raw_by_ts.loc[ts_val, "close"] / 10, abs=1e-4)
        assert row["high"] == pytest.approx(raw_by_ts.loc[ts_val, "high"] / 10, abs=1e-4)
    for ts_val, row in post.iterrows():
        assert row["close"] == pytest.approx(raw_by_ts.loc[ts_val, "close"], abs=1e-4)


def test_a_reverse_split_adjusts_the_other_way(monkeypatch, written):
    """AMCR's shape: ratio 0.2 (1-for-5) — pre-split bars divide by 0.2, i.e. x5."""
    ts = pd.to_datetime(["2024-01-02 09:30", "2024-01-05 09:30"])
    raw = _hourly_frame("AMCR", ts)
    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", lambda t, s, e: raw)
    monkeypatch.setattr(
        ingest,
        "_read_corporate_actions",
        lambda engine, tickers: _splits(
            [{"ticker": "AMCR", "ex_date": date(2024, 1, 4), "action_type": "split", "ratio": 0.2}]
        ),
    )

    ingest.run_bars_hourly(["AMCR"], START, END, engine=_FakeEngine())

    out = written["bars"][0].set_index("ts")
    raw_by_ts = raw.set_index("ts")
    pre_close = out.loc["2024-01-02 09:30", "close"]
    post_close = out.loc["2024-01-05 09:30", "close"]
    assert pre_close == pytest.approx(raw_by_ts.loc["2024-01-02 09:30", "close"] / 0.2, abs=1e-4)
    assert post_close == pytest.approx(raw_by_ts.loc["2024-01-05 09:30", "close"], abs=1e-4)


def test_multiple_splits_compound(monkeypatch, written):
    """A bar before both splits divides by the product of both ratios."""
    ts = pd.to_datetime(["2024-01-02 09:30", "2024-01-16 09:30", "2024-01-30 09:30"])
    raw = _hourly_frame("TPL", ts)
    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", lambda t, s, e: raw)
    monkeypatch.setattr(
        ingest,
        "_read_corporate_actions",
        lambda engine, tickers: _splits(
            [
                {"ticker": "TPL", "ex_date": date(2024, 1, 10), "action_type": "split", "ratio": 3},
                {"ticker": "TPL", "ex_date": date(2024, 1, 20), "action_type": "split", "ratio": 3},
            ]
        ),
    )

    ingest.run_bars_hourly(["TPL"], START, END, engine=_FakeEngine())

    out = written["bars"][0].set_index("ts")
    raw_by_ts = raw.set_index("ts")
    # Before both ex-dates: divide by 3 * 3 = 9.
    assert out.loc["2024-01-02 09:30", "close"] == pytest.approx(
        raw_by_ts.loc["2024-01-02 09:30", "close"] / 9, abs=1e-4
    )
    # After the first ex-date, before the second: divide by 3.
    assert out.loc["2024-01-16 09:30", "close"] == pytest.approx(
        raw_by_ts.loc["2024-01-16 09:30", "close"] / 3, abs=1e-4
    )
    # After both: untouched.
    assert out.loc["2024-01-30 09:30", "close"] == pytest.approx(
        raw_by_ts.loc["2024-01-30 09:30", "close"], abs=1e-4
    )


def test_a_ticker_with_no_splits_is_untouched(monkeypatch, written):
    """The over-broad-fix guard: no split rows means factor 1.0 everywhere."""
    ts = pd.to_datetime(["2024-01-02 09:30", "2024-01-05 09:30"])
    raw = _hourly_frame("AAPL", ts)
    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", lambda t, s, e: raw)
    monkeypatch.setattr(
        ingest, "_read_corporate_actions", lambda engine, tickers: _splits([])
    )

    ingest.run_bars_hourly(["AAPL"], START, END, engine=_FakeEngine())

    out = written["bars"][0].set_index("ts")
    raw_by_ts = raw.set_index("ts")
    for ts_val in ts:
        assert out.loc[ts_val, "close"] == pytest.approx(raw_by_ts.loc[ts_val, "close"], abs=1e-4)


def test_rerunning_does_not_double_adjust(monkeypatch, written):
    """Idempotency: two runs of the same fetch produce identical output —
    the adjustment is recomputed from raw data each time, never applied to
    an already-adjusted stored value.
    """
    ts = pd.to_datetime(["2024-01-02 09:30", "2024-01-05 09:30"])
    raw = _hourly_frame("KLAC", ts)
    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", lambda t, s, e: raw)
    monkeypatch.setattr(
        ingest,
        "_read_corporate_actions",
        lambda engine, tickers: _splits(
            [{"ticker": "KLAC", "ex_date": date(2024, 1, 4), "action_type": "split", "ratio": 10}]
        ),
    )

    ingest.run_bars_hourly(["KLAC"], START, END, engine=_FakeEngine())
    ingest.run_bars_hourly(["KLAC"], START, END, engine=_FakeEngine())

    first, second = written["bars"][0], written["bars"][1]
    pd.testing.assert_frame_equal(
        first.set_index("ts")[["open", "high", "low", "close"]],
        second.set_index("ts")[["open", "high", "low", "close"]],
    )


def test_a_ratio_escaping_the_daily_range_by_more_than_noise_is_rejected(monkeypatch):
    """The durable guard: if corporate_actions is missing a split (the
    exact bug this task exists to prevent from recurring), an unadjusted
    hourly day whose range escapes its daily counterpart by more than the
    noise tolerance is rejected, not silently upserted.
    """
    ts = pd.to_datetime(["2024-01-02 09:30", "2024-01-02 10:30"])
    # Raw hourly at ~10x a $100 daily bar — KLAC's shape, but with the split
    # NOT in corporate_actions, so back-adjustment can't fix it.
    raw = pd.DataFrame(
        {
            "ticker": "KLAC",
            "ts": ts,
            "open": [995.0, 1005.0],
            "high": [1010.0, 1020.0],
            "low": [990.0, 1000.0],
            "close": [1000.0, 1010.0],
            "volume": [1_000, 1_100],
        }
    )
    upserted: list[pd.DataFrame] = []
    rejects: list[dict] = []

    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly", lambda t, s, e: raw)
    monkeypatch.setattr(ingest, "_read_corporate_actions", lambda engine, tickers: _splits([]))
    monkeypatch.setattr(
        ingest,
        "_read_daily_range",
        lambda engine, tickers: pd.DataFrame(
            {"ticker": ["KLAC"], "d": [date(2024, 1, 2)], "high": [102.0], "low": [98.0]}
        ),
    )
    monkeypatch.setattr(
        ingest.db_io,
        "upsert",
        lambda engine, table, data, conflict_cols: (upserted.append(data), len(data))[1],
    )
    monkeypatch.setattr(
        ingest.db_io,
        "append",
        lambda engine, table, rows: rejects.extend(rows) if table == "bar_rejects" else None,
    )

    report = ingest.run_bars_hourly(["KLAC"], START, END, engine=_FakeEngine())

    assert upserted[0].empty or len(upserted[0]) == 0
    assert any(r["rule"] == "hourly_daily_range_escape" for r in rejects)
    assert report.rows_rejected == 2
