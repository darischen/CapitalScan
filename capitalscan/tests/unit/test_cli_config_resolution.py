"""Task: wire `jobs.config.resolve_config` into the CLI (ADR 091, BUILD §0.5).

Before this task, every command constructed config dataclasses directly
(`Config()`, `IndicatorParams()`, ...), so a `config.toml` or `CAPSCAN_*`
env var a user set was silently ignored. These tests pin:

  1. `cli._resolve_config_or_exit` is the one place that calls
     `jobs.config.resolve_config`, pinned to a repo-root `config.toml`
     rather than the process CWD (a resolver reading ambient CWD makes the
     result depend on where the user stood when they typed the command —
     see `test_jobs_config.py`'s module docstring).
  2. A `ConfigError` surfaces as a clean `console.print` + `typer.Exit(1)`,
     never a traceback.
  3. `config_hash(Config())` is unchanged by this wiring — no `config.toml`
     and no `CAPSCAN_*` env vars in this environment means `resolve_config`
     returns dataclass defaults, same as the `Config()` call it replaces.
  4. Each wired command threads the *resolved* config section through to
     the job function it already calls, instead of letting that job
     function default the section itself.

Every test pins `cli._CONFIG_FILE` explicitly and clears `CAPSCAN_*` (same
isolation pattern as `test_jobs_config.py`) so a stray ambient variable or a
`config.toml` some other test leaves behind cannot make this suite green by
accident.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from capitalscan.core.config import Config
from capitalscan.jobs import cli
from capitalscan.jobs.config import config_hash

SECTIONS = ["indicators", "signals", "exits", "costs", "universe", "stats", "splits"]

runner = CliRunner()

# Captured before the autouse fixture below overrides `cli._CONFIG_FILE` for
# every other test — the one test that checks the real default needs the
# unpatched value.
_REAL_CONFIG_FILE = cli._CONFIG_FILE


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for section in SECTIONS:
        monkeypatch.delenv(f"CAPSCAN_{section.upper()}", raising=False)
    # Default every test to a config file that does not exist, so the
    # "no config.toml" case is the default and each test opts in to a real
    # file explicitly.
    monkeypatch.setattr(cli, "_CONFIG_FILE", str(tmp_path / "absent.toml"))


def _toml(tmp_path, body: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return str(path)


# ---------------------------------------------------------------------------
# The pinned config_hash value (task requirement)
# ---------------------------------------------------------------------------


def test_default_config_hash_is_pinned():
    """This value is tracked by the human partner to set a Postgres GUC.
    Wiring the resolver in must not move it.

    Updated 2026-08-05 for two Session 10 changes, both genuine fields of
    `Config` and therefore both real `config_hash` moves (ADR 060):
    `UniverseParams.min_mcap_usd` 100e9 -> 30e9 (user's decision,
    2026-08-03), and the new `SignalParams.stoch_source` field (defaults
    to `"k_full"`, same detection behavior as before the field existed,
    but the field's presence still changes the hashed shape). Old value:
    `3e598c59e7d71eae`. New value: `1835688bf7d760ba`."""
    assert config_hash(Config()) == "1835688bf7d760ba"


# ---------------------------------------------------------------------------
# `_resolve_config_or_exit` — the one call site for `resolve_config`
# ---------------------------------------------------------------------------


def test_resolve_config_or_exit_defaults_with_no_file_or_env():
    cfg = cli._resolve_config_or_exit()
    assert cfg == Config()


def test_resolve_config_or_exit_is_pinned_to_repo_root_not_cwd():
    """`resolve_config`'s own default (`config_file="config.toml"`) reads
    the process CWD. The CLI must not inherit that — it pins an explicit
    path so the result is the same regardless of where `cscan` was invoked
    from."""
    assert _REAL_CONFIG_FILE != "config.toml"
    assert _REAL_CONFIG_FILE.endswith("config.toml")


def test_resolve_config_or_exit_applies_file_override(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[indicators]\nbb_window = 25\n"))
    cfg = cli._resolve_config_or_exit()
    assert cfg.indicators.bb_window == 25


def test_resolve_config_or_exit_applies_cli_overrides_on_top(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[indicators]\nbb_window = 25\n"))
    cfg = cli._resolve_config_or_exit(cli_overrides={"indicators": {"bb_window": 30}})
    assert cfg.indicators.bb_window == 30


def test_resolve_config_or_exit_raises_typer_exit_on_malformed_env(monkeypatch, capsys):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")
    with pytest.raises(typer.Exit) as exc_info:
        cli._resolve_config_or_exit()
    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "error" in out.lower()


def test_resolve_config_or_exit_error_message_names_the_bad_variable(monkeypatch, capsys):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "not json")
    with pytest.raises(typer.Exit):
        cli._resolve_config_or_exit()
    out = capsys.readouterr().out
    assert "CAPSCAN_INDICATORS" in out


# ---------------------------------------------------------------------------
# `cscan indicators` threads the resolved IndicatorParams through
# ---------------------------------------------------------------------------


def test_indicators_command_threads_resolved_params(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[indicators]\nbb_window = 25\n"))
    captured = {}

    def _fake_run_indicators(tickers, start, end, engine=None, params=None, max_workers=1):
        captured["params"] = params
        return SimpleNamespace(rows_written=0, rows_flagged=0)

    monkeypatch.setattr("capitalscan.jobs.compute.run_indicators", _fake_run_indicators)

    result = runner.invoke(cli.app, ["indicators", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    assert captured["params"].bb_window == 25


def test_indicators_command_malformed_config_exits_clean(monkeypatch, capsys):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")

    result = runner.invoke(cli.app, ["indicators", "--tickers", "AAPL"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# `cscan universe` threads the resolved UniverseParams through
# ---------------------------------------------------------------------------


def test_universe_command_threads_resolved_params(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[universe]\nmin_price = 5.0\n"))
    captured = {}

    def _fake_run_universe(quarter, tickers=None, engine=None, up=None, today=None):
        captured["up"] = up
        return SimpleNamespace(tickers=[])

    monkeypatch.setattr("capitalscan.jobs.compute.run_universe", _fake_run_universe)

    result = runner.invoke(cli.app, ["universe", "--quarter", "2026Q3", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    assert captured["up"].min_price == 5.0


# ---------------------------------------------------------------------------
# `cscan events` threads the resolved SignalParams through
# ---------------------------------------------------------------------------


def test_events_command_threads_resolved_params(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[signals]\nstoch_oversold = 25.0\n"))
    captured = {}

    def _fake_run_events(tickers, target_start, target_end, engine=None, sp=None, config=None):
        captured["config"] = config
        return SimpleNamespace(rows_written=0, rows_flagged=0)

    monkeypatch.setattr("capitalscan.jobs.compute.run_events", _fake_run_events)

    result = runner.invoke(cli.app, ["events", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    assert captured["config"].signals.stoch_oversold == 25.0


# ---------------------------------------------------------------------------
# `cscan poll` threads the resolved SignalParams/ExitParams/StatsParams through
# ---------------------------------------------------------------------------


def test_poll_command_threads_resolved_params(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[exits]\nmax_hold_days = 7\n"))
    captured = {}

    def _fake_run_poll(interval=300, tickers=None, engine=None, sp=None, ep=None, stats=None):
        captured["ep"] = ep
        return SimpleNamespace(rows_written=0, notes=None)

    monkeypatch.setattr("capitalscan.jobs.poll.run_poll", _fake_run_poll)

    result = runner.invoke(cli.app, ["poll", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    assert captured["ep"].max_hold_days == 7


def test_poll_command_blocks_on_malformed_unrelated_section(monkeypatch, capsys):
    """`resolve_config` validates all seven sections unconditionally
    (`jobs/config.py::_read_env` loops over every `SECTIONS` key), so a
    malformed `CAPSCAN_COSTS` blocks `poll` even though `poll` only ever
    consumes `signals`/`exits`/`stats`. This is `resolve_config`'s existing
    all-or-nothing contract (ADR 091: raise rather than default), not a
    `poll`-specific special case — pinned here so the behavior is
    deliberate, not incidental, and so a `poll` operator debugging a
    startup failure has a test to point at.
    """
    monkeypatch.setenv("CAPSCAN_COSTS", "{bad json")

    result = runner.invoke(cli.app, ["poll", "--tickers", "AAPL"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "CAPSCAN_COSTS" in result.output


# ---------------------------------------------------------------------------
# `cscan backtest` resolves config instead of calling `Config()` directly
# ---------------------------------------------------------------------------


def test_backtest_command_uses_resolved_config(monkeypatch, tmp_path):
    from capitalscan.jobs import db_io, ingest
    from capitalscan.research import backtest as backtest_mod

    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[indicators]\nbb_window = 25\n"))
    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")

    from contextlib import contextmanager

    @contextmanager
    def _fake_run_job(engine, job, params):
        yield ingest.IngestReport(job=job, run_id="fake_run_id")

    monkeypatch.setattr(ingest, "run_job", _fake_run_job)

    captured = {}

    def _fake_run_backtest(tickers, config, run_id, engine=None, max_workers=1, full_universe=True):
        captured["config"] = config
        return backtest_mod.BacktestReport(
            run_id=run_id, rows_written=0, tickers=[], failed_tickers={}
        )

    monkeypatch.setattr(backtest_mod, "run_backtest", _fake_run_backtest)

    cli.backtest(tickers="AAPL", workers=1, sweep=False, config_name=None)

    assert captured["config"].indicators.bb_window == 25


def test_backtest_command_malformed_config_exits_clean_not_traceback(monkeypatch, capsys):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")

    with pytest.raises(typer.Exit) as exc_info:
        cli.backtest(tickers="AAPL", workers=1, sweep=False, config_name=None)

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out


# ---------------------------------------------------------------------------
# `cscan nightly` threads the resolved IndicatorParams/SignalParams through
# to `compute.run_indicators` / `compute.run_events`, its two config-taking
# steps. Found missing in review: the original wiring pass touched the
# standalone `indicators`/`events`/`universe`/`poll`/`backtest` commands but
# not `nightly`'s own calls into the same two `compute` functions, so a
# user with a `config.toml` got one config from `cscan indicators` and a
# different (default) one from the Task Scheduler `nightly` chain calling
# the identical function.
# ---------------------------------------------------------------------------


def _patch_nightly_io(monkeypatch):
    """Stubs every IO call `nightly` makes except the two `compute.*` calls
    under test, so this stays a unit test (CONSTRAINTS.md: no real IO)."""
    from contextlib import contextmanager

    from capitalscan.jobs import db_io, ingest, scheduled_runs
    from capitalscan.research import path_backfill as path_backfill_mod
    from capitalscan.research.path_backfill import PathBackfillReport

    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")
    monkeypatch.setattr(scheduled_runs, "record", lambda engine, job: None)
    # Same reason as `record`: `complete` (added 2026-08-09) is a second
    # database call in the same chain, stubbed so this stays a unit test.
    monkeypatch.setattr(scheduled_runs, "complete", lambda *a, **k: 1)
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    monkeypatch.setattr(path_backfill_mod, "run_path_capture", lambda *a, **k: PathBackfillReport())

    # `nightly`'s path capture runs inside `ingest.run_job` so its `path`
    # rows carry a `run_id` (ADR 034); the real one writes a `runs` row.
    @contextmanager
    def _fake_run_job(engine, job, params):
        yield ingest.IngestReport(job=job, run_id="test-run-id")

    monkeypatch.setattr(ingest, "run_job", _fake_run_job)
    for name in (
        "run_bars_daily",
        "run_bars_hourly",
        "run_actions",
        "run_market",
        "run_shares",
        "run_earnings",
    ):
        monkeypatch.setattr(ingest, name, lambda *a, **k: None)


def test_nightly_command_threads_resolved_config(monkeypatch, tmp_path):
    from capitalscan.jobs import compute

    monkeypatch.setattr(
        cli,
        "_CONFIG_FILE",
        _toml(tmp_path, "[indicators]\nbb_window = 25\n[signals]\nstoch_oversold = 25.0\n"),
    )
    _patch_nightly_io(monkeypatch)

    captured = {}

    def _fake_run_indicators(tickers, start, end, engine=None, params=None, max_workers=1):
        captured["params"] = params

    def _fake_run_events(tickers, target_start, target_end, engine=None, sp=None, config=None):
        captured["config"] = config

    monkeypatch.setattr(compute, "run_indicators", _fake_run_indicators)
    monkeypatch.setattr(compute, "run_events", _fake_run_events)

    cli.nightly()

    assert captured["params"].bb_window == 25
    assert captured["config"].signals.stoch_oversold == 25.0


def test_nightly_command_malformed_config_exits_before_any_ingest_or_compute_io(
    monkeypatch, capsys
):
    """A malformed config must fail the chain before any bars/actions/
    market/shares/earnings/indicators/events job runs — verified by *not*
    patching those calls away and asserting the config error wins first
    (they would raise `AssertionError` if the chain reached that far).

    `scheduled_runs.record` is the one exception, and deliberately so
    (ADR 080): it is recorded *before* config resolution precisely so
    `cscan status` can tell "the scheduler never fired" apart from "nightly
    fired and died on a bad config" — both would otherwise be silently
    absent from `scheduled_runs`. So this test asserts `record` WAS called,
    while every `ingest.run_*`/`compute.run_*` step was NOT — "no partial
    pipeline," not "no writes at all."
    """
    from capitalscan.jobs import db_io, ingest, scheduled_runs

    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")
    monkeypatch.setattr(db_io, "get_engine", lambda: "fake-engine")

    record_calls = []
    monkeypatch.setattr(
        scheduled_runs, "record", lambda engine, job: record_calls.append((engine, job))
    )

    def _unreached(*a, **k):
        raise AssertionError("ingest/compute IO reached despite malformed config")

    for name in (
        "run_bars_daily",
        "run_bars_hourly",
        "run_actions",
        "run_market",
        "run_shares",
        "run_earnings",
    ):
        monkeypatch.setattr(ingest, name, _unreached)

    with pytest.raises(typer.Exit) as exc_info:
        cli.nightly()

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert record_calls == [("fake-engine", "nightly")]


# ---------------------------------------------------------------------------
# Minors: CLI-layer coverage for a malformed config.toml and an unknown
# section override, exercised through the CLI helper rather than only
# `jobs/config.py`'s own suite.
# ---------------------------------------------------------------------------


def test_malformed_config_toml_exits_clean(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[indicators\nbb_window = 25\n"))

    with pytest.raises(typer.Exit) as exc_info:
        cli._resolve_config_or_exit()

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out


def test_unknown_section_in_cli_overrides_exits_clean(capsys):
    with pytest.raises(typer.Exit) as exc_info:
        cli._resolve_config_or_exit(cli_overrides={"indicatorz": {"bb_window": 20}})

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "indicatorz" in out
