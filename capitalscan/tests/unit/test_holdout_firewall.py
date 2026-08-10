"""Holdout firewall — ADR 088, TESTS.md §3.6.

A live event carries `split_key = 'holdout'` by date assignment. A view that
attaches statistics to events by inheriting `e.split_key` would surface
holdout numbers in the screener every day, silently and indefinitely, and
the numbers would look entirely reasonable.

**The rule is about joining, not about exposing.** A view that *attaches*
statistics to events must pin `split_key = 'validate'`. A view that
*projects* `cell_stats` as a table — `v_stats` — never joins an event, so
`split_key` as a queryable dimension is correct research-surface behaviour.

`EVENT_JOINED_STAT_VIEWS` names the first kind. The completeness test below
scans every view definition and fails if a view joins `events` to
`cell_stats` without being in the set, so the list cannot go stale when
Phase 5 adds views.

Reads `db/schema.sql`, so this runs in the fast tier with no database.

That file must be regenerated after every migration, or this test is
checking a schema that no longer exists. It drifted twice while this
comment merely asserted the requirement, so the requirement now has an
enforcer: `tests/integration/test_schema_drift.py` diffs the committed file
against a live `pg_dump`. Do not weaken the coupling by relaxing that test
instead of running `cscan db schema`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[3] / "db" / "schema.sql"

# Views that attach statistics to events. These must pin the split.
EVENT_JOINED_STAT_VIEWS = {"v_screen"}

# What ADR 088 requires pinned wherever cell_stats is joined to events.
REQUIRED_PINS = {
    "split_key": r"split_key\s*=\s*'validate'",
    "era": r"era\s+IS\s+NULL",
    "horizon_days": r"horizon_days\s*=\s*5",
    "target_pct": r"target_pct\s*=\s*0\.03",
}


def _views() -> dict[str, str]:
    sql = SCHEMA.read_text(encoding="utf-8")
    return {
        name: ddl for name, ddl in re.findall(r"CREATE VIEW public\.(\w+) AS\n(.*?);\n", sql, re.S)
    }


@pytest.fixture(scope="module")
def views() -> dict[str, str]:
    assert SCHEMA.exists(), f"{SCHEMA} missing; regenerate with `cscan db schema`"
    found = _views()
    assert found, "no views parsed from schema.sql"
    return found


def _joins_events_to_stats(ddl: str) -> bool:
    return "cell_stats" in ddl and re.search(r"\bevents\b", ddl) is not None


# ---------------------------------------------------------------------------
# The firewall
# ---------------------------------------------------------------------------


def test_the_named_views_exist(views):
    missing = EVENT_JOINED_STAT_VIEWS - set(views)
    assert not missing, f"named views absent from schema: {sorted(missing)}"


@pytest.mark.parametrize("view", sorted(EVENT_JOINED_STAT_VIEWS))
def test_event_joined_stat_views_pin_split_to_validate(views, view):
    ddl = views[view]
    assert re.search(REQUIRED_PINS["split_key"], ddl), f"{view} must pin split_key = 'validate'"


@pytest.mark.parametrize("view", sorted(EVENT_JOINED_STAT_VIEWS))
def test_event_joined_stat_views_never_inherit_the_event_split(views, view):
    ddl = views[view]
    assert not re.search(r"c\.split_key\s*=\s*e\.split_key", ddl), (
        f"{view} inherits the event's split_key; a live event is 'holdout'"
    )


@pytest.mark.parametrize("view", sorted(EVENT_JOINED_STAT_VIEWS))
@pytest.mark.parametrize("pin", sorted(REQUIRED_PINS))
def test_event_joined_stat_views_pin_every_report_parameter(views, view, pin):
    # ADR 088: horizon_days and target_pct are report parameters, not event
    # properties, so one event belongs to many cells. Leaving either unpinned
    # multiplies screener rows instead of failing.
    assert re.search(REQUIRED_PINS[pin], views[view]), f"{view} must pin {pin}"


# ---------------------------------------------------------------------------
# Completeness — the list cannot go stale
# ---------------------------------------------------------------------------


def test_no_unlisted_view_joins_events_to_cell_stats(views):
    """The guard that keeps EVENT_JOINED_STAT_VIEWS honest.

    A Phase 5 view joining events to cell_stats without being listed here
    fails this test rather than silently bypassing the firewall.
    """
    offenders = {
        name
        for name, ddl in views.items()
        if _joins_events_to_stats(ddl) and name not in EVENT_JOINED_STAT_VIEWS
    }
    assert not offenders, (
        f"view(s) {sorted(offenders)} join events to cell_stats but are not in "
        "EVENT_JOINED_STAT_VIEWS. Add them and pin split_key = 'validate', or "
        "restructure so they do not attach statistics to events."
    )


def test_a_projection_only_stats_view_is_not_required_to_pin(views):
    """v_stats exposes cell_stats as a table rather than attaching it to
    events, so split_key is a legitimate query dimension there."""
    if "v_stats" not in views:
        pytest.skip("v_stats not present")
    ddl = views["v_stats"]
    assert "cell_stats" in ddl
    assert not _joins_events_to_stats(ddl), "v_stats must not join events"


def test_every_view_that_reads_cell_stats_is_classified(views):
    """No third category. A view touching cell_stats either joins events (and
    is listed) or projects it (and does not join events)."""
    for name, ddl in views.items():
        if "cell_stats" not in ddl:
            continue
        joins = _joins_events_to_stats(ddl)
        listed = name in EVENT_JOINED_STAT_VIEWS
        assert joins == listed, (
            f"{name}: joins_events={joins} but listed={listed}. "
            "Listed views must join events; unlisted ones must not."
        )


# ---------------------------------------------------------------------------
# Guard on the guard
# ---------------------------------------------------------------------------


def test_the_completeness_scan_would_catch_an_unpinned_view():
    """A synthetic offending view must be detected.

    Without this, the scan passing could mean "no offenders" or "the
    detector does not work".
    """
    offending = (
        "SELECT e.ticker, c.p_hit FROM events e "
        "JOIN cell_stats c ON c.signal_type = e.signal_type AND c.split_key = e.split_key"
    )
    assert _joins_events_to_stats(offending)
    assert not re.search(REQUIRED_PINS["split_key"], offending)
    assert re.search(r"c\.split_key\s*=\s*e\.split_key", offending)


def test_events_carries_no_cell_id_column():
    """Invariant 5b: cell_id is derived, never stored on events."""
    sql = SCHEMA.read_text(encoding="utf-8")
    body = re.search(r"CREATE TABLE public\.events \((.*?)\n\);", sql, re.S)
    assert body, "events table not found in schema.sql"
    assert "cell_id" not in body.group(1)
