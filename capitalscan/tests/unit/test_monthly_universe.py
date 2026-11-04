"""`cscan monthly` runs the quarterly universe evaluation (owner, 2026-11-03).

ADR 014 makes trade membership quarterly and automatic, and until this
date no timer ran it: `monthly` was a no-op and 2026Q3 was evaluated by
hand 33 days late (OPERATIONS.md). Called as a plain function with every
database boundary stubbed, the convention of `test_add_ticker_cli.py`.

What these pin: the quarter it picks, that a missing quarter is evaluated
and its events re-stamped from the quarter's last day, that an evaluated
quarter is left alone, and the refusal while another job runs (ADR 060),
which leans on the unit's `Restart=on-failure` to retry.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
import typer

from capitalscan.core.config import Config
from capitalscan.jobs import cli, compute, db_io, ingest, scheduled_runs

TODAY = date(2026, 10, 1)
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def calls(monkeypatch) -> list:
    log: list = []

    def rec(name, result=None):
        def _fake(*args, **kwargs):
            log.append((name, args, kwargs))
            return result

        return _fake

    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda cli_overrides=None: Config())
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    monkeypatch.setattr(cli, "_live_quarters", lambda engine, chash: ["2026Q1", "2026Q2"])
    monkeypatch.setattr(cli, "_jobs_running", lambda engine: [])
    monkeypatch.setattr(cli, "_resolve_tickers", lambda tickers: ["AAPL", "VGT"])
    monkeypatch.setattr(scheduled_runs, "record", rec("record", 0))
    monkeypatch.setattr(scheduled_runs, "complete", rec("complete", 1))
    report = ingest.IngestReport(job="universe", run_id="monthly_test", tickers=["AAPL", "VGT"])
    monkeypatch.setattr(compute, "run_universe", rec("run_universe", report))
    monkeypatch.setattr(compute, "run_events", rec("run_events"))
    return log


def _names(log: list) -> list[str]:
    return [name for name, _, _ in log]


def _only(log: list[tuple[str, tuple, dict]], name: str) -> tuple[str, tuple, dict]:
    (call,) = [c for c in log if c[0] == name]
    return call


@pytest.mark.parametrize(
    ("today", "as_of"),
    [
        (date(2026, 10, 1), date(2026, 9, 30)),
        (date(2026, 11, 3), date(2026, 9, 30)),
        (date(2026, 12, 31), date(2026, 9, 30)),
        (date(2027, 1, 1), date(2026, 12, 31)),
        (date(2026, 4, 1), date(2026, 3, 31)),
        # A quarter's own last day still answers the quarter before: the
        # evaluation reads that day's close, which the session has not made.
        (date(2026, 9, 30), date(2026, 6, 30)),
        (date(2026, 3, 31), date(2025, 12, 31)),
    ],
)
def test_latest_completed_quarter(today, as_of):
    assert cli._latest_completed_quarter(today) == as_of


def test_a_missing_quarter_is_evaluated_then_its_events_restamped(calls):
    cli.monthly()

    assert _names(calls) == ["record", "run_universe", "run_events", "complete"]
    _, args, kwargs = _only(calls, "run_universe")
    assert args == ("2026Q3",)
    assert kwargs["tickers"] is None  # every active ticker, as the CLI does
    assert isinstance(kwargs["config"], Config)
    _, args, kwargs = _only(calls, "run_events")
    assert args == (["AAPL", "VGT"], date(2026, 9, 30), TODAY)
    assert isinstance(kwargs["config"], Config)
    _, args, _ = _only(calls, "complete")
    assert args[1:] == ("monthly", "ok")


def test_a_late_run_restamps_the_whole_window_since_the_quarter_ended(calls, monkeypatch):
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 11, 3))
    cli.monthly()

    _, args, _ = _only(calls, "run_universe")
    assert args == ("2026Q3",)
    _, args, _ = _only(calls, "run_events")
    assert args[1:] == (date(2026, 9, 30), date(2026, 11, 3))


def test_an_evaluated_quarter_is_left_alone(calls, monkeypatch):
    monkeypatch.setattr(cli, "_live_quarters", lambda engine, chash: ["2026Q2", "2026Q3"])
    cli.monthly()

    assert _names(calls) == ["record", "complete"]
    _, args, _ = _only(calls, "complete")
    assert args[1:] == ("monthly", "ok")


def test_refuses_while_another_job_runs(calls, monkeypatch):
    busy = [("backtest_compute_x", "backtest_compute", datetime(2026, 8, 1, 10, 0))]
    monkeypatch.setattr(cli, "_jobs_running", lambda engine: busy)
    with pytest.raises(typer.Exit) as exc:
        cli.monthly()

    assert exc.value.exit_code == 1
    assert "run_universe" not in _names(calls)
    assert "run_events" not in _names(calls)
    _, args, _ = _only(calls, "complete")
    assert args[1:] == ("monthly", "failed")


def test_the_unit_retries_a_refusal():
    """The refusal above exits 1 and relies on systemd to try again.

    A `oneshot` unit ignores `Restart=` (the nightly unit's comment), so the
    unit must stay `simple`, and the burst limit must outlast a weekly that
    can run up to its 54 h `RuntimeMaxSec`.
    """
    unit = (REPO / "scripts" / "systemd" / "capitalscan-monthly.service").read_text()
    assert "Type=simple" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=1h" in unit
    assert "StartLimitIntervalSec=3d" in unit
    assert "StartLimitBurst=60" in unit
