"""`ingest.run_job` closes its `runs` row on Ctrl-C.

**The defect.** `run_job` caught `Exception`. `KeyboardInterrupt` and
`SystemExit` derive from `BaseException`, so that clause never saw them:
Ctrl-C propagated straight out of the context manager, `_finish_run` never
ran, and the row stayed `'running'` forever.

**How it was found.** Four rows sat at `'running'` in the live `runs`
table on 2026-08-16 with no process behind them. Each carried params
*identical* to an `'ok'` row seconds later — the same command, cancelled
and immediately re-run:

    indicators_20260814T224922  running    start=2005-07-15
    indicators_20260814T224933  ok         start=2005-07-15   (+11s)
    events_20260815T034803      running    start=2010-01-01
    events_20260815T035132      ok         start=2010-01-01   (+3m28s)

A `ValueError` run in the same window closed itself correctly, which is
what ruled out a generic teardown problem and pointed at the exception
hierarchy.

**Why it matters beyond tidiness.** Anything reading `runs` to answer "is
a job live right now" gets a false positive that never expires.
`cscan system-status` could not distinguish a running backtest from a
Ctrl-C an hour earlier, and the only cure was a manual UPDATE.

`'interrupted'` rather than `'failed'`: migration `c5b81f2e64a7` added
that status for this state, and the distinction is load-bearing.
`'failed'` means a defect worth investigating; `'interrupted'` means an
operator changed their mind. Collapsing them buries real failures among
routine cancellations.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from capitalscan.jobs import ingest


class _RecordingEngine:
    """Captures `_finish_run`'s writes without a database."""

    def __init__(self) -> None:
        self.finishes: list[dict] = []

    @contextmanager
    def begin(self):  # noqa: ANN201
        yield self

    def execute(self, statement, params=None):  # noqa: ANN001, ANN201
        if params and "status" in params:
            self.finishes.append(dict(params))
        return None


@pytest.fixture
def engine(monkeypatch):
    eng = _RecordingEngine()
    monkeypatch.setattr(ingest, "_start_run", lambda e, job, params: f"{job}_fake_run_id")
    return eng


def _statuses(engine: _RecordingEngine) -> list[str]:
    return [f["status"] for f in engine.finishes]


def test_keyboard_interrupt_closes_the_row_as_interrupted(engine):
    """The defect, directly. Before the fix this list was empty."""
    with pytest.raises(KeyboardInterrupt):
        with ingest.run_job(engine, "indicators", {}):
            raise KeyboardInterrupt

    assert _statuses(engine) == ["interrupted"], (
        "Ctrl-C must close the runs row; an empty list means it stayed 'running' forever"
    )


def test_system_exit_closes_the_row_too(engine):
    """The other `BaseException` a CLI actually raises. `typer.Exit` derives
    from it, so a command exiting non-zero inside a job body would have left
    the same orphan."""
    with pytest.raises(SystemExit):
        with ingest.run_job(engine, "events", {}):
            raise SystemExit(1)

    assert _statuses(engine) == ["failed"]


def test_a_normal_exception_is_still_failed_not_interrupted(engine):
    """The distinction is the point. A defect must not be filed as an
    operator cancellation."""
    with pytest.raises(ValueError):
        with ingest.run_job(engine, "events", {}):
            raise ValueError("signal_date is before event_start")

    assert _statuses(engine) == ["failed"]
    assert "event_start" in engine.finishes[0]["notes"]


def test_a_clean_body_is_still_ok(engine):
    with ingest.run_job(engine, "indicators", {}) as report:
        report.rows_written = 42

    assert _statuses(engine) == ["ok"]
    assert engine.finishes[0]["rows_written"] == 42


def test_the_row_is_closed_exactly_once(engine):
    """`_finish_run` moved out of the `try` body during the fix. If it had
    stayed inside, the success path would write 'ok' and then any failure
    in `_finish_run` itself would write again."""
    with ingest.run_job(engine, "indicators", {}):
        pass

    assert len(engine.finishes) == 1


def test_rows_written_so_far_survives_a_cancellation(engine):
    """A job cancelled mid-write should record what it managed, not zero.
    `path_backfill` writes incrementally, so this is real data."""
    with pytest.raises(KeyboardInterrupt):
        with ingest.run_job(engine, "path_backfill", {}) as report:
            report.rows_written = 1000
            raise KeyboardInterrupt

    assert engine.finishes[0]["rows_written"] == 1000


def test_the_interrupt_still_propagates(engine):
    """This records what happened; it must never swallow the signal.
    `pytest.raises` above already proves it, stated here as the explicit
    contract."""
    raised = False
    try:
        with ingest.run_job(engine, "indicators", {}):
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        raised = True

    assert raised


def test_a_cancellation_note_is_never_empty(engine):
    """`str(KeyboardInterrupt())` is the empty string, which would write a
    blank note and read as "no information recorded" rather than
    "cancelled"."""
    with pytest.raises(KeyboardInterrupt):
        with ingest.run_job(engine, "indicators", {}):
            raise KeyboardInterrupt

    assert engine.finishes[0]["notes"] == "KeyboardInterrupt"
