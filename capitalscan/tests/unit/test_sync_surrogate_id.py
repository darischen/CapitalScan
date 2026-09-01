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

**The collision has two halves and the first fix only closed one.**
Excluding `id` from `DO UPDATE SET` stops an existing row's id being
overwritten. But a source row that is *new* to the target under the natural
key raises no conflict at all, so `ON CONFLICT` never fires and the INSERT
carries the source's id straight into the target's primary key. A verifying
re-run failed on the identical id, which is what proved the first fix
insufficient.

So the column is not shipped at all. `events.id` defaults to
`nextval('events_id_seq')` on both stores, so an omitted id is assigned by
the target and an update through the natural key leaves the existing one
alone.
"""

import inspect

import pandas as pd

from capitalscan.jobs import sync as sync_job


class TestDropSurrogateId:
    def test_the_id_is_not_shipped_when_the_key_is_natural(self) -> None:
        """Not merely excluded from the update -- absent from the INSERT.

        The target's `nextval` default then assigns its own, which is the
        only version that survives a row that is new to the target.
        """
        frame = pd.DataFrame({"id": [1], "config_hash": ["h"], "ticker": ["AA"], "net_ret": [0.1]})

        out = sync_job._drop_surrogate_id(frame, ("config_hash", "ticker"))

        assert "id" not in out.columns
        assert list(out.columns) == ["config_hash", "ticker", "net_ret"]

    def test_the_source_frame_is_not_mutated(self) -> None:
        """`run_live_sync` reads `frame["id"]` afterwards for its watermark,
        which tracks *source* ids and must survive the drop."""
        frame = pd.DataFrame({"id": [7], "config_hash": ["h"], "ticker": ["AA"]})

        sync_job._drop_surrogate_id(frame, ("config_hash", "ticker"))

        assert "id" in frame.columns, "the caller still needs the source id"

    def test_the_id_is_kept_when_it_is_the_conflict_key(self) -> None:
        """`signal_reports`, `predictions` and `positions` conflict on `id`.

        There the id is the identity the upsert resolves by, not a value
        being carried along, so dropping it would break the match.
        """
        frame = pd.DataFrame({"id": [1], "ticker": ["AA"], "fired_at": ["t"]})

        out = sync_job._drop_surrogate_id(frame, ("id",))

        assert "id" in out.columns

    def test_a_frame_without_an_id_is_returned_unchanged(self) -> None:
        frame = pd.DataFrame({"ticker": ["AA"], "ts": ["t"], "close": [1.0]})

        out = sync_job._drop_surrogate_id(frame, ("ticker", "ts"))

        assert list(out.columns) == ["ticker", "ts", "close"]

    def test_the_events_key_is_natural_so_events_is_covered(self) -> None:
        """Guards the premise rather than the mechanism.

        If `events` ever conflicts on `id`, the helper above correctly does
        nothing and this protection silently disappears.
        """
        tables = {t.name: t for t in sync_job._tables(__import__("datetime").date(2020, 1, 1), "h")}
        assert "id" not in tables["events"].key


class TestWiredIntoBothPushPaths:
    def test_run_sync_uses_it(self) -> None:
        assert "_drop_surrogate_id" in inspect.getsource(sync_job.run_sync)

    def test_run_live_sync_uses_it(self) -> None:
        """The research poller's per-tick push writes serving too, and can
        collide identically."""
        assert "_drop_surrogate_id" in inspect.getsource(sync_job.run_live_sync)


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
