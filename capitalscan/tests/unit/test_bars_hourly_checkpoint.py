"""`run_bars_hourly` must checkpoint (BUILD.md §5.4).

The full hourly backfill is a ~4.6 hour job: 725 days walked in 60-day
windows across ~630 tickers at 0.5 req/s. CLAUDE.md requires a checkpoint
on anything over 10 minutes. Accumulating every frame in memory and
writing once at the end means an interrupt at ticker 600 of 630 discards
all 600.

**The grain changed on 2026-08-26, from per ticker to per chunk.**
BUILD.md §5.4 says "checkpoint per ticker", which was right when each
ticker also cost ~2s of network: the write was free beside the fetch. Once
`fetch_bars_hourly_many` batched the fetch, the per-ticker commit *was* the
step -- 1,470 database round trips against ~60 seconds of fetching, and
~29 of the 30 minutes a nightly spent here.

`HOURLY_WRITE_CHUNK` tickers are buffered and committed together. The key
is still `(ticker, ts, interval)`, so a restart rewrites rather than
duplicates; what a failure now costs is one chunk instead of one ticker.
That is a real weakening of the guarantee and it is deliberate.

**These tests were red on `main` before this change**, and had been since
the fetch was batched: they stubbed `fetch_bars_hourly`, the call site
moved to `fetch_bars_hourly_many`, the stub stopped being reached, and
`written["bars"][0]` raised `IndexError`. Nothing about the batching was
wrong -- the tests simply were not run.

No database here on purpose. These tests stub the engine and the fetcher
so they can run while a real backfill is in flight —
`tests/integration/test_ingest.py` truncates `bars` and `tickers`, so it
is not safe to run concurrently with one.
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
    """Satisfies `_finish_run`'s `engine.begin()` without a real connection."""

    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()


def _hourly_frame(ticker: str) -> pd.DataFrame:
    ts = pd.date_range("2024-01-02 09:30", periods=6, freq="h")
    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": ts,
            "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "high": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "low": [99.5, 100.5, 101.5, 102.5, 103.5, 104.5],
            "close": [100.2, 101.2, 102.2, 103.2, 104.2, 105.2],
            "volume": [1_000, 1_100, 1_200, 1_300, 1_400, 1_500],
        }
    )


@pytest.fixture()
def upserted(monkeypatch) -> list[str]:
    """Records the ticker of every batch reaching `db_io.upsert`, in order."""
    seen: list[str] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "bars" and len(data):
            seen.extend(sorted(set(data["ticker"])))
        return len(data)

    monkeypatch.setattr(ingest.db_io, "append", lambda *a, **k: None)
    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    # No splits, no daily bars on file — these tests exercise checkpointing,
    # not the split-adjustment/range-escape guard added alongside it (see
    # test_bars_hourly_split_adjust.py), and `_FakeEngine` has no `.connect()`
    # for the two reads those features need.
    monkeypatch.setattr(
        ingest,
        "_read_corporate_actions",
        lambda engine, tickers: pd.DataFrame(
            columns=["ticker", "ex_date", "action_type", "ratio", "amount"]
        ),
    )
    monkeypatch.setattr(
        ingest,
        "_read_daily_range",
        lambda engine, tickers: pd.DataFrame(columns=["ticker", "d", "high", "low"]),
    )
    return seen


def test_a_committed_chunk_survives_a_later_failure(monkeypatch, upserted):
    """An interrupt must not discard work that was already committed.

    **Rewritten 2026-08-26, and the guarantee genuinely changed.** This
    asserted that a raise on ticker 3 of 3 still persisted tickers 1 and 2,
    which was true while the loop fetched *and wrote* one ticker at a time.
    Neither is true now: `fetch_bars_hourly_many` fetches every ticker up
    front, so a per-ticker fetch failure mid-loop is no longer a shape the
    code can take, and writes are buffered per `HOURLY_WRITE_CHUNK`.

    What remains, and what this pins: a chunk that reached the database is
    durable, and a failure costs at most the chunk in flight. `chunk = 1`
    here so the boundary is observable in three tickers rather than 101.
    """
    monkeypatch.setattr(ingest, "HOURLY_WRITE_CHUNK", 1)
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_bars_hourly_many",
        lambda tickers, start, end: {t: _hourly_frame(t) for t in tickers},
    )

    real_upsert = ingest.db_io.upsert

    def failing_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "bars" and "CCC" in set(data["ticker"]):
            raise RuntimeError("interrupted")
        return real_upsert(engine, table_name, data, conflict_cols)

    monkeypatch.setattr(ingest.db_io, "upsert", failing_upsert)

    with pytest.raises(RuntimeError, match="interrupted"):
        ingest.run_bars_hourly(["AAA", "BBB", "CCC"], START, END, engine=_FakeEngine())

    assert upserted == ["AAA", "BBB"]


def test_writes_are_chunked_rather_than_one_per_ticker(monkeypatch, upserted):
    """The property that removed 1,470 round trips from a nightly.

    Regressing to one write per ticker would not fail any other test here --
    every assertion above is about *which* tickers landed, not how many
    calls it took -- and would quietly put ~29 minutes back into the step.
    """
    calls: list[int] = []
    real_upsert = ingest.db_io.upsert

    def counting_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "bars":
            calls.append(len(set(data["ticker"])))
        return real_upsert(engine, table_name, data, conflict_cols)

    monkeypatch.setattr(ingest.db_io, "upsert", counting_upsert)
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_bars_hourly_many",
        lambda tickers, start, end: {t: _hourly_frame(t) for t in tickers},
    )

    tickers = [f"T{i:03d}" for i in range(5)]
    ingest.run_bars_hourly(tickers, START, END, engine=_FakeEngine())

    assert calls == [5], "five tickers under one chunk should be a single write"


def test_rows_written_accumulates_across_tickers(monkeypatch, upserted):
    """Per-ticker writes must still report one total, not just the last batch."""
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_bars_hourly_many",
        lambda tickers, start, end: {t: _hourly_frame(t) for t in tickers},
    )

    report = ingest.run_bars_hourly(["AAA", "BBB"], START, END, engine=_FakeEngine())

    assert upserted == ["AAA", "BBB"]
    assert report.rows_written == 12
    assert report.tickers == ["AAA", "BBB"]


def test_a_ticker_with_no_hourly_data_does_not_abort_the_rest(monkeypatch, upserted):
    """Delisted names return empty inside the 725-day window; skip, don't stop."""

    def fake_fetch(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        # A ticker with no bars is **absent from the dict**, which is what
        # `fetch_bars_hourly_many` does and what an empty frame meant before.
        return {t: _hourly_frame(t) for t in tickers if t != "DEAD"}

    monkeypatch.setattr(ingest.yahoo, "fetch_bars_hourly_many", fake_fetch)

    report = ingest.run_bars_hourly(["AAA", "DEAD", "BBB"], START, END, engine=_FakeEngine())

    assert upserted == ["AAA", "BBB"]
    assert report.tickers == ["AAA", "BBB"]
