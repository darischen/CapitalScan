"""The poller must store `signal_type` on every report it writes.

Added 2026-08-29 with migration `a4c8d19f6e02`. Without it a report carries
nothing identifying which event it belongs to once ADR 150's nightly sweep
nulls `event_id`, and `v_screen_live` has to match on `(ticker,
signal_date)` -- giving both of a ticker's fires on one day the same
timestamp.
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import poll as poll_job

PAYLOAD_KEY = '"signal_type": signal_type,'


class TestThePollerWritesIt:
    def test_signal_type_is_in_the_signal_reports_payload(self) -> None:
        assert PAYLOAD_KEY in inspect.getsource(poll_job), (
            "the poller must write signal_type; the value is already in scope "
            "at the write site because it goes into the notification subject"
        )

    def test_it_is_written_from_the_variable_not_a_literal(self) -> None:
        """A hardcoded string would be worse than NULL: it would look
        authoritative and be wrong for every signal but one."""
        src = inspect.getsource(poll_job)
        assert PAYLOAD_KEY in src
        assert '"signal_type": "' not in src

    def test_it_sits_in_the_same_payload_as_event_id(self) -> None:
        """Both identify the event; storing them apart would invite one being
        written without the other.

        Measured on what lies *between* the two keys rather than on a fixed
        window, because comments sit between them and a window is brittle.
        """
        src = inspect.getsource(poll_job)
        i_type = src.index(PAYLOAD_KEY)
        # **The LAST `event_id` before it**, not the first in the module:
        # `poll.py` writes two tables carrying that key, and the earlier one
        # belongs to a different insert entirely. A naive `.index` finds that
        # one and reports a failure that is really the test's own bug.
        i_event = src.rindex('"event_id": event_id,', 0, i_type)
        between = src[i_event:i_type]
        assert "db_io.append" not in between, "they must be in the same insert"
        assert '"fired_at"' not in between, "and in the same dict"


class TestTheColumnIsNullable:
    def test_the_migration_does_not_backfill(self) -> None:
        """Existing rows genuinely do not know their signal type: `state_json`
        carries indicator state but no signal type, and the events that would
        have supplied it are the ones ADR 150 deleted. A NULL says "not
        recorded"; a guessed value would say something false."""
        mig = inspect.getsource(
            __import__(
                "db.migrations.versions.a4c8d19f6e02_signal_reports_signal_type",
                fromlist=["upgrade"],
            )
        )
        assert "ADD COLUMN IF NOT EXISTS signal_type text" in mig
        assert "UPDATE signal_reports" not in mig
