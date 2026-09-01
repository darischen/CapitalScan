"""`run_sync` must not overwrite a surrogate `events.id` (2026-09-01).

The 2026-09-01 nightly sync failed with

    duplicate key value violates unique constraint "events_pkey"
    DETAIL:  Key (id)=(61797210) already exists.

Two unrelated rows, one id: on serving 61797210 was ADM `bb_upper_touch`,
written by that day's poller; on research it was AA `stoch_oversold`,
written by that night's `run_events`.

**ADR 158 made it possible.** With the poller writing serving natively,
each store mints `events.id` from its own sequence, so the two allocate
independently out of one numeric range. Before that, serving was copy-only
and the id spaces could not diverge.

`db_io.upsert`'s default overwrites every non-key column -- correct for
data, wrong for a surrogate key the conflict clause does not name.
"""

import inspect

import pandas as pd

from capitalscan.jobs import sync as sync_job


class TestUpdateColumnsPreservingId:
    def test_it_drops_id_when_the_key_is_natural(self) -> None:
        frame = pd.DataFrame(columns=["id", "config_hash", "ticker", "net_ret"])
        key = ("config_hash", "ticker")

        cols = sync_job._update_columns_preserving_id(frame, key)

        assert cols == ["net_ret"], "id and the conflict columns must both be excluded"

    def test_it_defers_to_the_default_when_the_key_is_the_id(self) -> None:
        """`signal_reports`, `predictions` and `positions` conflict on `id`.

        A conflict column is never written by `db_io.upsert`, so there is
        nothing to exclude and narrowing the update set would only risk
        dropping a real column.
        """
        frame = pd.DataFrame(columns=["id", "ticker", "fired_at"])

        assert sync_job._update_columns_preserving_id(frame, ("id",)) is None

    def test_it_defers_to_the_default_when_there_is_no_id(self) -> None:
        frame = pd.DataFrame(columns=["ticker", "ts", "close"])

        assert sync_job._update_columns_preserving_id(frame, ("ticker", "ts")) is None

    def test_the_events_key_is_natural_so_events_is_covered(self) -> None:
        """Guards the premise rather than the mechanism.

        If `events` ever conflicts on `id`, the helper above correctly does
        nothing and this protection silently disappears.
        """
        tables = {t.name: t for t in sync_job._tables(__import__("datetime").date(2020, 1, 1), "h")}
        assert "id" not in tables["events"].key


class TestWiredIntoBothPushPaths:
    def test_run_sync_uses_it(self) -> None:
        assert "_update_columns_preserving_id" in inspect.getsource(sync_job.run_sync)

    def test_run_live_sync_uses_it(self) -> None:
        """The research poller's per-tick push writes serving too, and can
        collide identically."""
        assert "_update_columns_preserving_id" in inspect.getsource(sync_job.run_live_sync)


class TestPullDropsTheCrossStoreEventId:
    def test_event_id_is_nulled_on_arrival(self) -> None:
        """`signal_reports.event_id` names a row in the *source's* id space.

        Since ADR 158 the same integer is a different event on each side,
        so copying it produces a link that resolves and points at the wrong
        ticker. Nulling is what ADR 150's sweep already does to this column
        by design, and `v_screen_live` stopped joining on it in
        `d5e91a7c3b48` for the same reason.
        """
        src = inspect.getsource(sync_job.pull_live_records)
        assert "frame.assign(event_id=None)" in src
        assert 'name == "signal_reports"' in src

    def test_the_pull_resets_the_target_sequences(self) -> None:
        """An explicit-id INSERT does not advance a sequence.

        `run_sync` has reset serving's since 2026-08-28; the reverse
        direction was missed and research's `signal_reports_id_seq` drifted
        211 behind `max(id)`, which is what failed the 2026-08-31 fallback
        poll.
        """
        assert "_reset_sequences(target)" in inspect.getsource(sync_job.pull_live_records)

    def test_one_reset_implementation(self) -> None:
        """Both directions call the same helper rather than carrying a copy."""
        assert inspect.getsource(sync_job).count("_RESET_SEQUENCES_SQL") == 2
