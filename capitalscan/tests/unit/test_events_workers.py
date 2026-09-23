"""`cscan events --workers`, and the two rules the parallel path must keep.

Measured 2026-09-22 on `capitalscan_hist`, 20 tickers over 2002-2026, the
same 52,683 rows and the same fingerprint every time:

    original                337.9 s
    serial, after this work  39.4 s   (8.6x)
    --workers 8              20.3 s   (16.6x)

Tickers are independent — the debounce key is `(ticker, signal_date, bound)`
— so parallelism is safe. What is NOT safe is letting the two paths drift or
letting completion order reach the write, and that is what this pins.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from capitalscan.jobs import cli, compute

SRC = Path(compute.__file__).read_text(encoding="utf-8")
SERIAL_BRANCH = chr(10) + "        else:"  # the parallel branch ends here


class TestOneLoopForBothPaths:
    def test_both_paths_call_the_same_per_ticker_function(self) -> None:
        """A second copy of the detection loop is how the serial and parallel
        answers would quietly diverge."""
        body = SRC[SRC.index("def run_events(") :]
        assert body.count("_events_for_ticker(") >= 1
        assert "_events_one_ticker" in body
        worker = SRC[SRC.index("def _events_one_ticker(") : SRC.index("def run_events(")]
        assert "_events_for_ticker(" in worker, "the worker must reuse the shared loop"

    def test_the_worker_opens_its_own_connection(self) -> None:
        """CLAUDE.md's platform rule: spawn, and connections are not
        picklable, so a worker cannot be handed the parent's engine."""
        worker = SRC[SRC.index("def _events_one_ticker(") : SRC.index("def run_events(")]
        assert "db_io.get_engine(database_url" in worker


class TestDeterminism:
    """ADR 060: identical config, identical output.

    `as_completed` yields in whatever order the workers finish, so without
    the sort the same run would send its rows in a different order each time.
    """

    def _pool_block(self) -> str:
        start = SRC.index("with ProcessPoolExecutor(max_workers=max_workers) as pool")
        return SRC[start : SRC.index(SERIAL_BRANCH, start)]

    def test_parallel_rows_are_sorted_before_the_write(self) -> None:
        assert "deduped.sort(" in self._pool_block()

    def test_the_sort_key_is_total_over_the_written_rows(self) -> None:
        """A partial key leaves ties in completion order, which is exactly
        the non-determinism this guards against."""
        block = self._pool_block()
        for column in ("ticker", "signal_date", "signal_type"):
            assert f'r["{column}"]' in block

    def test_the_serial_path_does_not_sort(self) -> None:
        """It walks `tickers` in order, so its order is the input's. One
        sort call in `run_events`, in the parallel branch only."""
        body = SRC[SRC.index("def run_events(") :]
        assert body.count("deduped.sort(") == 1


class TestTheParentDoesNotReadTwice:
    def test_the_parent_reads_only_on_the_serial_path(self) -> None:
        """Workers read their own slices. Reading the whole window in the
        parent as well is most of what held 8 workers to 1.3x before this,
        so the reads belong inside the serial branch, not above it."""
        body = SRC[SRC.index("        parallel = max_workers > 1") :]
        before_branch, _, after = body.partition("        if parallel:")
        assert "_read_bars_range(" not in before_branch
        pool, _, serial = after.partition(SERIAL_BRANCH)
        assert "_read_bars_range(" not in pool
        for read in ("_read_bars_range(", "_read_indicators_range(", "_read_universe_flags("):
            assert read in serial


class TestTheWriteSwitchesToCopyWhenBig:
    def test_the_threshold_is_a_named_constant(self) -> None:
        assert isinstance(compute._COPY_WRITE_THRESHOLD, int)
        assert compute._COPY_WRITE_THRESHOLD > 0

    def test_both_write_paths_use_the_same_conflict_key(self) -> None:
        """A staging write that keyed differently would insert duplicates
        instead of updating."""
        body = SRC[SRC.index("            key = [") : SRC.index("# See `db_io.fill_event_sector")]
        assert body.count("key,") == 2, "copy_upsert and upsert must share the key"
        assert body.count("_RUN_EVENTS_UPDATE_COLUMNS") == 2


class TestTheFlagIsWired:
    def test_run_events_takes_max_workers(self) -> None:
        assert "max_workers" in inspect.signature(compute.run_events).parameters

    def test_the_cli_passes_workers_through(self, monkeypatch) -> None:  # noqa: ANN001
        seen: dict = {}

        def _fake(tickers, start, end, config=None, max_workers=1):  # noqa: ANN001, ANN202
            seen["max_workers"] = max_workers

            class _R:
                rows_written = 0
                rows_flagged = 0

            return _R()

        monkeypatch.setattr(compute, "run_events", _fake)
        monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
        cli.events(lookback=5, tickers="AAPL", workers=8)
        assert seen["max_workers"] == 8

    def test_the_default_stays_serial(self, monkeypatch) -> None:  # noqa: ANN001
        """The nightly's five-day window is not worth process startup, and a
        surprise default would change the shape of every scheduled run."""
        assert inspect.signature(cli.events).parameters["workers"].default.default == 1
