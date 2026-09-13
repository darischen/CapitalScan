"""`cscan weekly` — CLI wiring only, no execution.

Built 2026-09-13 after the monolithic single-`run_backtest`-call version of
`weekly` OOM-killed on `wivie`: 7.47GB RSS on a 7.6GB box, 9h11m in, because
`run_backtest` holds every ticker's frame in memory until one final write.
`weekly` now dispatches through `_run_backtest_compute_chunked` — the same
checkpointed path `cscan backtest --phase compute` already used — followed
by `finalize_cofire`, instead of one whole-universe call. These tests pin
that wiring so it cannot regress back to the monolithic call by accident.

CONSTRAINTS.md: never touch a real database. `cli.weekly`'s IO all comes
through `capitalscan.jobs.db_io`, `capitalscan.jobs.ingest.run_job`,
`capitalscan.jobs.scheduled_runs`, `cli._run_backtest_compute_chunked`, and
`capitalscan.research.backtest.finalize_cofire` — each patched here.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
import typer

from capitalscan.jobs import cli, db_io, ingest, scheduled_runs
from capitalscan.research import backtest as backtest_mod


@contextmanager
def _fake_run_job(engine, job, params):
    report = ingest.IngestReport(job=job, run_id=f"{job}_fake_run_id")
    yield report


@pytest.fixture(autouse=True)
def _no_real_io(monkeypatch):
    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(cli, "_resolve_tickers", lambda tickers: ["AAPL", "MSFT", "TSM"])
    monkeypatch.setattr(scheduled_runs, "record", lambda engine, job: None)
    monkeypatch.setattr(scheduled_runs, "complete", lambda *args, **kwargs: 1)
    monkeypatch.setattr(ingest, "run_job", _fake_run_job)
    monkeypatch.setattr(backtest_mod, "finalize_cofire", lambda engine, chash: 0)

    # The refit (ADR 184) and artifact publish are exercised elsewhere
    # (test_nightly_chain.py's `TestTheRefitLivesInWeeklyNotNightly`); here
    # they only need to not touch a real database.
    from capitalscan.jobs import predict as predict_mod

    monkeypatch.setattr(
        predict_mod, "run_predict", lambda *args, **kwargs: predict_mod.PredictReport()
    )

    def _fake_serving_engine():
        raise RuntimeError("serving not configured (test)")

    from capitalscan.jobs import sync as sync_mod

    monkeypatch.setattr(sync_mod, "serving_engine", _fake_serving_engine)


def _call(workers=8, chunk_size=25, cosmetic=True):
    return cli.weekly(workers=workers, chunk_size=chunk_size, cosmetic=cosmetic)


class TestWeeklyUsesTheChunkedPath:
    def test_calls_the_chunked_helper_not_run_backtest_directly(self, monkeypatch):
        """The regression this whole file exists to catch: `weekly` must not
        go back to a single `run_backtest(resolved, ...)` call over the
        whole universe — that is exactly what OOM-killed on `wivie`.
        """
        calls = []

        def _fake_chunked(engine, resolved, config, chash, **kwargs):
            calls.append({"resolved": resolved, "kwargs": kwargs})
            return (1, 0, 0, {})

        monkeypatch.setattr(cli, "_run_backtest_compute_chunked", _fake_chunked)

        _call(workers=4, chunk_size=10, cosmetic=False)

        assert len(calls) == 1
        assert calls[0]["resolved"] == ["AAPL", "MSFT", "TSM"]
        assert calls[0]["kwargs"]["workers"] == 4
        assert calls[0]["kwargs"]["chunk_size"] == 10
        assert calls[0]["kwargs"]["cosmetic"] is False

    def test_scheduled_no_tty_means_quiet_true(self, monkeypatch):
        """Matches nightly's hardcoded `quiet=True` on `run_path_capture` —
        weekly runs under systemd/Task Scheduler, never a TTY.
        """
        captured = {}

        def _fake_chunked(engine, resolved, config, chash, **kwargs):
            captured.update(kwargs)
            return (1, 0, 0, {})

        monkeypatch.setattr(cli, "_run_backtest_compute_chunked", _fake_chunked)
        _call()
        assert captured["quiet"] is True

    def test_finalize_runs_after_compute(self, monkeypatch):
        order = []

        def _fake_chunked(engine, resolved, config, chash, **kwargs):
            order.append("compute")
            return (1, 0, 5, {})

        def _fake_finalize(engine, chash):
            order.append("finalize")
            return 5

        monkeypatch.setattr(cli, "_run_backtest_compute_chunked", _fake_chunked)
        monkeypatch.setattr(backtest_mod, "finalize_cofire", _fake_finalize)
        _call()
        assert order == ["compute", "finalize"]

    def test_default_chunk_size_is_25(self, monkeypatch):
        """The same default as `cscan backtest --phase compute`, so the two
        checkpoint shapes stay interchangeable for `_chunk_already_done`.
        """
        captured = {}

        def _fake_chunked(engine, resolved, config, chash, **kwargs):
            captured.update(kwargs)
            return (1, 0, 0, {})

        monkeypatch.setattr(cli, "_run_backtest_compute_chunked", _fake_chunked)
        cli.weekly(workers=8, chunk_size=25, cosmetic=True)
        assert captured["chunk_size"] == 25


class TestWeeklyPartialFailure:
    def test_failed_tickers_exits_nonzero_and_marks_scheduled_runs_failed(self, monkeypatch):
        monkeypatch.setattr(
            cli,
            "_run_backtest_compute_chunked",
            lambda *a, **kw: (2, 0, 3, {"BADCO": "ValueError: boom"}),
        )
        calls = []
        monkeypatch.setattr(
            scheduled_runs, "complete", lambda engine, job, status, **kw: calls.append(status)
        )

        with pytest.raises(typer.Exit) as exc_info:
            _call()

        assert exc_info.value.exit_code == 1
        assert calls == ["failed"]

    def test_failed_tickers_skips_the_refit(self, monkeypatch):
        """A partial backtest failure must not train a model on incomplete
        labels — the refit is only reached on a clean compute+finalize.
        """
        monkeypatch.setattr(
            cli,
            "_run_backtest_compute_chunked",
            lambda *a, **kw: (2, 0, 3, {"BADCO": "ValueError: boom"}),
        )
        refit_calls = []
        from capitalscan.jobs import predict as predict_mod

        monkeypatch.setattr(
            predict_mod,
            "run_predict",
            lambda *a, **kw: refit_calls.append(1) or predict_mod.PredictReport(),
        )

        with pytest.raises(typer.Exit):
            _call()

        assert refit_calls == []


class TestWeeklySuccessMarksScheduledRunsOk:
    def test_clean_run_marks_ok(self, monkeypatch):
        monkeypatch.setattr(cli, "_run_backtest_compute_chunked", lambda *a, **kw: (3, 0, 42, {}))
        calls = []
        monkeypatch.setattr(
            scheduled_runs, "complete", lambda engine, job, status, **kw: calls.append(status)
        )
        _call()
        assert calls == ["ok"]
