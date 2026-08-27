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
from capitalscan.jobs import cli, compute
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
    `3e598c59e7d71eae`. New value: `1835688bf7d760ba`.

    Updated 2026-08-13 for ADR 108's `SignalParams.enabled_signal_types`.
    Same mechanism as `stoch_source` above — a new `Config` field changes
    the hashed shape whatever its value — but here the move is the *point*
    rather than a side effect. `signal_strength` counts concurrent types, so
    enabling `BEAR_CLOSE_ABOVE_UPPER` shifts it on every day that fires; the
    field exists so the new signal set gets its own identity instead of
    overwriting the 626,977 events Sessions 12 and 13 published against.
    Old value: `1835688bf7d760ba`. New value: `697f3ae71428d392`.

    Updated 2026-08-14 for ADR 109's `IndicatorParams.bear_close_band_lag`.
    Third instance of the same mechanism, reached by a third route: ADR 109
    changed a *formula* in `core/indicators.py` (the close-confirmed band is
    bar t's own, not t-1's), and a formula is not a `Config` field, so the
    hash did not move on its own. Naming the lag as a field moves it, and
    `bear_close_band_lag=1` keeps ADR 108's superseded population
    reproducible from a config rather than only from a database snapshot.
    Old value: `697f3ae71428d392`. New value: `541f84a384b07ba2`.

    Updated 2026-08-15 for the new `ExitParams.exit_stoch_source` field.
    Unlike every move above, this one changes **no behaviour at all**: it
    defaults to `"k_full"`, the value `core/exits.py` previously hardcoded
    as a string literal. The field exists so the exit's %K column is exit
    policy the way its two thresholds already are, rather than a literal
    that agrees with `SignalParams.stoch_source` by coincidence. That
    coincidence is invisible until the entry moves off `k_full`, which is
    invariant 9's failure mode exactly.
    Old value: `541f84a384b07ba2`. New value: `86e91448a65aa40b`.

    Updated 2026-08-20 for ADR 142, and this move carries two changes at
    once. `SignalParams.fast_agreement_both_extreme` widens the fast/full
    agreement rule so two %K columns also agree when both sit beyond the same
    threshold -- the fourth instance of the ADR 108/109 mechanism, and again
    reached through a formula: the comparison lives in `core/signals.py` and
    would not have moved the hash on its own. `UniverseParams.min_price` was
    deleted in the same commit, dead config since Session 9, bundled here
    because removing it moves the hash and did not justify a rebuild alone.
    Old value: `86e91448a65aa40b`. New value: `bbc99a02ebdc999f`.

    Updated 2026-08-21 for `UniverseParams.min_mcap_usd` 30e9 -> 20e9, the
    universe expansion's own move. Unlike ADR 142's, this one changes *which
    names are measured* rather than which bars fire, so it invalidates every
    row under `bbc99a02ebdc999f` as thoroughly as a signal change would.
    Old value: `bbc99a02ebdc999f`. New value: `a38d3ca6b58295e8`.

    Updated 2026-08-25 for `UniverseParams.sma200_slope_min`, new. The floor
    for `crit_sma200_slope` was the literal `0.0` inside `core/universe.py`,
    which broke invariant 9 and, worse, meant the traded population could be
    changed in code **without moving this hash** -- two universes under one
    hash, the state ADR 060 makes universe definition config to prevent.

    **The default is unchanged at 0.0, so no ticker's membership moves.**
    The hash moves because the field exists, not because the rule changed.
    That is the cost of making it sweepable, and it is paid once: the
    NYSE expansion needs a rebuild anyway, so both land under this hash.
    Old value: `a38d3ca6b58295e8`. New value: `a38d3ca6b58295e8`.

    **The Postgres GUC must not move until a backtest has written events
    under the new hash.** `v_screen` and `compute.scan` both read
    `capitalscan.default_config_hash`, and pointing them at a config with no
    rows yet returns an empty screener rather than an error (invariant 5b's
    deliberate behaviour). Set it after the backtest, not before."""
    assert config_hash(Config()) == "a38d3ca6b58295e8"


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

    def _fake_run_indicators(
        tickers, start, end, engine=None, params=None, max_workers=1, chunk_size=None
    ):
        captured["params"] = params
        captured["chunk_size"] = chunk_size
        return SimpleNamespace(rows_written=0, rows_flagged=0)

    monkeypatch.setattr("capitalscan.jobs.compute.run_indicators", _fake_run_indicators)

    result = runner.invoke(cli.app, ["indicators", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    assert captured["params"].bb_window == 25
    # `chunk_size` bounds peak memory (2026-08-26); the CLI must pass it, not
    # silently fall back to the function default.
    assert captured["chunk_size"] == compute.INDICATOR_CHUNK_SIZE


def test_indicators_command_threads_an_explicit_chunk_size(monkeypatch, tmp_path):
    """The flag has to reach the job. A run that quietly used the default
    would look identical while holding many times the memory."""
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[indicators]" + chr(10)))
    captured = {}

    def _fake(tickers, start, end, engine=None, params=None, max_workers=1, chunk_size=None):
        captured["chunk_size"] = chunk_size
        return SimpleNamespace(rows_written=0, rows_flagged=0)

    monkeypatch.setattr("capitalscan.jobs.compute.run_indicators", _fake)
    result = runner.invoke(cli.app, ["indicators", "--tickers", "AAPL", "--chunk-size", "37"])
    assert result.exit_code == 0, result.output
    assert captured["chunk_size"] == 37


def test_indicators_command_malformed_config_exits_clean(monkeypatch, capsys):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")

    result = runner.invoke(cli.app, ["indicators", "--tickers", "AAPL"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# `cscan universe` threads the resolved UniverseParams through
# ---------------------------------------------------------------------------


def test_universe_command_threads_resolved_params(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_CONFIG_FILE", _toml(tmp_path, "[universe]\nmin_mcap_usd = 5.0\n"))
    captured = {}

    def _fake_run_universe(quarter, tickers=None, engine=None, up=None, today=None, config=None):
        captured["config"] = config
        return SimpleNamespace(tickers=[])

    monkeypatch.setattr("capitalscan.jobs.compute.run_universe", _fake_run_universe)

    result = runner.invoke(cli.app, ["universe", "--quarter", "2026Q3", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    # Any `UniverseParams` field would do; this asserts the section is
    # threaded, not the field. It read `min_price` until ADR 142 removed
    # that field as dead config.
    #
    # **The whole `Config` is threaded now, not just its `universe`
    # section** (d4a17c93f60b): `universe.config_hash` is a hash over the
    # entire Config, so a job handed only `UniverseParams` could not
    # compute the value every other job writes -- the divergence
    # `run_events` records as Final-review Finding 1.
    assert captured["config"] is not None, "run_universe was not given the resolved Config"
    assert captured["config"].universe.min_mcap_usd == 5.0


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

    def _fake_run_poll(
        interval=300, tickers=None, engine=None, sp=None, ep=None, stats=None, config=None
    ):
        captured["ep"] = ep
        captured["config"] = config
        return SimpleNamespace(rows_written=0, notes=None)

    monkeypatch.setattr("capitalscan.jobs.poll.run_poll", _fake_run_poll)

    result = runner.invoke(cli.app, ["poll", "--tickers", "AAPL"])

    assert result.exit_code == 0, result.output
    assert captured["ep"].max_hold_days == 7
    # The whole resolved config, not just the three sections `poll`
    # consumes. `run_poll` used to rebuild `Config(signals=sp)` to compute
    # `config_hash`, which dropped every other section — so an override of
    # `universe`, `splits`, `indicators`, or `costs` made the poller write
    # live rows under a hash no other job used, unjoinable by any statistic
    # and never overwritten by the nightly pass.
    #
    # This fixture sets `[exits] max_hold_days = 7`, a non-default outside
    # `signals`, so the assertion below fails against the old rebuild.
    assert captured["config"] is not None, "cli.poll must thread the resolved config through"
    assert captured["config"].exits.max_hold_days == 7


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

    cli.backtest(
        tickers="AAPL", workers=1, sweep=False, config_name=None, phase="all", chunk_size=25
    )

    assert captured["config"].indicators.bb_window == 25


def test_backtest_command_malformed_config_exits_clean_not_traceback(monkeypatch, capsys):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")

    with pytest.raises(typer.Exit) as exc_info:
        cli.backtest(
            tickers="AAPL", workers=1, sweep=False, config_name=None, phase="all", chunk_size=25
        )

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
    # ADR 093's peak refresh, a third database call in the nightly chain.
    from capitalscan.research import peak_labels as peak_labels_mod

    monkeypatch.setattr(peak_labels_mod, "backfill_peak_labels", lambda *a, **k: 0)

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

    monkeypatch.setattr(
        cli, "_sweep_provisional_poll_rows", lambda *a, **k: 0
    )  # ADR 150; sentinel engine has no .begin()

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


def test_mcp_serve_loads_env_local_before_reading_the_database_url():
    """`cscan mcp serve` must reach `.env.local` like every other command.

    It shipped without this and exited 1 with "neither DATABASE_URL_MCP nor
    DATABASE_URL_RESEARCH is set" while both sat in `.env.local`. Every
    other `cscan` command gets there for free through
    `db_io.get_engine()`; this one never opens an engine of its own, so it
    is the one command that has to load the file explicitly.

    The end-to-end check that verified the server missed it entirely,
    because it set `os.environ` directly and never went through the CLI.

    Read positionally from the AST: `_load_env()` has to run *before*
    `resolve_database_url()`, and a call-presence assertion alone would pass
    with the two in the wrong order.
    """
    import ast
    import inspect

    from capitalscan.jobs import cli

    tree = ast.parse(inspect.getsource(cli.mcp_serve))
    calls = {
        node.func.id: node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_load_env" in calls, "cscan mcp serve no longer loads .env.local"
    assert "resolve_database_url" in calls
    assert calls["_load_env"] < calls["resolve_database_url"], (
        "_load_env runs after the URL is read, so .env.local cannot be seen"
    )
