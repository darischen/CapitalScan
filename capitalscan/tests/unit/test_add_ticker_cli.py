"""`cscan add-ticker` (2026-11-03): the VGT procedure as one command.

Called directly as a plain function with every parameter passed, the
convention `test_path_cli.py` uses, and with every database and network
boundary stubbed. What these pin: the order of the steps, that each runs
for the named tickers only, the fund-list guards of ADR 147 and ADR 154,
and the refusal while another job runs (ADR 060).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime

import pytest
import typer

from capitalscan.core import training
from capitalscan.core.config import Config
from capitalscan.jobs import cli, compute, db_io, ingest
from capitalscan.research import path_backfill as path_backfill_mod
from capitalscan.research.path_backfill import PathBackfillReport

QUARTERS = ["2010Q1", "2010Q2", "2026Q2"]


@pytest.fixture
def calls(monkeypatch) -> list:
    log: list = []

    def rec(name, result=None):
        def _fake(*args, **kwargs):
            log.append((name, args, kwargs))
            if result is not None:
                return result
            return ingest.IngestReport(job=name, run_id="add_ticker_test")

        return _fake

    @contextmanager
    def _fake_run_job(engine, job, params):
        log.append(("run_job", (job,), params))
        yield ingest.IngestReport(job=job, run_id="add_ticker_test")

    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda cli_overrides=None: Config())
    monkeypatch.setattr(cli, "_jobs_running", lambda engine: [])
    monkeypatch.setattr(cli, "_live_quarters", lambda engine, chash: list(QUARTERS))
    monkeypatch.setattr(cli, "_fill_missing_cik", rec("cik", 1))
    monkeypatch.setattr(cli, "_print_add_ticker_summary", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "run_job", _fake_run_job)
    for name in (
        "ensure_tickers",
        "run_bars_daily",
        "run_bars_hourly",
        "run_actions",
        "run_shares",
        "run_earnings",
    ):
        monkeypatch.setattr(ingest, name, rec(name))
    for name in ("run_indicators", "run_universe", "run_events"):
        monkeypatch.setattr(compute, name, rec(name))
    monkeypatch.setattr(cli, "backtest", rec("backtest"))
    monkeypatch.setattr(
        path_backfill_mod, "run_path_backfill", rec("run_path_backfill", PathBackfillReport())
    )
    return log


def _names(log: list) -> list[str]:
    return [name for name, _, _ in log if name != "run_job"]


def _run(symbols: str, etf: bool = False, force: bool = False) -> None:
    cli.add_ticker(symbols=symbols, etf=etf, lookback=8500, force=force)


def test_a_fund_runs_every_step_in_order(calls):
    _run("vgt", etf=True)

    assert _names(calls) == [
        "ensure_tickers",
        "run_bars_daily",
        "run_bars_hourly",
        "run_actions",
        "run_shares",
        "run_indicators",
        *["run_universe"] * len(QUARTERS),
        "run_events",
        "backtest",
        "run_path_backfill",
    ]


def test_every_step_is_scoped_to_the_named_tickers(calls):
    _run("VGT,^GSPC", etf=True)

    by_name = {}
    for name, args, kwargs in calls:
        by_name.setdefault(name, []).append((args, kwargs))
    assert by_name["ensure_tickers"][0][0][0] == ["VGT", "^GSPC"]
    for name in ("run_bars_daily", "run_bars_hourly", "run_actions", "run_shares"):
        assert by_name[name][0][0][0] == ["VGT", "^GSPC"], name
    assert by_name["run_indicators"][0][0][0] == ["VGT", "^GSPC"]
    assert [a[0] for a, _ in by_name["run_universe"]] == QUARTERS
    assert all(k["tickers"] == ["VGT", "^GSPC"] for _, k in by_name["run_universe"])
    assert by_name["run_events"][0][0][0] == ["VGT", "^GSPC"]
    assert by_name["backtest"][0][1]["tickers"] == "VGT,^GSPC"
    assert by_name["backtest"][0][1]["sweep"] is False
    assert by_name["run_path_backfill"][0][1]["tickers"] == ["VGT", "^GSPC"]


def test_a_company_gets_its_cik_and_earnings_and_a_fund_does_not(calls):
    _run("ZZZZ,VGT")

    names = _names(calls)
    cik = [a for n, a, _ in calls if n == "cik"]
    earnings = [(a, k) for n, a, k in calls if n == "run_earnings"]
    assert cik == [("fake-engine", ["ZZZZ"])]
    assert earnings == [((["ZZZZ"],), {"historical": True, "forward_days": 0})]
    # Earnings land before indicators, which read them for `days_to_earnings`.
    assert names.index("run_earnings") < names.index("run_indicators")


def test_events_start_no_earlier_than_the_split_boundary(calls):
    _run("VGT", etf=True)

    (args,) = [a for n, a, _ in calls if n == "run_events"]
    assert args[1] == date.fromisoformat(Config().splits.event_start)


def test_etf_flag_refuses_a_symbol_not_on_the_fund_lists(calls):
    with pytest.raises(typer.Exit) as exc:
        _run("ZZZZ", etf=True)
    assert exc.value.exit_code == 1
    assert _names(calls) == []


def test_a_symbol_on_one_fund_list_only_is_refused(calls, monkeypatch):
    monkeypatch.setattr(ingest, "SEC_NON_FILER_TICKERS", ingest.SEC_NON_FILER_TICKERS | {"ZZZZ"})
    assert "ZZZZ" not in training.ETF_TICKERS
    with pytest.raises(typer.Exit):
        _run("ZZZZ")
    assert _names(calls) == []


def test_refuses_while_another_job_runs_unless_forced(calls, monkeypatch):
    monkeypatch.setattr(
        cli,
        "_jobs_running",
        lambda engine: [("backtest_compute_x", "backtest_compute", datetime(2026, 11, 7, 1, 0))],
    )
    with pytest.raises(typer.Exit):
        _run("VGT", etf=True)
    assert _names(calls) == []

    _run("VGT", etf=True, force=True)
    assert "run_path_backfill" in _names(calls)


def test_no_symbols_is_an_error(calls):
    with pytest.raises(typer.Exit):
        _run(" , ")


@pytest.mark.parametrize(
    ("as_of", "label"),
    [
        (date(2010, 3, 31), "2010Q1"),
        (date(2010, 6, 30), "2010Q2"),
        (date(2025, 9, 30), "2025Q3"),
        (date(2026, 12, 31), "2026Q4"),
    ],
)
def test_quarter_label(as_of, label):
    assert cli._quarter_label(as_of) == label
