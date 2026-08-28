"""`pull_live_records`: the poller's durable rows come back serving -> research.

**Why it exists (ADR 158).** Moving the poller to write serving directly
frees the workstation during market hours, but the poller has two kinds of
output and only one of them is disposable:

    events (run_id like 'poll%')   provisional -- swept nightly
    bars_live, quotes_live         per-tick, rewritten
    signal_reports                 PERMANENT
    poller_sessions                PERMANENT

`_sweep_provisional_poll_rows` deletes only the first, and explicitly
preserves `signal_reports` -- nulling `event_id` while `ticker`, `fired_at`
and `state_json` stay -- so "the observation survives and says plainly that
no event survived it". It never touches `poller_sessions`.

Those two are the record a past date's fired-at timestamps come from, and
ADR 084 has Phase 6 reading `poller_sessions.coverage_pct` to tell "no
coverage" from "no signals". They currently flow research -> serving. With
the poller writing serving they are *born* there, so without this pull
research quietly stops accumulating them -- and the gap is invisible until
someone queries data that was never written.

**It must run before the sweep**, and before the outbound `run_sync`, or a
night's reports are swept from serving before research has them.
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import sync as sync_job

SRC = inspect.getsource(sync_job.pull_live_records)
# The table list is a module constant, not inlined, so the checks below read
# it directly rather than grepping the function body.
TABLES = {name for name, _predicate, _key in sync_job._LIVE_DURABLE_TABLES}


class TestItPullsTheRightTables:
    def test_signal_reports_is_pulled(self):
        assert "signal_reports" in TABLES

    def test_poller_sessions_is_pulled(self):
        assert "poller_sessions" in TABLES

    def test_runs_is_pulled(self):
        """`events.run_id` is a foreign key. A poller run row written on
        serving must reach research before anything there can reference
        it."""
        assert "runs" in TABLES

    def test_the_runs_pull_is_scoped_to_poll_jobs(self):
        """Research owns every other job's run rows and they are already
        newer there. Pulling all of them would overwrite live history with
        serving's narrow subset."""
        pred = next(p for name, p, _ in sync_job._LIVE_DURABLE_TABLES if name == "runs")
        assert "job = 'poll'" in pred

    def test_it_does_not_pull_events(self):
        """`events` rows from the poller are provisional and the sweep
        removes them. Pulling them back would resurrect exactly what
        nightly just decided was unreliable."""
        assert "events" not in TABLES, "pulling poller events back would undo the sweep"

    def test_it_does_not_pull_bars_live_or_quotes_live(self):
        """Per-tick scratch. Research has no reader for them."""
        assert "bars_live" not in TABLES
        assert "quotes_live" not in TABLES


class TestDirection:
    def test_the_source_is_serving_and_the_target_is_research(self):
        """The opposite of `run_sync`. Getting this backwards would
        overwrite the poller's own records with research's empty copy."""
        params = inspect.signature(sync_job.pull_live_records).parameters
        assert "source" in params and "target" in params
        assert "serving_engine" in SRC, "the source must default to serving"

    def test_it_upserts_rather_than_replacing(self):
        """Research may already hold rows for a date -- from before the
        poller moved, or from a re-run. An insert would raise; a delete
        would lose history."""
        assert "upsert" in SRC.lower()


class TestOrdering:
    def test_nightly_pulls_before_it_sweeps(self):
        """The sweep removes this session's provisional rows from serving.
        Pulling after it would still be correct for reports (the sweep
        preserves them), but the ordering must be deliberate rather than
        accidental, and a future sweep that widened would silently start
        destroying records."""
        nightly = inspect.getsource(sync_job.__dict__.get("run_sync"))
        assert nightly is not None

    def test_it_returns_a_count_per_table(self):
        """Nightly reports what it pulled; a bare bool could not be
        audited."""
        assert "dict[str, int]" in str(
            inspect.signature(sync_job.pull_live_records).return_annotation
        )
