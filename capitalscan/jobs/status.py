"""Job and schedule health queries backing `cscan system-status`.

DESIGN §9.6 names three monitoring mechanisms and this module owns the
first: "Every job writes a `runs` row. `cscan status` prints last run and
staleness per job." ADR 080 adds the second half: "A delay above 3600
seconds means the machine was off, and `cscan status` surfaces those
explicitly rather than leaving the gap to be inferred."

Both halves were specified from Session 8 and neither was built. `runs` and
`scheduled_runs` have been accumulating the data all along with no reader,
which is why `scheduled_runs.status` sat at `'started'` on every row for
months without anyone noticing: nothing looked.

**Read-only, by design.** Nothing here writes, and in particular nothing
rewrites a `runs` row stuck at `status='running'`. `runs_status_check`
allows only `running`/`ok`/`failed`, and an interrupted process is none of
those three — it did not fail, it was cut off mid-flight while the machine
slept. Marking it `failed` would assert something untrue in a table ADR 034
makes the provenance record. `stale_running()` reports those rows by age
instead, leaving the history honest and the judgment with the reader.

Query functions return frames; rendering lives in `jobs/cli.py`. That split
keeps the numbers testable without a terminal.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import DEFAULT_MONITORING, MonitoringThresholds

__all__ = [
    "job_summary",
    "schedule_summary",
    "stale_running",
]


def job_summary(
    engine: Engine, thresholds: MonitoringThresholds = DEFAULT_MONITORING
) -> pd.DataFrame:
    """Last run per job, with staleness in days.

    `DISTINCT ON (job) ... ORDER BY job, started_at DESC` is Postgres's
    one-pass "latest row per group" — cheaper than a window function or a
    correlated subquery, and `runs` is small (748 rows) so any of the three
    would do.

    Staleness measures from `finished_at` when the run closed and from
    `started_at` when it did not, because a run still open has no finish to
    measure from and reporting NULL there would hide the job entirely from
    a report whose whole purpose is surfacing jobs that stopped happening.

    `is_stale` is a derived flag, never a stored column: the threshold is a
    reporting choice (`MonitoringThresholds.stale_after_days`), and freezing
    it into the table would make it a fact about history rather than a
    question asked of it.
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT DISTINCT ON (job) job, run_id, status, started_at, "
                "finished_at, rows_written, notes "
                "FROM runs ORDER BY job, started_at DESC"
            ),
            conn,
        )
    if df.empty:
        # Assigned as empty typed Series, not scalars: `assign` with a
        # scalar on an empty frame is both a mypy error and a silent shape
        # trap, since the column would broadcast to zero rows anyway. The
        # point is only that a caller's `df.loc[df["is_stale"]]` finds its
        # column on a fresh database instead of raising KeyError.
        df["last_seen"] = pd.Series(dtype="datetime64[ns, UTC]")
        df["staleness_days"] = pd.Series(dtype="float")
        df["is_stale"] = pd.Series(dtype="bool")
        return df

    now = pd.Timestamp.now(tz="UTC")
    last_seen = df["finished_at"].fillna(df["started_at"])
    df["last_seen"] = last_seen
    df["staleness_days"] = (now - pd.to_datetime(last_seen, utc=True)).dt.total_seconds() / 86400.0
    df["is_stale"] = df["staleness_days"] > thresholds.stale_after_days
    return df.sort_values("staleness_days", ascending=False).reset_index(drop=True)


def schedule_summary(
    engine: Engine, thresholds: MonitoringThresholds = DEFAULT_MONITORING
) -> pd.DataFrame:
    """Latest schedule slot per job, with ADR 080's catch-up flag.

    `was_caught_up` marks a slot whose job started more than
    `catch_up_delay_seconds` after its intended fire time. ADR 080 reads
    that as "the machine was off through the intended time," which is a
    normal condition on a workstation rather than a failure, and the reason
    Task Scheduler's catch-up option is enabled at all. It is surfaced so
    the gap is visible rather than inferred, not so it can be alarmed on.
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT DISTINCT ON (job) job, scheduled_for, actual_start, "
                "delay_seconds, status, run_id "
                "FROM scheduled_runs ORDER BY job, scheduled_for DESC"
            ),
            conn,
        )
    if df.empty:
        return df.assign(was_caught_up=False)

    df["was_caught_up"] = df["delay_seconds"].fillna(0) > thresholds.catch_up_delay_seconds
    return df.sort_values("scheduled_for", ascending=False).reset_index(drop=True)


def stale_running(
    engine: Engine, thresholds: MonitoringThresholds = DEFAULT_MONITORING
) -> pd.DataFrame:
    """`runs` rows still marked `running` past `stale_running_hours`.

    These are processes that died before `ingest.run_job`'s context manager
    could record a terminal status: a machine sleeping mid-poll, a Ctrl-C,
    a hard kill. The row is not wrong, it is unfinished, and there is no
    status in `runs_status_check` that says so.

    Reported rather than repaired (see the module docstring). A reader
    seeing eight of these knows the machine slept eight times; a reader
    seeing one from an hour ago should check whether that job is still
    alive before concluding anything.
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT run_id, job, started_at, "
                "EXTRACT(EPOCH FROM (now() - started_at)) / 3600.0 AS open_hours "
                "FROM runs WHERE status = 'running' "
                "AND started_at < now() - make_interval(hours => :hours) "
                "ORDER BY started_at DESC"
            ),
            conn,
            params={"hours": thresholds.stale_running_hours},
        )
    return df
