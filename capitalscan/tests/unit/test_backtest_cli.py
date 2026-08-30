"""Tests for `cscan backtest` (Task 11a) — CLI wiring only, no execution.

CONSTRAINTS.md: never touch a real database. `cli.backtest`'s IO all comes
through `capitalscan.jobs.db_io`, `capitalscan.jobs.ingest.run_job`,
`capitalscan.research.backtest.run_backtest`, and `capitalscan.research.
harness.run_harness` — each patched here to a fake, so this stays a unit
test.

`cli.backtest` is called directly as a plain function, not through Typer's
CLI runner, so every parameter must be passed explicitly: a bare
`cli.backtest()` would bind `tickers`, `workers`, etc. to their raw
`typer.Option(...)` objects (`OptionInfo`), not the resolved defaults —
that only happens inside Typer's own dispatch machinery.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import pytest
import typer

from capitalscan.core.config import Config, ExitParams
from capitalscan.jobs import cli, db_io, ingest
from capitalscan.jobs.config import config_hash
from capitalscan.research import backtest as backtest_mod
from capitalscan.research import harness as harness_mod
from capitalscan.research.backtest import BacktestReport, BacktestRunFailed
from capitalscan.research.harness import CheckResult, HarnessReport

DEFAULT_CONFIG = Config()
DEFAULT_HASH = config_hash(DEFAULT_CONFIG)

# Captured before the autouse fixture below patches these names on `cli` for
# every other test — the two tests that exercise these helpers' *real*
# bodies need the unpatched function object, not the fixture's stand-in.
_REAL_PRIOR_CLEAN_CHECK = cli._prior_clean_default_run_exists
_REAL_LOAD_BARS_BY_TICKER = cli._load_bars_by_ticker


def _passing_harness_report() -> HarnessReport:
    ok = CheckResult("ok", True, [], {})
    return HarnessReport(
        no_lookahead=ok, entry_sanity=ok, exit_sanity=ok, return_identity=ok, non_overlap=ok
    )


def _failing_harness_report() -> HarnessReport:
    bad = CheckResult("bad", False, [{"reason": "boom"}], {})
    ok = CheckResult("ok", True, [], {})
    return HarnessReport(
        no_lookahead=bad, entry_sanity=ok, exit_sanity=ok, return_identity=ok, non_overlap=ok
    )


@contextmanager
def _fake_run_job(engine, job, params):
    """Mirrors `ingest.run_job`'s shape (yield a report, re-raise on
    exception) without touching the database `_start_run`/`_finish_run`
    would otherwise hit.
    """
    report = ingest.IngestReport(job=job, run_id="backtest_fake_run_id")
    yield report


@pytest.fixture(autouse=True)
def _no_real_io(monkeypatch):
    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(ingest, "run_job", _fake_run_job)
    # `_prior_clean_default_run_exists` / `_load_bars_by_ticker` /
    # `_load_hourly_by_ticker` / `_load_events_for_run` are cli.py's own
    # DB-touching helpers; default to inert, DB-free behavior and let
    # individual tests override.
    monkeypatch.setattr(cli, "_prior_clean_default_run_exists", lambda engine, chash: False)
    monkeypatch.setattr(cli, "_load_bars_by_ticker", lambda engine, tickers, config: {})
    monkeypatch.setattr(cli, "_load_hourly_by_ticker", lambda engine, tickers, config: {})
    monkeypatch.setattr(
        cli, "_load_events_for_run", lambda engine, run_id: pd.DataFrame(columns=["ticker"])
    )


def _call(tickers=None, workers=1, sweep=False, config_name=None, phase="all", chunk_size=25):
    """Every option passed explicitly, including the ones a test does not care
    about.

    Calling the command as a plain function binds any omitted parameter to
    its `typer.Option(...)` object rather than to that option's default, so
    `phase` arrives as an `OptionInfo` and fails validation. Adding an option
    to the command means adding it here -- which is the trade for testing the
    real function instead of going through `CliRunner`.
    """
    return cli.backtest(
        tickers=tickers,
        workers=workers,
        sweep=sweep,
        config_name=config_name,
        phase=phase,
        chunk_size=chunk_size,
    )


# ---------------------------------------------------------------------------
# ExitParams defaults actually match ADR 059 (invariant 9: read from the
# dataclass, never restate as a literal)
# ---------------------------------------------------------------------------


def test_exit_params_defaults_are_the_swept_values():
    """The defaults ADR 059 required be validated *before* sweeping, then
    updated *by* the sweep.

    ADR 059 is a sequencing decision -- validate one config, then sweep -- and
    both halves are now done: the harness passed on ATR k=1.5 / target 4%, the
    fourteen-arm sweep ran on 2026-08-28/29, and the config moved on its
    evidence. This test froze the starting values as if they were permanent,
    which made a deliberate change look like a regression.

    It still guards the thing worth guarding: that the defaults are the values
    the sweep selected, not something drifted into by accident."""
    ep = ExitParams()
    assert ep.stop_mode == "atr"
    assert ep.stop_atr_k == 2.0
    assert ep.target_pct == 0.05
    assert ep.max_hold_days == 5


# ---------------------------------------------------------------------------
# --workers defaults to 1
# ---------------------------------------------------------------------------


def test_workers_option_default_is_one():
    import inspect

    sig = inspect.signature(cli.backtest)
    assert sig.parameters["workers"].default.default == 1


def test_workers_is_passed_through_to_run_backtest(monkeypatch):
    captured = {}

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        captured["max_workers"] = max_workers
        return BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])

    _call(tickers="AAPL", workers=7)

    assert captured["max_workers"] == 7


# ---------------------------------------------------------------------------
# Ticker resolution reuses `_resolve_tickers`
# ---------------------------------------------------------------------------


def test_tickers_flag_reuses_resolve_tickers(monkeypatch):
    calls = []

    def _fake_resolve(t):
        calls.append(t)
        return ["TSM", "NVDA"]

    monkeypatch.setattr(cli, "_resolve_tickers", _fake_resolve)
    monkeypatch.setattr(
        backtest_mod,
        "run_backtest",
        lambda tickers, config, run_id, engine=None, max_workers=1, full_universe=True: (
            BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})
        ),
    )

    _call(tickers="TSM,NVDA")

    assert calls == ["TSM,NVDA"]


# ---------------------------------------------------------------------------
# `--tickers` is a partial run -> full_universe=False reaches run_backtest
# ---------------------------------------------------------------------------


def test_explicit_tickers_passes_full_universe_false(monkeypatch):
    captured = {}

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        captured["full_universe"] = full_universe
        return BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])

    _call(tickers="AAPL")

    assert captured["full_universe"] is False


def test_no_tickers_flag_passes_full_universe_true(monkeypatch):
    captured = {}

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        captured["full_universe"] = full_universe
        return BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL", "MSFT"])

    _call(tickers=None)

    assert captured["full_universe"] is True


# ---------------------------------------------------------------------------
# Default config passed to run_backtest is the real dataclass default
# ---------------------------------------------------------------------------


def test_default_config_passed_to_run_backtest(monkeypatch):
    captured = {}

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        captured["config"] = config
        return BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])

    _call(tickers="AAPL")

    assert captured["config"] == DEFAULT_CONFIG
    # The swept values (2026-08-29), not the pre-sweep ones. The point of the
    # assertion is that the CLI hands `run_backtest` the *default* config
    # rather than constructing its own, so it must track the defaults.
    assert captured["config"].exits.stop_atr_k == 2.0
    assert captured["config"].exits.target_pct == 0.05
    assert captured["config"].exits.stop_mode == "atr"


# ---------------------------------------------------------------------------
# --sweep guard (ADR 059)
# ---------------------------------------------------------------------------


def test_sweep_without_prior_clean_run_refuses(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prior_clean_default_run_exists", lambda engine, chash: False)
    run_backtest_called = []
    monkeypatch.setattr(
        backtest_mod,
        "run_backtest",
        lambda *a, **k: run_backtest_called.append(1) or BacktestReport(run_id="x"),
    )

    with pytest.raises(typer.Exit) as exc_info:
        _call(sweep=True)

    assert exc_info.value.exit_code == 1
    assert not run_backtest_called
    out = capsys.readouterr().out
    assert "ADR 059" in out


def test_sweep_with_prior_clean_run_does_not_run_a_single_default_backtest(monkeypatch, capsys):
    """Passing the ADR 059 gate must actually run the 18-config sweep (Task
    12), not silently fall back to a single default-config backtest and
    call it a sweep. `run_backtest` must be called 18 times, not once.
    """
    monkeypatch.setattr(cli, "_prior_clean_default_run_exists", lambda engine, chash: True)
    monkeypatch.setattr(cli, "_sweep_config_already_done", lambda engine, chash: False)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(cli, "_load_bars_by_ticker", lambda engine, tickers, config: {})

    run_backtest_calls = []

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        run_backtest_calls.append(config)
        return BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(ingest, "run_job", _counting_run_job())

    _call(sweep=True)

    assert len(run_backtest_calls) == 18


# ---------------------------------------------------------------------------
# The 18-config sweep itself (Task 12)
# ---------------------------------------------------------------------------


def _counting_run_job():
    """A fake `ingest.run_job` that hands out a distinct `run_id` per call —
    unlike `_fake_run_job` above (fixed `"backtest_fake_run_id"` for every
    call, fine for the single-run tests), the sweep tests need to tell each
    of the 18 executions' `run_id`s apart to assert they are distinct.
    """
    counter = {"n": 0}

    @contextmanager
    def _run_job(engine, job, params):
        counter["n"] += 1
        report = ingest.IngestReport(job=job, run_id=f"sweep_run_{counter['n']}")
        yield report

    return _run_job


def test_sweep_dispatches_all_18_configs_in_deterministic_order(monkeypatch):
    monkeypatch.setattr(cli, "_prior_clean_default_run_exists", lambda engine, chash: True)
    monkeypatch.setattr(cli, "_sweep_config_already_done", lambda engine, chash: False)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(cli, "_load_bars_by_ticker", lambda engine, tickers, config: {})
    monkeypatch.setattr(ingest, "run_job", _counting_run_job())

    calls = []

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        calls.append((config, run_id, tuple(tickers), max_workers, full_universe))
        return BacktestReport(
            run_id=run_id, rows_written=1, tickers=list(tickers), failed_tickers={}
        )

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)

    _call(sweep=True, workers=3)

    expected_configs = backtest_mod.sweep_configs(DEFAULT_CONFIG)
    expected_hashes = [config_hash(c) for c in expected_configs]

    assert len(calls) == 18
    assert [config_hash(c) for c, _rid, _t, _w, _fu in calls] == expected_hashes
    # Every call gets its own run_id (one `runs` row per execution).
    assert len({rid for _c, rid, _t, _w, _fu in calls}) == 18
    # Ticker list, worker count and full_universe are threaded through unchanged.
    assert all(t == ("AAPL",) for _c, _rid, t, _w, _fu in calls)
    assert all(w == 3 for _c, _rid, _t, w, _fu in calls)


def test_sweep_failure_at_config_n_does_not_discard_earlier_configs(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prior_clean_default_run_exists", lambda engine, chash: True)
    monkeypatch.setattr(cli, "_sweep_config_already_done", lambda engine, chash: False)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(ingest, "run_job", _counting_run_job())

    calls = []

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        calls.append(config)
        if len(calls) == 5:
            raise BacktestRunFailed({"AAPL": "ValueError: boom"})
        return BacktestReport(
            run_id=run_id, rows_written=1, tickers=list(tickers), failed_tickers={}
        )

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)

    with pytest.raises(typer.Exit) as exc_info:
        _call(sweep=True)

    assert exc_info.value.exit_code == 1
    # Stopped at the failing config — configs 6-18 were never dispatched,
    # and nothing here rolls back or re-executes 1-4.
    assert len(calls) == 5
    out = capsys.readouterr().out
    assert "5" in out and "18" in out


def test_sweep_resume_skips_already_completed_configs(monkeypatch):
    """A rerun after an interrupt must resume, not redo or duplicate: a
    config whose `runs` row already shows a clean completion is skipped
    entirely — `run_backtest` (and therefore the `events` upsert) is never
    called for it a second time.
    """
    monkeypatch.setattr(cli, "_prior_clean_default_run_exists", lambda engine, chash: True)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(ingest, "run_job", _counting_run_job())

    expected_configs = backtest_mod.sweep_configs(DEFAULT_CONFIG)
    expected_hashes = [config_hash(c) for c in expected_configs]
    done_hashes = set(expected_hashes[:14])  # first 14 already ran

    monkeypatch.setattr(
        cli, "_sweep_config_already_done", lambda engine, chash: chash in done_hashes
    )

    calls = []

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        calls.append(config_hash(config))
        return BacktestReport(
            run_id=run_id, rows_written=1, tickers=list(tickers), failed_tickers={}
        )

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)

    _call(sweep=True)

    assert len(calls) == 4
    assert set(calls) == set(expected_hashes[14:])
    assert not (set(calls) & done_hashes)


def test_sweep_config_already_done_queries_expected_filters(monkeypatch):
    """Mirrors `test_prior_clean_default_run_exists_queries_expected_filters`
    for the sweep's own per-config resume check: scoped to
    `job = 'backtest_sweep'`, distinct from `job = 'backtest'` so a
    completed default run and a completed sweep member are never confused.
    """
    captured = {}

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult(self._row)

    class _FakeEngine:
        def __init__(self, row):
            self._row = row

        def connect(self):
            return _FakeConn(self._row)

    assert cli._sweep_config_already_done(_FakeEngine(("1",)), "abc123") is True
    assert captured["params"] == {"chash": "abc123"}
    assert "job = 'backtest_sweep'" in captured["sql"]
    assert "status = 'ok'" in captured["sql"]
    assert "notes IS NULL" in captured["sql"]
    assert "full_universe" in captured["sql"]

    assert cli._sweep_config_already_done(_FakeEngine(None), "abc123") is False


def test_prior_clean_default_run_exists_queries_expected_filters(monkeypatch):
    """Unit-tests the gate helper itself against a fake connection, without
    a real database: asserts the query is scoped to job='backtest',
    status='ok', notes IS NULL, this exact config_hash, and full_universe.
    """
    captured = {}

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult(self._row)

    class _FakeEngine:
        def __init__(self, row):
            self._row = row

        def connect(self):
            return _FakeConn(self._row)

    assert _REAL_PRIOR_CLEAN_CHECK(_FakeEngine(("1",)), "abc123") is True
    assert captured["params"] == {"chash": "abc123"}
    assert "job = 'backtest'" in captured["sql"]
    assert "status = 'ok'" in captured["sql"]
    assert "notes IS NULL" in captured["sql"]
    assert "full_universe" in captured["sql"]

    assert _REAL_PRIOR_CLEAN_CHECK(_FakeEngine(None), "abc123") is False


# ---------------------------------------------------------------------------
# --config-name is an explicit, undefined-until-refused flag
# ---------------------------------------------------------------------------


def test_config_name_errors_without_touching_run_backtest(monkeypatch, capsys):
    run_backtest_called = []
    monkeypatch.setattr(backtest_mod, "run_backtest", lambda *a, **k: run_backtest_called.append(1))

    with pytest.raises(typer.Exit) as exc_info:
        _call(config_name="alt_config")

    assert exc_info.value.exit_code == 1
    assert not run_backtest_called
    out = capsys.readouterr().out
    assert "config-name" in out


# ---------------------------------------------------------------------------
# BacktestRunFailed -> clear error, exit 1, no traceback escapes
# ---------------------------------------------------------------------------


def test_backtest_run_failed_surfaces_as_clean_error(monkeypatch, capsys):
    def _raise(*a, **k):
        raise BacktestRunFailed({"AAPL": "ValueError: boom", "MSFT": "ValueError: boom"})

    monkeypatch.setattr(backtest_mod, "run_backtest", _raise)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL", "MSFT"])

    with pytest.raises(typer.Exit) as exc_info:
        _call(tickers="AAPL,MSFT")

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "backtest run failed" in out.lower()


# ---------------------------------------------------------------------------
# Partial failure: reported, and non-zero exit
# ---------------------------------------------------------------------------


def test_partial_failure_reports_failed_tickers_and_exits_nonzero(monkeypatch, capsys):
    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        return BacktestReport(
            run_id=run_id,
            rows_written=3,
            tickers=["AAPL"],
            failed_tickers={"MSFT": "ValueError: bad data"},
        )

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL", "MSFT"])
    monkeypatch.setattr(cli, "_load_bars_by_ticker", lambda engine, tickers, config: {})
    monkeypatch.setattr(
        harness_mod,
        "run_harness",
        lambda events, bars_by_ticker, config, hourly_by_ticker=None, max_workers=1: (
            _passing_harness_report()
        ),
    )

    with pytest.raises(typer.Exit) as exc_info:
        _call(tickers="AAPL,MSFT")

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "MSFT" in out
    assert "1 ticker(s) failed" in out


def test_full_success_no_failed_tickers_exits_zero_when_harness_passes(monkeypatch, capsys):
    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        return BacktestReport(run_id=run_id, rows_written=5, tickers=["AAPL"], failed_tickers={})

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(
        cli, "_load_bars_by_ticker", lambda engine, tickers, config: {"AAPL": pd.DataFrame()}
    )
    monkeypatch.setattr(
        harness_mod,
        "run_harness",
        lambda events, bars_by_ticker, config, hourly_by_ticker=None, max_workers=1: (
            _passing_harness_report()
        ),
    )

    # Should not raise.
    _call(tickers="AAPL")

    out = capsys.readouterr().out
    assert "config_hash" in out


# ---------------------------------------------------------------------------
# config_hash is printed prominently
# ---------------------------------------------------------------------------


def test_config_hash_is_printed(monkeypatch, capsys):
    monkeypatch.setattr(
        backtest_mod,
        "run_backtest",
        lambda tickers, config, run_id, engine=None, max_workers=1, full_universe=True: (
            BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})
        ),
    )
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])

    _call(tickers="AAPL")

    out = capsys.readouterr().out
    assert DEFAULT_HASH in out


# ---------------------------------------------------------------------------
# Harness wiring: automatic after a run with events, skipped when empty,
# gates the exit code on failure.
# ---------------------------------------------------------------------------


def test_harness_runs_automatically_and_gates_exit_code(monkeypatch, capsys):
    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        return BacktestReport(run_id=run_id, rows_written=2, tickers=["AAPL"], failed_tickers={})

    captured = {}

    def _fake_run_harness(events, bars_by_ticker, config, hourly_by_ticker=None, max_workers=1):
        captured["events"] = events
        captured["bars_by_ticker"] = bars_by_ticker
        return _failing_harness_report()

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(
        cli,
        "_load_bars_by_ticker",
        lambda engine, tickers, config: {"AAPL": pd.DataFrame({"ts": []})},
    )
    monkeypatch.setattr(harness_mod, "run_harness", _fake_run_harness)

    with pytest.raises(typer.Exit) as exc_info:
        _call(tickers="AAPL")

    assert exc_info.value.exit_code == 1
    assert "bars_by_ticker" in captured
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_harness_skipped_when_no_events_written(monkeypatch, capsys):
    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        return BacktestReport(run_id=run_id, rows_written=0, tickers=[], failed_tickers={})

    harness_called = []
    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(harness_mod, "run_harness", lambda *a, **k: harness_called.append(1))

    _call(tickers="AAPL")

    assert not harness_called
    out = capsys.readouterr().out
    assert "harness skipped" in out.lower()


# ---------------------------------------------------------------------------
# _load_bars_by_ticker builds the merged shape run_harness needs
# ---------------------------------------------------------------------------


def test_load_bars_by_ticker_merges_bars_and_indicators(monkeypatch):
    bars_df = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "ts": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "adj_close": [1.0, 2.0],
            "volume": [100, 200],
        }
    )
    ind_df = pd.DataFrame(
        {
            "ts": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "bb_upper": [1.1, 2.1],
            "bb_lower": [0.9, 1.9],
            "k_full": [50.0, 60.0],
        }
    )

    calls = {"n": 0}

    def _fake_read_sql(stmt, conn, params=None):
        calls["n"] += 1
        return bars_df if calls["n"] == 1 else ind_df[["ts", "bb_upper", "bb_lower", "k_full"]]

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    monkeypatch.setattr(pd, "read_sql", _fake_read_sql)

    out = _REAL_LOAD_BARS_BY_TICKER(_FakeEngine(), ["AAPL"], DEFAULT_CONFIG)

    assert "AAPL" in out
    merged = out["AAPL"]
    assert "bb_upper" in merged.columns
    assert "close" in merged.columns
    assert len(merged) == 2


def test_load_bars_by_ticker_skips_ticker_with_no_bars(monkeypatch):
    empty = pd.DataFrame(
        columns=["ticker", "ts", "open", "high", "low", "close", "adj_close", "volume"]
    )

    def _fake_read_sql(stmt, conn, params=None):
        return empty

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    monkeypatch.setattr(pd, "read_sql", _fake_read_sql)

    out = _REAL_LOAD_BARS_BY_TICKER(_FakeEngine(), ["ZZZZ"], DEFAULT_CONFIG)

    assert out == {}


# ---------------------------------------------------------------------------
# The harness is inside the timed block (session 15 prerequisite)
# ---------------------------------------------------------------------------


def test_the_harness_runs_inside_the_run_job_block():
    """`runs.finished_at - started_at` has to cover the whole job.

    It used to cover the write phase alone, because `run_harness` was called
    after the `with ingest.run_job(...)` had closed. The 2026-08-13
    full-universe run measured **4h55m by wall clock and 32m55s in `runs`**,
    and a duration column that silently means "part of the job" is worse
    than no column, because it gets quoted -- a 2026-08-09 session read
    those durations as the whole job and "corrected" CLAUDE.md to ~36
    minutes on the strength of them.

    Structural rather than behavioural: a timing assertion would need a
    clock, and a call-order assertion passes as soon as the two happen in
    sequence, which they always did. What changed is *nesting*, so nesting
    is what this reads.
    """
    import ast
    import inspect

    from capitalscan.jobs import cli

    tree = ast.parse(inspect.getsource(cli.backtest))
    with_blocks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "attr", "") == "run_job"
            for item in node.items
        )
    ]
    assert with_blocks, "cscan backtest no longer opens a run_job block"

    def _calls_harness(node) -> bool:
        return any(
            isinstance(inner, ast.Call) and getattr(inner.func, "id", "") == "run_harness"
            for inner in ast.walk(node)
        )

    assert any(_calls_harness(block) for block in with_blocks), (
        "run_harness is called outside every run_job block, so `runs` times the write phase only"
    )


def test_a_failing_harness_marks_the_run_rather_than_exiting_quietly():
    """ADR 059's sweep gate reads `status = 'ok'`.

    While the harness ran outside the block, a run whose harness failed was
    still recorded `ok`, so `--sweep` would happily start on top of it --
    the exact ordering ADR 059 exists to prevent.
    """
    import ast
    import inspect

    from capitalscan.jobs import cli

    source = inspect.getsource(cli.backtest)
    tree = ast.parse(source)
    raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and getattr(node.exc.func, "id", "") == "_BacktestOutcomeFailed"
    ]
    assert raises, "a failing outcome no longer raises inside the run_job block"
