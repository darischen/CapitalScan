"""`cscan events --lookback` is clamped to `SplitParams.event_start`.

**Why this file exists.** `events` is the only command whose `--lookback`
has a hard floor. `indicators` clamps naturally — a date with no bars
produces no row — but `jobs.config.split_key_for` *raises* on any
`signal_date` before `event_start`, deliberately, because labelling a
pre-2010 row `train` is a leakage bug rather than a cosmetic one.

The problem was never that guard. It was where the guard fires: per row,
inside `run_events`'s build loop, minutes after the job starts. On
2026-08-14 a real `cscan events --lookback 6100` asked for 2009-12-01,
scanned the full universe, and died with `rows_written = 0`. The window
was 31 days past a bound the CLI knew before doing any work.

Clamping the requested *window* leaves the row-level guard exactly as
strict, so both assertions below matter: the clamp happens, and it is
announced. A run that silently covers less history than asked for is
discovered much later, in a population that is short at one end.

`cli.events` is called as a plain function rather than through Typer's
runner, matching `test_path_cli.py` and `test_backtest_cli.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from capitalscan.core.config import Config
from capitalscan.jobs import cli, compute


@pytest.fixture
def captured(monkeypatch):
    """Capture the `(start, end)` window `cli.events` hands `run_events`."""
    seen: dict = {}

    class _Report:
        rows_written = 0
        rows_flagged = 0

    def _fake_run_events(tickers, start, end, *, config=None, **kw):
        seen["start"] = start
        seen["end"] = end
        seen["config"] = config
        return _Report()

    monkeypatch.setattr(compute, "run_events", _fake_run_events)
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda *a, **k: Config())
    monkeypatch.setattr(cli, "_resolve_tickers", lambda t: ["AAPL"])
    return seen


def _event_start(config: Config) -> date:
    return date.fromisoformat(config.splits.event_start)


def test_a_lookback_reaching_before_event_start_is_clamped(captured):
    """The 2026-08-14 failure, as a test: 6100 days reached 2009-12-01."""
    over = (date.today() - _event_start(Config())).days + 31

    cli.events(lookback=over, tickers=None)

    assert captured["start"] == _event_start(Config()), (
        "start must be clamped to event_start, not passed through to raise "
        "inside run_events' build loop"
    )


def test_the_clamp_is_announced_rather_than_silent(captured, capsys):
    """A run covering less history than asked for must say so."""
    over = (date.today() - _event_start(Config())).days + 31

    cli.events(lookback=over, tickers=None)

    out = capsys.readouterr().out
    assert "clamping" in out.lower()
    assert Config().splits.event_start in out


def test_the_exact_boundary_lookback_is_not_clamped(captured):
    """`(today - event_start).days` lands exactly on the bound and is valid.

    Guards against an off-by-one that would clamp the one lookback a caller
    computing the exact full-history window would reach for.
    """
    exact = (date.today() - _event_start(Config())).days

    cli.events(lookback=exact, tickers=None)

    assert captured["start"] == _event_start(Config())


def test_a_lookback_inside_the_window_is_passed_through_untouched(captured):
    """The overwhelmingly common case must not acquire a floor."""
    cli.events(lookback=5, tickers=None)

    assert captured["start"] == date.today() - timedelta(days=5)
    assert captured["end"] == date.today()


def test_no_clamp_message_when_nothing_is_clamped(captured, capsys):
    cli.events(lookback=5, tickers=None)

    assert "clamping" not in capsys.readouterr().out.lower()


def test_the_clamp_reads_the_resolved_config_not_a_hardcoded_date(monkeypatch, captured):
    """`event_start` is a `SplitParams` field and sweepable (ADR 060).

    A literal `2010-01-01` in `cli.py` would pass every test above while
    silently disagreeing with a config that moved the bound — invariant 9's
    failure mode, where the output looks fine either way.
    """
    shifted = Config()
    shifted = replace(shifted, splits=replace(shifted.splits, event_start="2015-06-01"))
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda *a, **k: shifted)

    over = (date.today() - date(2015, 6, 1)).days + 100
    cli.events(lookback=over, tickers=None)

    assert captured["start"] == date(2015, 6, 1)
