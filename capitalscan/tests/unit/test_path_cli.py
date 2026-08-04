"""Tests for `cscan path backfill` / `cscan path reconcile` CLI wiring
(findings #1, #6, #7 of the Session 10 final review) — no execution
against a real database.

`cli.path_backfill_cmd` / `cli.path_reconcile_cmd` are called directly as
plain functions, not through Typer's CLI runner (same convention
`test_backtest_cli.py` uses), so `--quiet`/`--run-id` are passed
explicitly rather than relying on Typer's own default-binding.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import pytest
import typer

from capitalscan.core.config import Config
from capitalscan.jobs import cli, db_io, ingest
from capitalscan.research import path_backfill as path_backfill_mod
from capitalscan.research import path_reconcile as path_reconcile_mod
from capitalscan.research.path_backfill import PathBackfillReport
from capitalscan.research.path_reconcile import ReconciliationReport


@contextmanager
def _fake_run_job(engine, job, params):
    report = ingest.IngestReport(job=job, run_id="path_fake_run_id")
    yield report


@pytest.fixture(autouse=True)
def _no_real_io(monkeypatch):
    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(ingest, "run_job", _fake_run_job)


def test_path_backfill_cmd_routes_through_resolve_config_or_exit(monkeypatch):
    # Finding #1: must call `_resolve_config_or_exit`, not import
    # `DEFAULT_CONFIG` directly — a `config.toml`/`CAPSCAN_*` override the
    # user set must actually reach `run_path_backfill`.
    sentinel_config = Config()
    seen: dict = {}

    def fake_resolve(cli_overrides=None):
        seen["called"] = True
        return sentinel_config

    def fake_run_path_backfill(engine, config, quiet=False, max_workers=1):
        seen["config"] = config
        seen["max_workers"] = max_workers
        return PathBackfillReport(events_processed=1, events_skipped_unfilled=0, rows_written=1)

    monkeypatch.setattr(cli, "_resolve_config_or_exit", fake_resolve)
    monkeypatch.setattr(path_backfill_mod, "run_path_backfill", fake_run_path_backfill)

    cli.path_backfill_cmd(quiet=False, workers=1)

    assert seen["called"] is True
    assert seen["config"] is sentinel_config
    assert seen["max_workers"] == 1


def test_path_backfill_cmd_passes_workers_through(monkeypatch):
    # `--workers` must reach `run_path_backfill`'s `max_workers`, matching
    # the same `ProcessPoolExecutor workers; 1 runs serially` convention
    # `cscan backtest --workers` and `cscan indicators --workers` use.
    seen: dict = {}

    def fake_run_path_backfill(engine, config, quiet=False, max_workers=1):
        seen["max_workers"] = max_workers
        return PathBackfillReport()

    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda cli_overrides=None: Config())
    monkeypatch.setattr(path_backfill_mod, "run_path_backfill", fake_run_path_backfill)

    cli.path_backfill_cmd(quiet=False, workers=8)

    assert seen["max_workers"] == 8


def test_path_reconcile_cmd_routes_through_resolve_config_or_exit(monkeypatch):
    sentinel_config = Config()
    seen: dict = {}

    def fake_resolve(cli_overrides=None):
        seen["called"] = True
        return sentinel_config

    def fake_reconcile(engine, config, config_hash):
        seen["config"] = config
        return ReconciliationReport(config_hash=config_hash, total_events=5, mismatches={}, explained={})

    monkeypatch.setattr(cli, "_resolve_config_or_exit", fake_resolve)
    monkeypatch.setattr(path_reconcile_mod, "reconcile", fake_reconcile)

    cli.path_reconcile_cmd(config_hash="c1")

    assert seen["called"] is True
    assert seen["config"] is sentinel_config


def test_path_reconcile_cmd_exits_zero_on_pass(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda cli_overrides=None: Config())
    monkeypatch.setattr(
        path_reconcile_mod,
        "reconcile",
        lambda engine, config, config_hash: ReconciliationReport(
            config_hash=config_hash, total_events=3, mismatches={}, explained={}
        ),
    )
    cli.path_reconcile_cmd(config_hash="c1")  # must not raise


def test_path_reconcile_cmd_raises_typer_exit_1_on_fail(monkeypatch):
    # Finding #6: every other failure path in cli.py raises
    # `typer.Exit(code=1)` — a FAIL print with exit code 0 would look
    # clean to any script/CI that only checks the return code.
    mismatch_frame = pd.DataFrame({"event_id": [1, 2], "mfe_derived": [0.1, 0.2], "mfe_actual": [0.9, 0.9]})
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda cli_overrides=None: Config())
    monkeypatch.setattr(
        path_reconcile_mod,
        "reconcile",
        lambda engine, config, config_hash: ReconciliationReport(
            config_hash=config_hash, total_events=3, mismatches={"mfe": mismatch_frame}, explained={}
        ),
    )
    with pytest.raises(typer.Exit) as exc_info:
        cli.path_reconcile_cmd(config_hash="c1")
    assert exc_info.value.exit_code == 1


def test_path_reconcile_cmd_prints_sample_event_ids(monkeypatch, capsys):
    # Finding #7: the printed line must carry sample event_ids to
    # investigate, not just a bare count.
    mismatch_frame = pd.DataFrame(
        {"event_id": [101, 102, 103], "mfe_derived": [0.1, 0.2, 0.3], "mfe_actual": [0.9, 0.9, 0.9]}
    )
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda cli_overrides=None: Config())
    monkeypatch.setattr(
        path_reconcile_mod,
        "reconcile",
        lambda engine, config, config_hash: ReconciliationReport(
            config_hash=config_hash, total_events=3, mismatches={"mfe": mismatch_frame}, explained={}
        ),
    )
    with pytest.raises(typer.Exit):
        cli.path_reconcile_cmd(config_hash="c1")
    out = capsys.readouterr().out
    assert "101" in out and "102" in out and "103" in out
