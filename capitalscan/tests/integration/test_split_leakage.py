"""Split leakage, structural — TESTS.md §3.5, ADR 019, invariant 5.

**Why this file exists.** CLAUDE.md names five tests that "carry the
correctness load" and says not to weaken any of them. Four of the five are
findable by filename. The fifth, split leakage, was only half implemented:
`split_key_for`'s boundary behaviour is unit-tested in
`test_backtest_config.py`, which proves the *function* assigns the right
label to a date, and nothing asserted the property over the *table*.

Those are different claims. The function can be correct while a row carries
a `split_key` that contradicts its `signal_date` — through a backfill, a
manual UPDATE, a migration, or a writer that set the column from something
other than `split_key_for`. Invariant 5 says `split_key` is assigned at
event creation and never computed at query time, which makes leakage a
schema-level fact rather than a discipline problem, and a schema-level fact
is exactly what an integration test can check and a unit test cannot.

Found by the Sessions 11-14 audit on 2026-08-14. The property held on 5.5M
rows across both live configs at the time of writing; this file is what
would notice it stopping.

**The other half of §3.5 — the purged fold check — is deliberately absent.**
Purged walk-forward cross-validation is Phase 6 (DESIGN §7), and there are no
folds to check yet. It belongs here when that machinery lands, not before.

**Read-only.** `SELECT` only, safe beside a live poller.

    uv run pytest capitalscan/tests/integration/test_split_leakage.py
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.config import Config
from capitalscan.jobs import db_io
from capitalscan.jobs.config import split_key_for


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)

SPLITS = Config().splits
TRAIN_END = date.fromisoformat(SPLITS.train_end)
VALIDATE_END = date.fromisoformat(SPLITS.validate_end)
EVENT_START = date.fromisoformat(SPLITS.event_start)


@pytest.fixture(scope="module")
def engine():
    """Skip the module when `events` is empty, rather than failing.

    CI's slow tier runs against a freshly-migrated container with no rows in
    it. Every assertion below is a count-equals-zero, so most would pass
    vacuously there while the two non-vacuity guards fail — reporting a
    defect where there is only an empty table.

    This is the same shape as `test_cell_grid_measured.py`'s prerequisite
    fixture, and it is here because the first version of this file did not
    have it and CI caught that on its first slow-tier run.
    """
    eng = db_io.get_engine()
    with eng.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM events")).scalar() or 0
    if n < 100_000:
        pytest.skip(
            f"`events` holds {n} rows; this module asserts a property over a populated "
            "table and proves nothing against an empty or seeded one. Expected on a "
            "fresh CI container."
        )
    return eng


def test_no_train_event_is_dated_after_train_end(engine):
    """The leakage that matters most: a training row carrying information
    from the validation window."""
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM events WHERE split_key = 'train' AND signal_date > :b"),
            {"b": TRAIN_END},
        ).scalar()
    assert n == 0, f"{n} train events dated after {TRAIN_END}"


def test_no_holdout_event_is_dated_on_or_before_validate_end(engine):
    """The firewall's date side. ADR 088 guards the *join*; this guards the
    assignment that join depends on."""
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM events WHERE split_key = 'holdout' AND signal_date <= :b"),
            {"b": VALIDATE_END},
        ).scalar()
    assert n == 0, f"{n} holdout events dated on or before {VALIDATE_END}"


def test_no_validate_event_falls_outside_its_window(engine):
    """Both bounds at once. A validate row before `train_end` is training
    data mislabelled; one after `validate_end` is holdout leaking backwards,
    which is the direction that silently inflates a validation result."""
    with engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM events WHERE split_key = 'validate' "
                "AND (signal_date <= :lo OR signal_date > :hi)"
            ),
            {"lo": TRAIN_END, "hi": VALIDATE_END},
        ).scalar()
    assert n == 0, f"{n} validate events outside ({TRAIN_END}, {VALIDATE_END}]"


def test_no_event_predates_event_start(engine):
    """ADR 040: events start 2010-01-01 even though ingest starts 2009-01-01,
    so the indicators have a warmup year. A row before that bound was built
    on a partial window."""
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM events WHERE signal_date < :b"), {"b": EVENT_START}
        ).scalar()
    assert n == 0, f"{n} events dated before {EVENT_START}"


def test_every_split_key_is_one_of_the_three_permitted(engine):
    """`events_split_key_check` enforces this, so a failure here means the
    constraint was dropped rather than that a bad value slipped past it."""
    with engine.connect() as conn:
        found = {r[0] for r in conn.execute(text("SELECT DISTINCT split_key FROM events"))}
    assert found <= {"train", "validate", "holdout"}, f"unexpected split_key values: {found}"


def test_the_stored_split_key_agrees_with_split_key_for(engine):
    """**The assertion the other five cannot make.**

    Each test above checks one boundary. This one checks the whole mapping:
    every distinct `(split_key, signal_date)` combination in the table must
    be what `jobs.config.split_key_for` would assign to that date. A row
    written by any path other than that function — a backfill, a manual
    UPDATE, a migration — is caught here even if it happens to land inside
    the right date range for some other split.

    Grouped rather than row-by-row so 5.5M rows collapse to a few thousand
    distinct dates, which keeps this a seconds-long query.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT split_key, signal_date FROM events ORDER BY signal_date")
        ).all()

    assert rows, "no events found; this test proves nothing against an empty table"
    mismatches = [(stored, when) for stored, when in rows if split_key_for(when, SPLITS) != stored]
    assert not mismatches, (
        f"{len(mismatches)} (split_key, signal_date) pairs disagree with split_key_for; "
        f"first few: {mismatches[:5]}"
    )


def test_the_check_is_not_vacuous(engine):
    """A guard on the guards. Every assertion above is a count-equals-zero,
    which passes trivially on an empty table or a mistyped column name."""
    with engine.connect() as conn:
        total = conn.execute(text("SELECT count(*) FROM events")).scalar()
        per_split = {
            r[0]: r[1]
            for r in conn.execute(text("SELECT split_key, count(*) FROM events GROUP BY 1"))
        }
    assert total and total > 100_000, f"only {total} events; the zero-counts above prove little"
    assert set(per_split) == {"train", "validate", "holdout"}, (
        f"expected all three splits populated, found {sorted(per_split)}"
    )
    for split, n in per_split.items():
        assert n > 0, f"{split} is empty"
