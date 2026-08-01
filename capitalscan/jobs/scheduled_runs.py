"""Catch-up delay recording (ADR 080, BUILD §8.8).

Task Scheduler's "Run task as soon as possible after a scheduled start is
missed" means a nightly job can start hours late after the machine was off.
This module records the gap between the *intended* time-of-day (DESIGN
§4.12's cadence table) and the actual start, so `cscan status` can surface
it rather than leaving the gap to be inferred.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import Engine

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
