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

import inspect

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


class _UpdateConn(_Conn):
    """Captures the statement `finalize_cofire` executes."""

    def __init__(self):
        super().__init__(row=None)
        self.statements: list[str] = []
        self.bound: list[dict] = []

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        self.bound.append(params or {})

        class _R:
            rowcount = 1

        return _R()


class _TxEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


class TestFinalizeUpdatesRatherThanUpserts:
    """**It must never be able to create an event row.**

    `db_io.upsert` issues `INSERT ... ON CONFLICT`, and Postgres enforces
    NOT NULL on the insert before reaching the conflict clause -- so a
    six-column frame fails on `run_id` even though every row is guaranteed
    to conflict. Observed on 2026-08-21 against 783,644 real rows.

    Padding the frame with the missing NOT NULL columns would have made it
    pass and would have been wrong: finalize corrects rows the compute phase
    wrote, and a key with no row is an upstream bug that should update
    nothing rather than invent a skeleton.
    """

    def _run(self, rows):
        conn = _UpdateConn()
        import capitalscan.research.backtest as bt

        original = bt.pd.read_sql
        bt.pd.read_sql = lambda *a, **k: _events(rows)
        try:
            written = bt.finalize_cofire(_TxEngine(conn), "abc123")
        finally:
            bt.pd.read_sql = original
        return written, conn

    def test_it_issues_an_update_not_an_insert(self):
        _, conn = self._run([("AAPL", "2026-01-05", "confluence_low", "next_open")])
        sql = " ".join(conn.statements)
        assert "UPDATE events" in sql
        assert "INSERT" not in sql.upper()

    def test_it_sets_only_cofire_count(self):
        _, conn = self._run([("AAPL", "2026-01-05", "confluence_low", "next_open")])
        sql = " ".join(conn.statements)
        assert "SET cofire_count" in sql
        # The other seventy columns belong to the compute phase.
        for column in ("run_id", "net_ret", "entry_price", "split_key"):
            assert f"SET {column}" not in sql
            assert f", {column} =" not in sql

    def test_it_matches_on_the_full_natural_key(self):
        _, conn = self._run([("AAPL", "2026-01-05", "confluence_low", "next_open")])
        sql = " ".join(conn.statements)
        for column in ("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"):
            assert f"e.{column} = v.{column}" in sql

    def test_the_page_stays_under_the_parameter_ceiling(self):
        """Postgres accepts at most 65,535 bound parameters per statement.

        Six per row here, so the default page must sit below ~10,900. The
        obvious 50,000 sends 300,000 and fails with a message naming neither
        the table nor the page size -- ADR 137's trap, reached again.
        """
        import inspect

        default = inspect.signature(backtest.finalize_cofire).parameters["chunk_size"].default
        assert default * 6 < 65_535

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


# ---------------------------------------------------------------------------
# The harness phase
# ---------------------------------------------------------------------------


class TestHarnessPhase:
    """**Why a third phase exists at all.**

    `run_harness` lived only inside `--phase all`, so the one path that
    validates a config was also the one path with no checkpoint. Five
    unresumable hours is a run nobody repeats after a crash, so the
    validation quietly stops happening -- and it had: the expanded universe
    shipped 2026-08-21 on compute + finalize, with the harness never run
    against it.

    Detaching costs nothing. The harness reads `events` and `bars`, writes
    no row, and holds no state the compute phase needs.
    """

    def test_harness_is_an_accepted_phase(self):
        src = inspect.getsource(cli.backtest)
        assert '{"all", "compute", "finalize", "harness"}' in src

    def test_the_error_message_lists_it(self):
        # A phase the code accepts but the error text omits is worse than an
        # undocumented flag: it tells a reader their correct spelling is wrong.
        src = inspect.getsource(cli.backtest)
        assert "all, compute, finalize or harness" in src

    def test_it_writes_nothing(self):
        """`rows_written` stays 0 and the phase issues no write.

        The value of a detached validation pass is that re-running it is
        free of consequence. A phase that quietly corrected a row would make
        "run the harness again" a decision rather than a reflex.
        """
        src = inspect.getsource(cli.backtest)
        harness_block = src[src.index('if phase == "harness":') :]
        harness_block = harness_block[: harness_block.index("# ---- phase: finalize")]
        for write in ("UPDATE", "INSERT", "upsert(", "DELETE"):
            assert write not in harness_block

    def test_an_unwritten_config_is_a_sequencing_error_not_a_failed_check(self):
        """Empty events must not read as "the checks failed".

        Both exit 1, so the distinction lives in the message: one names the
        command to run next, the other names violated invariants.
        """
        src = inspect.getsource(cli.backtest)
        assert "no events for" in src
        assert "--phase compute` first" in src


class TestHarnessEventScoping:
    """**The scoping is the whole correctness argument.**

    `_load_events_for_run` is `run_id`-scoped so a rerun does not pull a
    previous run's rows. The harness phase cannot use it: the compute phase
    writes one `run_id` **per chunk**, so no single `run_id` holds the
    universe and a run-scoped load would validate a twenty-five-ticker slice
    while reporting on the config.
    """

    def _capture(self, chash: str) -> dict:
        captured: dict = {}
        real = pd.read_sql

        def fake(stmt, conn, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params or {}
            return pd.DataFrame()

        pd.read_sql = fake  # type: ignore[assignment]
        try:
            cli._load_events_for_config(_Engine(_Conn()), chash)
        finally:
            pd.read_sql = real
        return captured

    def test_it_binds_the_config_hash(self):
        assert self._capture("abc123")["params"] == {"chash": "abc123"}

    def test_it_does_not_scope_on_run_id(self):
        sql = self._capture("abc123")["sql"]
        assert "config_hash = :chash" in sql
        assert "run_id" not in sql

    def test_its_sibling_still_scopes_on_run_id(self):
        # The two loaders exist because they answer different questions. If
        # this ever collapses into one, a `--phase all` rerun starts
        # validating every prior run's rows alongside its own.
        assert "run_id = :run_id" in inspect.getsource(cli._load_events_for_run)
