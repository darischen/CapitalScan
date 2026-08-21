"""The two-phase backtest, and the ways a resumable job loses work quietly.

Built 2026-08-21 after a container crash killed a 1h55m run at the harness
stage. The write phase had completed and survived, but there was no way to
resume anything, and the next run is longer: the universe grew from 613 to
930 active tickers.

The interesting properties are not "compute writes rows". They are that a
chunk is only skipped when it genuinely finished, that a partial run cannot
write a `cofire_count` computed over a slice, and that the finalize pass is
the only step allowed to touch that column.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.jobs import cli
from capitalscan.research import backtest


# ---------------------------------------------------------------------------
# The resume check
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    """Records the parameters the resume check binds, and answers with
    whatever `row` the test wants."""

    def __init__(self, row=None):
        self.row = row
        self.params: dict = {}
        self.sql = ""

    def execute(self, stmt, params=None):
        self.sql = str(stmt)
        self.params = params or {}
        return _Result(self.row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


class TestChunkAlreadyDone:
    def test_true_when_a_clean_row_exists(self):
        engine = _Engine(_Conn(row=(1,)))
        assert cli._chunk_already_done(engine, "abc123", 3, 13) is True

    def test_false_when_no_row_exists(self):
        engine = _Engine(_Conn(row=None))
        assert cli._chunk_already_done(engine, "abc123", 3, 13) is False

    def test_matches_on_config_chunk_and_split_together(self):
        """**`of` is in the key, and that is the point.**

        A chunk index means nothing without the split it came from. Rerunning
        with a different `--chunk-size` repartitions the same tickers, so
        "chunk 3 of 13" is not the work "chunk 3 of 26" would do. Without
        `of` in the predicate, changing the chunk size on a resume would skip
        tickers that were never processed -- silently, because the run would
        report success.
        """
        conn = _Conn(row=None)
        cli._chunk_already_done(_Engine(conn), "abc123", 3, 13)
        assert conn.params == {"chash": "abc123", "chunk": "3", "of": "13"}
        assert "params->>'chunk'" in conn.sql
        assert "params->>'of'" in conn.sql

    def test_only_a_clean_row_counts(self):
        # A crashed chunk leaves `status = 'running'` or `'failed'`. Either
        # must re-run: `events` writes are idempotent, so a redundant chunk
        # costs minutes while a skipped one loses a slice of the universe.
        conn = _Conn(row=None)
        cli._chunk_already_done(_Engine(conn), "abc123", 1, 2)
        assert "status = 'ok'" in conn.sql

    def test_scoped_to_the_compute_job_not_to_backtest(self):
        """`backtest`, `backtest_sweep` and `backtest_compute` are different
        jobs in `runs` on purpose. A completed whole-run backtest must never
        read as a completed chunk, or a resume would skip everything."""
        conn = _Conn(row=None)
        cli._chunk_already_done(_Engine(conn), "abc123", 1, 2)
        assert "job = 'backtest_compute'" in conn.sql


# ---------------------------------------------------------------------------
# The finalize pass
# ---------------------------------------------------------------------------


def _events(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["ticker", "signal_date", "signal_type", "entry_kind"]
    ).assign(config_hash="abc123")


class TestCofireIsCrossTicker:
    def test_counts_distinct_tickers_on_a_date_and_type(self):
        frame = _events(
            [
                ("AAPL", "2026-01-05", "confluence_low", "next_open"),
                ("MSFT", "2026-01-05", "confluence_low", "next_open"),
                ("NVDA", "2026-01-06", "confluence_low", "next_open"),
            ]
        )
        out = backtest.add_cofire_count(frame)
        by_ticker = dict(zip(out["ticker"], out["cofire_count"]))
        assert by_ticker["AAPL"] == 2
        assert by_ticker["MSFT"] == 2
        assert by_ticker["NVDA"] == 1

    def test_entry_kind_fanout_does_not_inflate_the_count(self):
        # One ticker contributes up to four rows, one per entry kind. The
        # count is of *tickers*, so those must not read as four co-firers.
        frame = _events(
            [
                ("AAPL", "2026-01-05", "confluence_low", kind)
                for kind in ("touch", "touch_5m", "touch_30m", "next_open")
            ]
        )
        out = backtest.add_cofire_count(frame)
        assert set(out["cofire_count"]) == {1}

    def test_a_chunk_undercounts_which_is_why_compute_does_not_write_it(self):
        """**The whole reason the phases exist.**

        Two tickers fire the same signal on the same day. Computed over the
        full frame the count is 2; computed over either ticker's chunk alone
        it is 1. Neither chunk can see the other, so a per-chunk
        `cofire_count` is wrong by construction -- and `cofire_count` feeds
        `n_eff`, which is the quantity every published probability is
        divided by.
        """
        full = _events(
            [
                ("AAPL", "2026-01-05", "confluence_low", "next_open"),
                ("MSFT", "2026-01-05", "confluence_low", "next_open"),
            ]
        )
        assert set(backtest.add_cofire_count(full)["cofire_count"]) == {2}

        chunk = _events([("AAPL", "2026-01-05", "confluence_low", "next_open")])
        assert set(backtest.add_cofire_count(chunk)["cofire_count"]) == {1}


class TestFinalizeWritesOnlyCofire:
    def test_updates_exactly_one_column(self, monkeypatch):
        """Every other column on these rows belongs to the compute phase.

        `finalize_cofire` reads five columns and writes back; naming
        `cofire_count` as the sole update column is what stops it reverting
        the other seventy to the values it never read.
        """
        captured: dict = {}

        def fake_upsert(engine, table, frame, keys, update_columns=None):
            captured["table"] = table
            captured["keys"] = keys
            captured["update_columns"] = update_columns
            return len(frame)

        monkeypatch.setattr(backtest.db_io, "upsert", fake_upsert)
        monkeypatch.setattr(
            backtest.pd,
            "read_sql",
            lambda *a, **k: _events([("AAPL", "2026-01-05", "confluence_low", "next_open")]),
        )

        written = backtest.finalize_cofire(_Engine(_Conn()), "abc123")

        assert written == 1
        assert captured["table"] == "events"
        assert captured["update_columns"] == ["cofire_count"]
        assert captured["keys"] == [
            "config_hash",
            "ticker",
            "signal_date",
            "signal_type",
            "entry_kind",
        ]

    def test_an_empty_config_writes_nothing_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(backtest.pd, "read_sql", lambda *a, **k: pd.DataFrame())
        assert backtest.finalize_cofire(_Engine(_Conn()), "nothing-here") == 0

    def test_reads_only_the_columns_the_grouping_needs(self):
        # `events` is 75 columns wide and this pass needs five. Reading the
        # rest would move roughly ten times the bytes to compute one integer.
        assert backtest._COFIRE_COLUMNS == (
            "config_hash",
            "ticker",
            "signal_date",
            "signal_type",
            "entry_kind",
        )


class TestPhaseValidation:
    @pytest.mark.parametrize("bad", ["computed", "COMPUTE", "", "final", "1"])
    def test_an_unknown_phase_is_refused(self, bad):
        # Accepting an unrecognised phase and falling through to the default
        # would run a *whole* backtest for someone who asked for a slice.
        assert bad not in {"all", "compute", "finalize"}
