"""`run_sync` must reset serving's sequences after copying rows into it.

**The incident, 2026-08-28, first live session after the poller moved.**
The Pi's poller inserted a `signal_reports` row, got id 21, and id 21
already existed. It crashed and the session ended eleven minutes in:

    duplicate key value violates unique constraint "signal_reports_pkey"
    DETAIL:  Key (id)=(21) already exists.

`signal_reports.id` is a serial. Serving's rows were **copied from
research by `run_sync` with their original ids**, and an INSERT that
supplies an explicit id does not advance the sequence. So serving held
1,829 rows while its sequence still read 21.

It was invisible until the poller moved, because until then nothing ever
*inserted* into serving -- every row arrived from the sync carrying its own
id. The moment serving became a write target, the sequence mattered.

`events_id_seq` was in the same state: 21, against a max id of 39,167,955.
That would have crashed on the first live event of the session rather than
the first report.

**Why the fix belongs in `run_sync` and not in a one-off repair.** Every
sync copies research's ids again, so a sequence corrected by hand goes
stale on the next nightly. The reset has to happen wherever the copy
happens.
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import sync as sync_job

SRC = inspect.getsource(sync_job.run_sync)


def test_run_sync_resets_sequences():
    """Without this, serving's sequences trail the rows it holds and the
    next insert on the Pi collides."""
    assert "setval" in SRC, (
        "run_sync copies rows with explicit ids, which does not advance "
        "serving's sequences; the poller then inserts and collides"
    )


def test_the_reset_runs_against_the_target_not_the_source():
    """Research's sequences are already correct -- it is where the ids came
    from. Resetting them would be harmless but pointless, and resetting the
    wrong one would leave the real problem in place."""
    # `PERFORM setval`, not bare `setval`: the explanatory comment above the
    # code mentions `setval(seq, 0)`, and that mention comes first.
    idx = SRC.index("PERFORM setval")
    # The nearest connection opened before the reset must be the target's.
    # A character window is too brittle -- the explanatory comment above it
    # is longer than any window worth picking.
    before = SRC[:idx]
    last_target = before.rfind("target.begin(")
    last_source = before.rfind("source.connect(")
    assert last_target > 0, "no target connection precedes the sequence reset"
    assert last_target > last_source, (
        "the sequence reset runs on the source; research's sequences are "
        "already correct and serving's are the ones that trail"
    )


def test_it_derives_the_sequence_rather_than_naming_tables():
    """A hardcoded list goes stale the moment a table gains a serial. The
    catalogue already knows which columns have sequences."""
    assert "pg_get_serial_sequence" in SRC


def test_it_is_guarded_against_an_empty_table():
    """`setval(seq, 0)` is an error in Postgres -- the minimum is 1 -- so a
    table with no rows must be skipped rather than crash the sync."""
    idx = SRC.index("pg_get_serial_sequence")
    window = SRC[idx : idx + 900]
    assert "max(" in window.lower()
    assert "> 0" in window or "coalesce" in window.lower()
