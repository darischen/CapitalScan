"""Catch-up delay recording (ADR 080, BUILD §8.8).

Task Scheduler's "Run task as soon as possible after a scheduled start is
missed" means a nightly job can start hours late after the machine was off.
This module records the gap between the *intended* time-of-day (DESIGN
§4.12's cadence table) and the actual start, so `cscan status` can surface
it rather than leaving the gap to be inferred.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Literal

from sqlalchemy import Engine, text

from capitalscan.jobs import db_io

ResumeDecision = Literal["run", "already_complete"]

# DESIGN §4.12 / ADR 080's schedule. Local (ET) time-of-day per job; the
# nightly/weekly/monthly cadence, not the poller's intraday loop.
SCHEDULE: dict[str, tuple[time, str]] = {
    "nightly": (time(16, 30), "daily"),
    "poll": (time(9, 15), "daily"),
    "weekly": (time(2, 0), "weekly"),  # Sunday
    "monthly": (time(3, 0), "monthly"),  # 1st of the month
}


def _scheduled_for(job: str, as_of: datetime) -> datetime:
    """The most recent intended fire time at or before `as_of`.

    **Every candidate carries `as_of`'s tzinfo.** `datetime.combine` returns
    a naive value unless one is supplied, and every branch here compares a
    candidate against `as_of`. ADR 127 made `poll._now_et` aware, and this
    raised on the first tick:

        TypeError: can't compare offset-naive and offset-aware datetimes

    Which is the right failure. The alternative — a naive comparison that
    silently treats an ET wall clock as UTC — is the bug ADR 127 fixed, and
    it would have reappeared here four hours wide.

    A naive `as_of` still works: `tzinfo` is then `None` and `combine`
    behaves as before, so callers that pass a naive clock are unaffected.
    """
    time_of_day, cadence = SCHEDULE[job]
    candidate = datetime.combine(as_of.date(), time_of_day, tzinfo=as_of.tzinfo)
    if cadence == "daily":
        return candidate if candidate <= as_of else candidate - timedelta(days=1)
    if cadence == "weekly":
        days_since_sunday = (as_of.weekday() + 1) % 7  # Monday=0 -> Sunday=6
        candidate = datetime.combine(
            as_of.date() - timedelta(days=days_since_sunday), time_of_day, tzinfo=as_of.tzinfo
        )
        return candidate if candidate <= as_of else candidate - timedelta(days=7)
    if cadence == "monthly":
        first_of_month = datetime.combine(
            as_of.date().replace(day=1), time_of_day, tzinfo=as_of.tzinfo
        )
        if first_of_month <= as_of:
            return first_of_month
        prev_month_end = first_of_month.date() - timedelta(days=1)
        return datetime.combine(prev_month_end.replace(day=1), time_of_day, tzinfo=as_of.tzinfo)
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


def _period_start(job: str, now: datetime) -> datetime:
    """Start of the window `job` is meant to run once inside, as a *naive*
    wall-clock datetime.

    daily -> midnight today; weekly -> the most recent Sunday 00:00
    (matching `_scheduled_for`'s weekday arithmetic); monthly -> the 1st at
    00:00. `now`'s tzinfo is dropped: the only value compared against this
    is `scheduled_runs.actual_start`, whose stored digits are a Pacific
    wall clock regardless of what tzinfo they read back with (see
    `resume_decision`).
    """
    _, cadence = SCHEDULE[job]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    if cadence == "daily":
        return midnight
    if cadence == "weekly":
        days_since_sunday = (now.weekday() + 1) % 7  # Monday=0 -> Sunday=6, so Sunday -> 0
        return midnight - timedelta(days=days_since_sunday)
    if cadence == "monthly":
        return midnight.replace(day=1)
    raise ValueError(cadence)  # pragma: no cover - SCHEDULE is closed above


def resume_decision(
    engine: Engine, job: str, now: datetime | None = None
) -> tuple[ResumeDecision, str]:
    """Whether a timer/boot-triggered `job` still needs to run this period.

    The `run_job` wrappers call this before doing any work.
    `systemd`'s `Persistent=true` re-fires a run that was *missed* while the
    machine was off, and `OnBootSec=` re-fires one that *crashed* mid-run --
    but neither knows whether the chain has since completed. This does:

    - ``("already_complete", detail)`` only when `scheduled_runs` holds a
      `status='ok'` run whose `actual_start` is inside the current period
      (today for nightly, since Sunday for weekly, since the 1st for
      monthly).
    - ``("run", detail)`` for every other state -- `status='failed'`, a
      `status='started'` row with no terminal write (the job crashed or was
      killed), or no row at all.

    Fails toward ``"run"``: the callers treat any error resolving this as
    "run", because redoing an idempotent chain costs time and skipping a
    needed one costs a day of staleness on the served site.

    **All comparison is on naive wall-clock digits.** `record` writes
    `actual_start` as `datetime.now()` -- a naive local timestamp on a
    machine whose clock is Pacific (SETUP.md). It lands in a `timestamptz`
    column under a UTC session, so it reads back tz-aware UTC with the
    Pacific wall-clock digits intact: the tzinfo is wrong, the digits are
    right. Trusting the tzinfo would shift a Sunday-02:00 weekly run onto
    the wrong side of the period boundary. So `now`'s tzinfo is dropped
    too, and both sides are compared as Pacific wall clocks. Pass a
    Pacific `now` (aware or naive); the machine clock is already Pacific.
    """
    now = now or datetime.now()
    if job not in SCHEDULE:
        raise ValueError(f"unknown job {job!r}")

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status, actual_start FROM scheduled_runs "
                "WHERE job = :job AND actual_start IS NOT NULL "
                "ORDER BY actual_start DESC LIMIT 1"
            ),
            {"job": job},
        ).first()

    if row is None:
        return "run", f"{job}: no prior run on record, running"

    status = row[0]
    actual_start = row[1].replace(tzinfo=None)

    in_period = actual_start >= _period_start(job, now)
    stamp = actual_start.strftime("%Y-%m-%d %H:%M")
    if status == "ok" and in_period:
        return "already_complete", f"{job}: completed {stamp} this period, nothing to do"

    reason = {
        "ok": f"last ok run ({stamp}) was a previous period",
        "failed": f"last run ({stamp}) failed",
        "started": f"last run ({stamp}) never finished (crash or kill)",
    }.get(status or "", f"last run status is {status!r}")
    return "run", f"{job}: {reason}, running"
