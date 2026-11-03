"""`nightly` closes its `scheduled_runs` slot after the sync, not before.

**The incident, 2026-10-18.** The Sunday nightly follows the Saturday
weekly, so its incremental sync re-ships every event the weekly relabelled.
It ran into the unit's `RuntimeMaxSec=4h` and systemd killed it. The slot
had been marked `ok` before the sync started, so the restart 15 minutes
later asked `resume-check`, heard "completed this period", and skipped.
Serving stayed behind until the next afternoon's run, and the next
morning's premarket inherited the whole backlog and was killed too.

A sync that *raises* is caught inside `nightly` and the chain still closes
`ok`, by design: research is the source of truth. Only a kill leaves the
slot `'started'`, and that is the state `resume_decision` reads as "run".

These tests pin the order in the source, the same way
`test_nightly_sweep_ordering.py` pins the sweep: a behavioural test would
need a real sync killed mid-way.
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import cli


def _nightly_source() -> str:
    return inspect.getsource(cli.nightly)


def test_the_slot_closes_after_the_sync_call():
    src = _nightly_source()
    assert src.index('scheduled_runs.complete(engine, slot, "ok"') > src.index("run_sync("), (
        "nightly marks its slot ok before the sync; a sync killed by the unit's "
        "time limit is then never retried (2026-10-18)"
    )


def test_the_slot_closes_after_the_serving_sweep():
    src = _nightly_source()
    assert src.index('scheduled_runs.complete(engine, slot, "ok"') > src.index("serving_engine()")


def test_the_slot_is_closed_exactly_once():
    assert _nightly_source().count("scheduled_runs.complete(") == 1


def test_the_source_records_why():
    assert "2026-10-18" in _nightly_source()
