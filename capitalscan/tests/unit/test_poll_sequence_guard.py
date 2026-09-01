"""The poller must refuse a store whose sequences are behind its own rows.

Reconstructed from the session that made it necessary: 2026-08-28 06:56,
serving held 1,829 `signal_reports` with `signal_reports_id_seq` at 21. The
poller inserted, drew a low id, and died on the first collision -- after
writing 18 rows into ids that already meant something else.
"""

from __future__ import annotations

import inspect

import pytest

from capitalscan.jobs import poll as poll_job


class TestAssertSequencesAreAhead:
    def test_an_empty_report_passes(self) -> None:
        poll_job.assert_sequences_are_ahead([])

    def test_a_sequence_behind_its_max_raises(self) -> None:
        with pytest.raises(RuntimeError, match="sequences behind"):
            poll_job.assert_sequences_are_ahead([("signal_reports", 21, 1829)])

    def test_the_message_names_the_table_and_both_numbers(self) -> None:
        """A preflight that says only "bad state" sends the reader to the
        catalogue. The 06:56 failure was diagnosable purely from these three
        values, so the refusal carries them."""
        with pytest.raises(RuntimeError) as exc:
            poll_job.assert_sequences_are_ahead([("signal_reports", 21, 1829)])
        text = str(exc.value)
        assert "signal_reports" in text
        assert "21" in text and "1829" in text

    def test_every_offender_is_named_not_only_the_first(self) -> None:
        """`events_id_seq` was at 21 against 39,167,955 by the same
        mechanism. Reporting one table at a time would have meant two
        failed sessions to learn about two tables."""
        with pytest.raises(RuntimeError) as exc:
            poll_job.assert_sequences_are_ahead(
                [("signal_reports", 21, 1829), ("events", 21, 39_167_955)]
            )
        assert "signal_reports" in str(exc.value)
        assert "events" in str(exc.value)

    def test_it_points_at_the_command_that_repairs_it(self) -> None:
        with pytest.raises(RuntimeError, match="cscan sync"):
            poll_job.assert_sequences_are_ahead([("signal_reports", 21, 1829)])


class TestSequencesBehindQuery:
    """The SQL is not exercised without a database, so pin the properties
    that made the original bug invisible."""

    def test_a_never_drawn_sequence_counts_as_behind(self) -> None:
        """`pg_sequence_last_value` is NULL for a sequence never drawn from.
        Left as NULL it would compare as unknown and the offender would pass
        the filter -- the exact state a freshly synced serving store is in."""
        assert "coalesce(pg_sequence_last_value" in poll_job.SEQUENCE_AUDIT_SQL

    def test_an_empty_table_is_not_an_offender(self) -> None:
        """max_id 0 on an empty table would otherwise trip the check on
        every table nothing has written yet."""
        src = inspect.getsource(poll_job.sequences_behind)
        assert "int(mx) > 0" in src

    def test_equality_is_not_behind(self) -> None:
        """`setval(seq, max)` leaves last_value == max, and the next
        `nextval` returns max+1. That is the correct post-sync state and
        must not be reported."""
        src = inspect.getsource(poll_job.sequences_behind)
        assert "int(last) < int(mx)" in src

    def test_it_derives_tables_from_the_catalogue(self) -> None:
        """A hardcoded list goes stale the moment a table gains a serial,
        and the poller's write set has already changed once (ADR 158)."""
        assert "pg_get_serial_sequence" in poll_job.SEQUENCE_AUDIT_SQL
        for hardcoded in ("'signal_reports'", '"signal_reports"'):
            assert hardcoded not in poll_job.SEQUENCE_AUDIT_SQL


class TestWiredIntoPreflight:
    def test_the_poll_command_calls_the_guard(self) -> None:
        from capitalscan.jobs import cli

        src = inspect.getsource(cli)
        assert "assert_sequences_are_ahead" in src

    def test_it_runs_before_run_poll(self) -> None:
        """A guard that runs after the first tick has already let the bad
        insert happen."""
        from capitalscan.jobs import cli

        src = inspect.getsource(cli)
        assert src.index("assert_sequences_are_ahead") < src.index("report = poll_job.run_poll(")

    def test_both_target_paths_are_guarded(self) -> None:
        """The research path went unguarded until 2026-09-01 and it bit.

        `assert_sequences_are_ahead` lived only inside the `if serving:`
        preflight, so `wait_and_poll.ps1` -- the workstation fallback when
        the Pi skips a day -- reached `run_poll` without it and failed
        mid-session on 2026-08-31 (`signal_reports_pkey`, id 1832). The
        cause arrives from the opposite direction to ADR 158's: rather
        than a copy-only store never advancing its sequences,
        `pull_live_records` copies serving -> research with explicit ids,
        which does not advance research's either.

        Asserted as two calls rather than by reading the branch, because
        the defect was structural -- one call, reachable on one path.
        """
        from capitalscan.jobs import cli

        src = inspect.getsource(cli.poll)
        assert src.count("assert_sequences_are_ahead(behind)") == 2, (
            "both the --serving and the research poll paths must run the "
            "sequence guard; one call means one guarded path"
        )
        assert src.count("poll_job.sequences_behind(conn)") == 2


class TestDroppedColumnsAreExcluded:
    """Found 2026-08-28 by running the query, after eleven green unit tests.

    `DROP COLUMN` does not remove the `pg_attribute` row -- it renames the
    column to a `........pg.dropped.N........` placeholder and sets
    `attisdropped`, so the physical tuple layout is preserved. A catalogue
    sweep over `attnum > 0` therefore still sees it.

    `pg_get_serial_sequence` **raises** on a name that is not a real column
    rather than returning NULL, so the `IS NOT NULL` filter that was meant
    to skip non-serial columns never runs. The whole statement aborts:

        ERROR: column "........pg.dropped.45........" of relation
               "cell_stats" does not exist

    Serving has no dropped columns and passed; research's `cell_stats` has
    one and did not.
    """

    def test_the_poll_audit_excludes_dropped_columns(self) -> None:
        assert "NOT a.attisdropped" in poll_job.SEQUENCE_AUDIT_SQL

    def test_the_sync_reset_excludes_them_too(self) -> None:
        """The same sweep, the same bug. `run_sync` targets serving, which
        has no dropped columns, so it would have failed only once research
        or a future serving store gained one."""
        import inspect

        from capitalscan.jobs import sync as sync_job

        assert "NOT a.attisdropped" in inspect.getsource(sync_job)
