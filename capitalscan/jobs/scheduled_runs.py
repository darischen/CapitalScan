"""Catch-up delay recording (ADR 080, BUILD §8.8).

Task Scheduler's "Run task as soon as possible after a scheduled start is
missed" means a nightly job can start hours late after the machine was off.
This module records the gap between the *intended* time-of-day (DESIGN
§4.12's cadence table) and the actual start, so `cscan status` can surface
it rather than leaving the gap to be inferred.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import Engine, text

from capitalscan.jobs import db_io

# DESIGN §4.12 / ADR 080's schedule. Local (ET) time-of-day per job; the
# nightly/weekly/monthly cadence, not the poller's intraday loop.
SCHEDULE: dict[str, tuple[time, str]] = {
    "nightly": (time(16, 30), "daily"),
    "poll": (time(9, 15), "daily"),
    "weekly": (time(2, 0), "weekly"),  # Sunday
    "monthly": (time(3, 0), "monthly"),  # 1st of the month
}


def _scheduled_for(job: str, as_of: datetime) -> datetime:
    """The most recent intended fire time at or before `as_of`."""
    time_of_day, cadence = SCHEDULE[job]
    candidate = datetime.combine(as_of.date(), time_of_day)
    if cadence == "daily":
        return candidate if candidate <= as_of else candidate - timedelta(days=1)
    if cadence == "weekly":
        days_since_sunday = (as_of.weekday() + 1) % 7  # Monday=0 -> Sunday=6
        candidate = datetime.combine(as_of.date() - timedelta(days=days_since_sunday), time_of_day)
        return candidate if candidate <= as_of else candidate - timedelta(days=7)
    if cadence == "monthly":
        first_of_month = datetime.combine(as_of.date().replace(day=1), time_of_day)
        if first_of_month <= as_of:
            return first_of_month
        prev_month_end = first_of_month.date() - timedelta(days=1)
        return datetime.combine(prev_month_end.replace(day=1), time_of_day)
    raise ValueError(cadence)  # pragma: no cover - SCHEDULE is closed above


def record(engine: Engine, job: str, run_id: str | None = None, now: datetime | None = None) -> int:
    """Upserts one `scheduled_runs` row and returns the delay in seconds.

    A delay above 3600 s means the machine was off through the intended
    fire time (ADR 080) — the caller can act on that, this module only
    measures it.
    """
    now = now or datetime.now()
    scheduled_for = _scheduled_for(job, now)
    delay_seconds = int((now - scheduled_for).total_seconds())
    db_io.upsert(
        engine,
        "scheduled_runs",
        [
            {
                "job": job,
                "scheduled_for": scheduled_for,
                "actual_start": now,
                "delay_seconds": delay_seconds,
                "status": "started",
                "run_id": run_id,
            }
        ],
        ["job", "scheduled_for"],
    )
    return delay_seconds


def complete(engine: Engine, job: str, status: str, run_id: str | None = None) -> int:
    """Close the slot `record` opened, returning rows updated (0 or 1).

    Without this, `scheduled_runs.status` could only ever hold `'started'` —
    `record` wrote that literal and nothing else ever touched the column.
    Measured 2026-08-09: every row in the table, going back to Session 8,
    said `'started'`, including jobs that had finished successfully days
    earlier. ADR 080 lists `status` as part of this table's contract, so the
    column existed and simply had no writer for its terminal half.

    **Targets the job's most recent slot, not a recomputed one.** The
    obvious implementation calls `_scheduled_for(job, now())` again and
    updates that key, which is wrong across a slot boundary: `nightly` is
    scheduled at 16:30, so a run starting 16:29 and finishing 16:31 opens
    the previous day's slot and would close the current day's, leaving one
    row permanently `'started'` and marking another complete that never
    ran. `max(scheduled_for)` is unambiguous — `record` has just written
    the newest slot for this job — and is immune to how long the job took.

    `run_id` is written here rather than at `record` time because the two
    are ordered the other way around: `record` fires before config
    resolution (deliberately, so a config failure still leaves a schedule
    trace), and `ingest.run_job` does not mint a `run_id` until after. That
    ordering is why `nightly`, `weekly`, and `monthly` all left the column
    NULL, breaking the join back to `runs` that ADR 080 specified it for.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE scheduled_runs SET status = :status, "
                "run_id = COALESCE(:run_id, run_id) "
                "WHERE job = :job AND scheduled_for = ("
                "  SELECT max(scheduled_for) FROM scheduled_runs WHERE job = :job)"
            ),
            {"job": job, "status": status, "run_id": run_id},
        )
    return int(result.rowcount)
