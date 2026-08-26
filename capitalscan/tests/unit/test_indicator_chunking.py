"""`run_indicators` computes and writes in chunks.

It used to compute every ticker, hold every frame, `pd.concat` them, and
convert the whole result to dicts for one upsert. Measured 2026-08-26 on a
1,462-ticker full-history run: 11 GB resident, 2.5 GB free of 32, and write
throughput decaying from ~900 rows/s to ~46 as memory filled.

The second cost was diagnostic rather than mechanical: with one write at
the end, nothing landed until every ticker finished, so a mid-run
`count(*)` returned the pre-run number and looked exactly like a hang. Two
working runs were killed for that reason.

These tests use a stubbed `_compute_one_ticker`, so they need no database
and no Yahoo.
"""

from __future__ import annotations

import inspect
from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import compute


@pytest.fixture
def frame_for():
    """One row per ticker, shaped like `compute_all` output."""

    def _make(ticker: str) -> pd.DataFrame:
        idx = pd.DatetimeIndex([pd.Timestamp("2020-01-02")])
        row: dict[str, object] = {c: 1.0 for c in compute.INDICATOR_COLUMNS}
        row["ticker"] = ticker
        return pd.DataFrame([row], index=idx)

    return _make


@pytest.fixture
def spy(monkeypatch, frame_for):
    """Record every write, and every group the pool was handed."""
    calls: dict[str, list] = {"writes": [], "groups": []}

    def _compute(ticker, *_a, **_k):
        return ticker, frame_for(ticker)

    def _upsert(engine, table, data, keys, update_columns=None):
        calls["writes"].append(len(data))
        return len(data)

    def _merge(engine, merged):
        merged = merged.copy()
        merged["days_to_earnings"] = None
        return merged

    monkeypatch.setattr(compute, "_compute_one_ticker", _compute)
    monkeypatch.setattr(compute.db_io, "upsert", _upsert)
    monkeypatch.setattr(compute, "_merge_days_to_earnings", _merge)
    monkeypatch.setattr(compute.db_io, "append", lambda *a, **k: None)
    return calls


class _FakeReport:
    def __init__(self):
        self.run_id = "test_run"
        self.rows_written = 0
        self.rows_flagged = 0
        self.tickers: list[str] = []
        self.notes = None


@pytest.fixture
def no_run_job(monkeypatch):
    """`run_job` writes a `runs` row; these tests have no database."""
    import contextlib

    @contextlib.contextmanager
    def _fake(engine, job, params):
        yield _FakeReport()

    monkeypatch.setattr(compute, "run_job", _fake)


@pytest.fixture
def fake_engine(monkeypatch):
    class _URL:
        def render_as_string(self, hide_password=True):
            return "postgresql+psycopg://u:p@localhost/db"

    class _Engine:
        url = _URL()

    monkeypatch.setattr(compute.db_io, "get_engine", lambda *a, **k: _Engine())
    return _Engine()


# ---------------------------------------------------------------------------
# The chunking itself
# ---------------------------------------------------------------------------


def test_it_writes_once_per_chunk_not_once_per_job(spy, no_run_job, fake_engine):
    """The property that bounds memory. One write at the end is what held
    11 GB; ten writes hold a tenth of it."""
    tickers = [f"T{i:03d}" for i in range(10)]
    compute.run_indicators(
        tickers, date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=3
    )
    # 10 tickers at 3 per chunk = 4 chunks
    assert len(spy["writes"]) == 4


def test_every_ticker_is_still_written(spy, no_run_job, fake_engine):
    """Chunking must not drop the remainder. 10 tickers at 3 per chunk
    leaves a final group of 1, and an off-by-one here loses real data
    silently."""
    tickers = [f"T{i:03d}" for i in range(10)]
    compute.run_indicators(
        tickers, date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=3
    )
    assert sum(spy["writes"]) == 10


def test_a_single_chunk_behaves_like_the_old_path(spy, no_run_job, fake_engine):
    """`chunk_size` larger than the ticker list is one write, which is what
    the code did before. The change must be a strict generalisation."""
    tickers = [f"T{i:03d}" for i in range(5)]
    compute.run_indicators(
        tickers, date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=999
    )
    assert len(spy["writes"]) == 1
    assert sum(spy["writes"]) == 5


def test_rows_written_accumulates_across_chunks(spy, no_run_job, fake_engine):
    """Reported separately because `report.rows_written` used to be assigned
    from a single upsert. Accumulating wrongly would under-report a run that
    worked."""
    tickers = [f"T{i:03d}" for i in range(7)]
    rep = compute.run_indicators(
        tickers, date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=2
    )
    assert rep.rows_written == 7


def test_computed_tickers_accumulate_across_chunks(spy, no_run_job, fake_engine):
    tickers = [f"T{i:03d}" for i in range(7)]
    rep = compute.run_indicators(
        tickers, date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=2
    )
    assert sorted(rep.tickers) == sorted(tickers)


def test_an_empty_ticker_list_writes_nothing(spy, no_run_job, fake_engine):
    rep = compute.run_indicators([], date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine)
    assert spy["writes"] == []
    assert rep.rows_written == 0


# ---------------------------------------------------------------------------
# Skips still work across chunks
# ---------------------------------------------------------------------------


def test_skipped_tickers_are_collected_across_every_chunk(
    monkeypatch, no_run_job, fake_engine, frame_for
):
    """A ticker with too little history returns `None` and is logged to
    `bar_rejects`. Collecting per chunk and overwriting would report only
    the last chunk's skips.
    """
    flagged: list[list] = []

    def _compute(ticker, *_a, **_k):
        return (ticker, None) if ticker.endswith(("0", "5")) else (ticker, frame_for(ticker))

    monkeypatch.setattr(compute, "_compute_one_ticker", _compute)
    monkeypatch.setattr(compute.db_io, "upsert", lambda *a, **k: len(a[2]))
    monkeypatch.setattr(
        compute, "_merge_days_to_earnings", lambda e, m: m.assign(days_to_earnings=None)
    )
    monkeypatch.setattr(compute.db_io, "append", lambda e, t, rows: flagged.append(rows))

    tickers = [f"T{i:03d}" for i in range(10)]  # T000,T005 skipped
    rep = compute.run_indicators(
        tickers, date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=3
    )
    assert rep.rows_flagged == 2
    assert len(flagged) == 1, "bar_rejects should be written once, not per chunk"
    assert {r["ticker"] for r in flagged[0]} == {"T000", "T005"}


def test_a_chunk_where_everything_skips_writes_no_rows(monkeypatch, no_run_job, fake_engine):
    """`if frames:` must still guard the write. An empty concat raises."""
    monkeypatch.setattr(compute, "_compute_one_ticker", lambda t, *a, **k: (t, None))
    monkeypatch.setattr(
        compute.db_io, "upsert", lambda *a, **k: pytest.fail("wrote an empty chunk")
    )
    monkeypatch.setattr(compute.db_io, "append", lambda *a, **k: None)
    rep = compute.run_indicators(
        ["A", "B"], date(2020, 1, 1), date(2020, 1, 3), engine=fake_engine, chunk_size=1
    )
    assert rep.rows_written == 0


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def test_chunk_size_has_a_default_so_callers_need_not_care():
    sig = inspect.signature(compute.run_indicators)
    assert sig.parameters["chunk_size"].default == compute.INDICATOR_CHUNK_SIZE


def test_the_default_is_bounded():
    """Large enough that pool overhead is amortised, small enough that one
    chunk's frames fit comfortably. 200 tickers of full history is roughly
    a million rows."""
    assert 50 <= compute.INDICATOR_CHUNK_SIZE <= 500
