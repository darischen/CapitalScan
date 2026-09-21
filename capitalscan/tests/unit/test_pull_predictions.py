"""`pull_live_records` adopts serving-born predictions (forward-log adoption,
2026-09-19), resolved on the debounce slot since 2026-09-20.

**Why this exists.** The Pi's poller scores signals live with
`predict --serving` and writes straight to serving's `predictions` table.
Research never sees that row, so when the forward log later resolves an
outcome it resolves against a number research computed after the fact
(nightly's own refit or scoring pass), not the number a reader actually saw.
`_pull_predictions` closes that gap: it is `pull_live_records`'s fourth step,
serving -> research, alongside `runs`, `signal_reports` and
`poller_sessions`.

**Not a `_LIVE_DURABLE_TABLES` entry**, on purpose: the other three are
scoped by a lookback window because a poller session belongs to one day. A
serving-born prediction's identity is which side of
`ServingParams.serving_id_floor` minted it (see that field and
`_reset_sequences`), so this step reads a floor and also needs
`_apply_slot_remap` to rewrite `event_id` into research's id space.

**Resolution is slot-keyed, not natural-key, since 2026-09-20.** The
label-mismatch case (`bb_lower_touch` on the Pi linking onto a
`bull_close_below_lower` research event) and the two-events-in-one-slot
ambiguity rule live in `test_slot_remap.py`, which tests
`_apply_slot_remap` directly. This file tests `_pull_predictions`'s own
concerns layered on top of that: floor scoping, the write-time collision
guard, and the wiring into `pull_live_records`. Every fixture below uses
the SAME `signal_type` on both the source prediction and the target event,
because the slot-vs-label distinction is not what these tests are about.

**Written with `insert_new` (`DO NOTHING`), not `copy_upsert`.** ADR 195's
insert-only rule for `predictions` applies to this adoption path too (whole-
branch-review finding IMPORTANT 2) -- a rewrite after `outcomes` resolves
turns evidence into a fitted number. The fixtures below patch
`db_io.insert_new`, not `copy_upsert`.

No real database anywhere in this file. `_apply_slot_remap`'s and
`_null_inbound_remap_collisions`'s Engine calls (`pd.read_sql` lookups) and
`db_io.insert_new`'s write are all faked, following the pattern
`test_sync_remap.py` established for `_apply_remap`.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd
import pytest
from sqlalchemy import Engine

from capitalscan.core.config import ServingParams
from capitalscan.jobs import sync as sync_job
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH

CHASH = DEFAULT_CONFIG_HASH
FLOOR = ServingParams().serving_id_floor


class _FakeSourceEngine:
    """Answers the floor-scoped SELECT from a frame held in memory."""

    def __init__(self, predictions: pd.DataFrame) -> None:
        self.predictions = predictions


class _FakeTargetEngine:
    """Answers `_apply_slot_remap`'s lookup and `_null_inbound_remap_collisions`'s
    collision check from frames held in memory.

    `predictions` is what research already holds -- empty by default, so
    every existing test that does not care about collisions is unaffected.
    `connect()` returns `self` so the collision check's
    `with target.connect() as conn:` works the same way `target` itself
    does for `_apply_slot_remap`'s direct `pd.read_sql(..., target, ...)`
    call.
    """

    def __init__(self, events: pd.DataFrame, predictions: pd.DataFrame | None = None) -> None:
        self.events = events
        self.predictions = (
            predictions if predictions is not None else pd.DataFrame(columns=["id", "event_id"])
        )

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _predictions(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["id", "event_id", "config_hash", "ticker", "as_of", "signal_type", "entry_kind"],
    )


def _events(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind"],
    )


@pytest.fixture
def insert_new_calls(monkeypatch):
    """Record what `_pull_predictions` writes, instead of hitting Postgres."""
    calls: list[dict[str, Any]] = []

    def fake_insert_new(engine, table_name, data, conflict_cols):
        frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        calls.append({"table": table_name, "key": list(conflict_cols), "frame": frame.copy()})
        return len(frame)

    monkeypatch.setattr(sync_job.db_io, "insert_new", fake_insert_new)
    return calls


# The slot lookup's WHERE binds exactly these four columns -- `side` is not
# a database column (`_apply_slot_remap`'s own comment says why), so it is
# never part of the query the fixture below has to answer.
_SLOT_SELECT_COLS = ("config_hash", "ticker", "signal_date", "entry_kind")


@pytest.fixture
def patched_read_sql(monkeypatch):
    """Route every one of `_pull_predictions`'s reads -- the select, the
    slot lookup, and the inbound collision check -- through the fake
    engines, distinguished the way the real calls are: the select binds
    `:floor`, the slot lookup binds `:v0`, ..., the collision check binds
    `:claimed`.

    Every SQL statement's text is also recorded, in call order, onto
    `.statements` -- Task 5a: a test asserting on that text catches a
    dropped `WHERE` clause that a fake filtering the frame itself would
    hide (the earlier version of this fixture applied `id >= floor` in
    Python, so the selection test would have passed with the SQL clause
    deleted from the real query).
    """
    statements: list[str] = []

    def fake_read_sql(sql, con, params=None):
        statements.append(str(sql))
        params = params or {}
        if "floor" in params:
            assert isinstance(con, _FakeSourceEngine)
            frame = con.predictions
            return frame[frame["id"] >= params["floor"]].reset_index(drop=True)

        if "claimed" in params:
            assert isinstance(con, _FakeTargetEngine)
            frame = con.predictions
            if frame.empty:
                return pd.DataFrame(columns=["__owner_id", "__val"])
            matched = frame[frame["event_id"].isin(params["claimed"])]
            return matched.rename(columns={"id": "__owner_id", "event_id": "__val"})[
                ["__owner_id", "__val"]
            ].reset_index(drop=True)

        assert isinstance(con, _FakeTargetEngine)
        frame = con.events
        for i, col in enumerate(_SLOT_SELECT_COLS):
            frame = frame[frame[col].isin(params[f"v{i}"])]
        cols = ["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind"]
        return frame[cols].reset_index(drop=True)

    monkeypatch.setattr("capitalscan.jobs.sync.pd.read_sql", fake_read_sql)
    fake_read_sql.statements = statements
    return fake_read_sql


def _pull(
    source_predictions: pd.DataFrame,
    target_events: pd.DataFrame,
    target_predictions: pd.DataFrame | None = None,
) -> tuple[int, int, int, int]:
    source = cast(Engine, _FakeSourceEngine(source_predictions))
    target = cast(Engine, _FakeTargetEngine(target_events, target_predictions))
    return sync_job._pull_predictions(source, target)


class TestSelectionIsFloorScoped:
    def test_below_floor_rows_are_not_selected(self, patched_read_sql, insert_new_calls):
        """Research-born predictions stay below the floor (`_reset_sequences`);
        the pull must not try to adopt research's own rows back into itself.
        """
        below = _predictions([(FLOOR - 1, 1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")])
        _pull(below, _events([(1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")]))
        assert insert_new_calls == [], "a below-floor row was adopted"

    def test_at_and_above_the_floor_rows_are_selected(self, patched_read_sql, insert_new_calls):
        rows = _predictions(
            [
                (FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch"),
                (FLOOR + 1, 2, CHASH, "BB", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        events = _events(
            [
                (1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch"),
                (2, CHASH, "BB", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        adopted, no_slot, ambiguous, unmapped = _pull(rows, events)
        assert adopted == 2
        assert (no_slot, ambiguous, unmapped) == (0, 0, 0)
        assert len(insert_new_calls[0]["frame"]) == 2

    def test_an_empty_selection_writes_nothing(self, patched_read_sql, insert_new_calls):
        below = _predictions([(FLOOR - 5, 1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")])
        adopted, no_slot, ambiguous, unmapped = _pull(below, _events([]))
        assert (adopted, no_slot, ambiguous, unmapped) == (0, 0, 0, 0)
        assert insert_new_calls == [], "an empty frame must not reach insert_new"

    def test_the_select_clause_itself_is_floor_scoped(self, patched_read_sql):
        """Task 5a: the earlier version of this test only proved that
        rows below the floor were dropped, and `fake_read_sql` applied
        that filter itself -- so the assertion would still pass with
        `WHERE id >= :floor` deleted from the real query. Assert on the
        emitted SQL text directly, which only the real clause can satisfy.
        """
        below = _predictions([(FLOOR - 1, 1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")])
        _pull(below, _events([]))
        select_statements = [s for s in patched_read_sql.statements if "FROM predictions" in s]
        assert select_statements, "no SELECT against predictions was issued"
        assert "id >= :floor" in select_statements[0]


class TestTheRemapResolvesThroughTheDebounceSlot:
    """Same `signal_type` on both sides throughout -- the label-mismatch
    case (a live label linking onto a differently-labelled research event)
    is `test_slot_remap.py`'s job. What these tests pin is that
    `_pull_predictions` reaches the slot lookup at all and rewrites
    `event_id` into research's id space."""

    def test_event_id_is_rewritten_into_researchs_id_space(
        self, patched_read_sql, insert_new_calls
    ):
        """The Pi's `event_id` names a row in serving's id space. Research
        must see its own id for the same signal, resolved through the
        debounce slot `(config_hash, ticker, signal_date, side,
        entry_kind)`."""
        source = _predictions(
            [(FLOOR, 987_654, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        _pull(source, target_events)
        written = insert_new_calls[0]["frame"]
        assert written.loc[0, "event_id"] == 42
        assert written.loc[0, "event_id"] != 987_654, "the serving-side id survived the pull"

    def test_entry_kind_separates_the_two_grains(self, patched_read_sql, insert_new_calls):
        source = _predictions(
            [(FLOOR, 0, CHASH, "KO", "2026-08-24", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [
                (55964059, CHASH, "KO", "2026-08-24", "bear_close_above_upper", "next_open"),
                (55964060, CHASH, "KO", "2026-08-24", "bear_close_above_upper", "touch"),
            ]
        )
        _pull(source, target_events)
        written = insert_new_calls[0]["frame"]
        assert written.loc[0, "event_id"] == 55964060


class TestAnUnmatchedKeyIsNullNotTheSourceId:
    def test_no_matching_event_yields_null(self, patched_read_sql, insert_new_calls):
        """No event shares the slot at all -- a provisional poller event
        the ADR 150 sweep already removed, for instance: the prediction
        still adopts, but `event_id` is honest about having nothing to
        point at, and it counts as `no_slot`, not `ambiguous`."""
        source = _predictions([(FLOOR, 555, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch")])
        adopted, no_slot, ambiguous, unmapped = _pull(source, _events([]))
        assert adopted == 1
        assert (no_slot, ambiguous, unmapped) == (1, 0, 1)
        written = insert_new_calls[0]["frame"]
        assert pd.isna(written.loc[0, "event_id"])
        assert written.loc[0, "event_id"] != 555, "the source id must not survive a miss"

    def test_no_slot_count_is_scoped_to_the_misses_only(self, patched_read_sql, insert_new_calls):
        source = _predictions(
            [
                (FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch"),
                (FLOOR + 1, 2, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        target_events = _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        adopted, no_slot, ambiguous, unmapped = _pull(source, target_events)
        assert adopted == 2
        assert (no_slot, ambiguous, unmapped) == (1, 0, 1)


class TestACollisionWithADifferentResearchRowIsNulledNotRaised:
    """CRITICAL 1: `predictions_event_id` is UNIQUE on both stores. If
    research already holds its own row for the resolved event under a
    different `id` -- a prior pull that failed non-fatally followed by
    that night's `predict`, or `weekly`'s refit, which calls `run_predict`
    with no pull ahead of it -- an unguarded write raises, and because
    selection is floor-scoped with no date bound, the same row would raise
    every night forever.

    A write-time collision is neither `no_slot` nor `ambiguous`: the slot
    resolved to exactly one event, and something else already claimed that
    link. It shows up as a nulled `event_id` with both counts at zero --
    distinct from the two the design doc names, and logged separately by
    `_pull_predictions` rather than folded into either bucket. `unmapped`
    (the fourth return value, the ground truth read from the frame after
    every nulling step) DOES include it -- that is the point of computing
    it from the frame rather than summing `no_slot` and `ambiguous`."""

    def test_a_different_owner_gets_nulled_not_raised(self, patched_read_sql, insert_new_calls):
        source = _predictions(
            [(FLOOR, 42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        # Research already scored this event under its OWN id (999, well
        # below the floor) -- exactly the shape a failed pull followed by
        # that night's `predict`, or a `weekly` refit with no pull ahead
        # of it, produces.
        target_predictions = pd.DataFrame([{"id": 999, "event_id": 42}])
        adopted, no_slot, ambiguous, unmapped = _pull(source, target_events, target_predictions)
        assert adopted == 1, "the row still adopts -- it is evidence, not discarded"
        assert (no_slot, ambiguous) == (0, 0), (
            "the slot resolved cleanly; the collision is separate"
        )
        assert unmapped == 1, "the ground-truth count still catches the nulled row"
        written = insert_new_calls[0]["frame"]
        assert pd.isna(written.loc[0, "event_id"]), "must not overwrite research's own row"

    def test_re_adopting_the_same_row_is_not_a_collision(self, patched_read_sql, insert_new_calls):
        """A repeated pull sends the same `id` again. When that `id` is
        the one already holding the `event_id` on the target, it must not
        be treated as a collision with itself."""
        source = _predictions(
            [(FLOOR, 42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        # The row research already holds under event_id=42 is THIS SAME
        # adopted row (id=FLOOR), from a previous pull.
        target_predictions = pd.DataFrame([{"id": FLOOR, "event_id": 42}])
        adopted, no_slot, ambiguous, unmapped = _pull(source, target_events, target_predictions)
        assert (no_slot, ambiguous, unmapped) == (0, 0, 0)
        written = insert_new_calls[0]["frame"]
        assert written.loc[0, "event_id"] == 42, "adopting itself again must not null the link"

    def test_research_row_is_neither_deleted_nor_rewritten(
        self, patched_read_sql, insert_new_calls
    ):
        """Unlike `_clear_remap_collisions` (outbound), the inbound path
        must never touch the target's existing row -- `outcomes.
        prediction_id` may reference it. Only the incoming frame changes."""
        source = _predictions(
            [(FLOOR, 42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_predictions = pd.DataFrame([{"id": 999, "event_id": 42}])
        _pull(source, target_events, target_predictions)
        # The write only ever carries the incoming (adopted) frame, whose
        # own `id` is FLOOR -- research's id=999 row is never part of it.
        written = insert_new_calls[0]["frame"]
        assert list(written["id"]) == [FLOOR]


class TestTwoIncomingRowsResolvingToOneEventAreBothNulled:
    """CRITICAL, found in review 2026-09-20. The slot is coarser than the
    natural key it replaced: two serving events can share one debounce
    slot under different labels (design doc, "Ambiguity" -- 15 slots in
    research), and each serving event carries its OWN prediction, because
    `predictions_event_id` is unique per *serving* event, not per slot.
    Both predictions then resolve through `_apply_slot_remap` to the SAME
    research event.

    Left unguarded, both rows would reach `insert_new` carrying the same
    `event_id`. `ON CONFLICT (id) DO NOTHING` covers `id`, not
    `predictions_event_id` (also UNIQUE), so the write raises -- and
    because selection is floor-scoped with no date bound, it raises on
    the same two rows every night forever, exactly the failure shape this
    module's docstrings warn about elsewhere. `cli.nightly`'s broad
    `except Exception` would swallow it, leaving adoption silently dead."""

    def test_both_rows_land_null_and_neither_is_picked(self, patched_read_sql, insert_new_calls):
        # bb_lower_touch and confluence_low are both LONG_SIGNALS (same
        # side), same ticker/date/entry_kind -- the same debounce slot,
        # filled by two different serving-born predictions.
        source = _predictions(
            [
                (FLOOR, 100, CHASH, "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (FLOOR + 1, 200, CHASH, "AA", "2026-09-09", "confluence_low", "touch"),
            ]
        )
        # Research holds exactly one event for that slot.
        target_events = _events([(1, CHASH, "AA", "2026-09-09", "bb_lower_touch", "touch")])
        adopted, no_slot, ambiguous, unmapped = _pull(source, target_events)
        assert adopted == 2, "both rows still adopt -- they are evidence, not discarded"
        assert (no_slot, ambiguous) == (0, 0), "the slot itself resolved to exactly one event"
        assert unmapped == 2, "both rows, not one, must be counted"
        written = insert_new_calls[0]["frame"].set_index("id")
        assert pd.isna(written.loc[FLOOR, "event_id"])
        assert pd.isna(written.loc[FLOOR + 1, "event_id"])
        assert not (written["event_id"] == 1).any(), "neither row may keep the shared id"

    def test_a_third_row_in_a_different_slot_is_unaffected(
        self, patched_read_sql, insert_new_calls
    ):
        source = _predictions(
            [
                (FLOOR, 100, CHASH, "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (FLOOR + 1, 200, CHASH, "AA", "2026-09-09", "confluence_low", "touch"),
                (FLOOR + 2, 300, CHASH, "BB", "2026-09-09", "bb_upper_touch", "touch"),
            ]
        )
        target_events = _events(
            [
                (1, CHASH, "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (2, CHASH, "BB", "2026-09-09", "bb_upper_touch", "touch"),
            ]
        )
        adopted, no_slot, ambiguous, unmapped = _pull(source, target_events)
        assert adopted == 3
        assert unmapped == 2, "only the colliding pair is nulled"
        written = insert_new_calls[0]["frame"].set_index("id")
        assert written.loc[FLOOR + 2, "event_id"] == 2, "the clean row resolves normally"

    def test_one_row_already_adopted_last_night_keeps_its_link(
        self, patched_read_sql, insert_new_calls
    ):
        """Order, found wrong in round 1 of the fix (review round 2,
        2026-09-20). Row A (id=FLOOR) was adopted on a PRIOR pull and
        research already holds it with `event_id=1` -- a legitimate,
        permanent owner. This pull re-sends A (a repeated pull, same id)
        alongside a NEW row B that resolves to the same slot, so the
        incoming frame holds two rows both claiming `event_id=1`.

        Deduping before the collision check would null BOTH in the frame,
        so the collision check would have nothing left to compare A
        against and `unmapped` would wrongly count 2. Running the
        collision check first excludes A as its own owner, nulls B
        against A, and leaves the dedup step nothing to do: A must keep
        `event_id=1`, only B lands NULL, and `unmapped` must be 1."""
        source = _predictions(
            [
                (FLOOR, 100, CHASH, "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (FLOOR + 1, 200, CHASH, "AA", "2026-09-09", "confluence_low", "touch"),
            ]
        )
        target_events = _events([(1, CHASH, "AA", "2026-09-09", "bb_lower_touch", "touch")])
        target_predictions = pd.DataFrame([{"id": FLOOR, "event_id": 1}])
        adopted, no_slot, ambiguous, unmapped = _pull(source, target_events, target_predictions)
        assert adopted == 2
        assert (no_slot, ambiguous) == (0, 0)
        assert unmapped == 1, "only B is actually NULL -- A's prior link is untouched"
        written = insert_new_calls[0]["frame"].set_index("id")
        assert written.loc[FLOOR, "event_id"] == 1, "A's own link must survive re-adoption"
        assert pd.isna(written.loc[FLOOR + 1, "event_id"]), "B has nothing left to claim"


class TestTheWriteIsKeyedOnId:
    def test_insert_new_conflicts_on_id_alone(self, patched_read_sql, insert_new_calls):
        """Keying on `event_id` would be wrong: NULLs are distinct in a
        unique index, so every unmapped row would duplicate on every
        nightly pull. `id` is the one column serving and research now agree
        names the same row -- that agreement is the whole point of the
        floor split -- so it is what makes a repeated pull a no-op."""
        source = _predictions([(FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        _pull(source, _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")]))
        assert insert_new_calls[0]["table"] == "predictions"
        assert insert_new_calls[0]["key"] == ["id"]

    def test_a_second_pull_over_the_same_rows_sends_the_same_ids(
        self, patched_read_sql, insert_new_calls
    ):
        """Unit-level proxy for idempotency: `insert_new`'s own `ON
        CONFLICT (id) DO NOTHING` is what makes the second call insert
        nothing at the database layer. What this step owns is sending the
        *same* identity both times, so that guarantee actually applies --
        a step that re-keyed or dropped `id` between runs would defeat it
        even with a correct `insert_new`.
        """
        source = _predictions([(FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        events = _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        _pull(source, events)
        _pull(source, events)
        assert len(insert_new_calls) == 2
        first_ids = insert_new_calls[0]["frame"]["id"].tolist()
        second_ids = insert_new_calls[1]["frame"]["id"].tolist()
        assert first_ids == second_ids == [FLOOR]


class TestAnAdoptedRowSurvivesAFollowingRunPredict:
    """Design doc Testing item 7. `run_predict` writes with
    `db_io.insert_new(conflict_cols=["event_id"])` (ADR 195) -- the same
    insert-only primitive `_pull_predictions` now uses keyed on `id`
    (IMPORTANT 2). This exercises the two writes back to back against one
    in-memory store that implements real `ON CONFLICT ... DO NOTHING`
    semantics, so it proves the adopted row's *values* survive a `predict`
    run that reaches the same event, not merely that a mock was called.
    """

    class _ConflictAwareStore:
        """A minimal stand-in for a Postgres table with two independently
        unique columns (`id`, `event_id`) and `INSERT ... ON CONFLICT DO
        NOTHING`, keyed by whichever column the caller names."""

        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        def insert_new(self, engine, table_name, data, conflict_cols) -> int:
            assert len(conflict_cols) == 1
            key = conflict_cols[0]
            incoming = data if isinstance(data, list) else data.to_dict("records")
            existing_keys = {row[key] for row in self.rows if row.get(key) is not None}
            inserted = 0
            for row in incoming:
                if row.get(key) in existing_keys:
                    continue
                self.rows.append(dict(row))
                existing_keys.add(row.get(key))
                inserted += 1
            return inserted

    def test_the_adopted_rows_values_are_unchanged_after_a_later_predict_write(
        self, patched_read_sql, monkeypatch
    ):
        store = self._ConflictAwareStore()
        monkeypatch.setattr(sync_job.db_io, "insert_new", store.insert_new)

        adopted_id = FLOOR + 7
        source = _predictions(
            [(adopted_id, 42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        adopted, _no_slot, _ambiguous, _unmapped = sync_job._pull_predictions(
            cast(Engine, _FakeSourceEngine(source)),
            cast(Engine, _FakeTargetEngine(target_events)),
        )
        assert adopted == 1
        assert len(store.rows) == 1
        assert store.rows[0]["id"] == adopted_id
        assert store.rows[0]["event_id"] == 42
        assert store.rows[0]["ticker"] == "KO"

        # `run_predict`'s own write, for the same event, with a DIFFERENT
        # id and different feature values -- the shape of a `weekly` refit
        # or nightly `predict` reaching an event the Pi already scored.
        refit_written = store.insert_new(
            None,
            "predictions",
            [{"id": 999_001, "event_id": 42, "config_hash": CHASH, "ticker": "KO"}],
            conflict_cols=["event_id"],
        )
        assert refit_written == 0, "the event already has a row; predict must insert nothing"
        assert len(store.rows) == 1
        assert store.rows[0]["id"] == adopted_id, "the adopted row's identity is unchanged"


class TestItIsWiredIntoThePull:
    def test_pull_live_records_reports_predictions_and_unmapped(self, monkeypatch):
        """`pull_live_records` itself, with everything below it faked, so
        this pins the wiring rather than re-testing `_pull_predictions`.
        `predictions_unmapped` is the fourth value `_pull_predictions`
        returns directly (the ground truth), not a sum computed here --
        the fake below returns `unmapped=2` while `no_slot + ambiguous`
        is only 1, so a wiring bug that summed instead of passing through
        would be caught."""
        monkeypatch.setattr(sync_job, "_pull_predictions", lambda source, target: (3, 1, 0, 2))
        monkeypatch.setattr(
            sync_job.pd,
            "read_sql",
            lambda *a, **k: pd.DataFrame(),
        )
        monkeypatch.setattr(sync_job.db_io, "copy_upsert", lambda *a, **k: 0)
        monkeypatch.setattr(sync_job, "_reset_sequences", lambda *a, **k: None)

        out = sync_job.pull_live_records(
            source=cast(Engine, object()), target=cast(Engine, object())
        )
        assert out["predictions"] == 3
        assert out["predictions_unmapped"] == 2

    def test_the_existing_three_tables_are_unaffected(self):
        """The brief's constraint: their behaviour and order are unchanged.
        `predictions` must not appear among them."""
        names = [name for name, _predicate, _key in sync_job._LIVE_DURABLE_TABLES]
        assert names == ["runs", "signal_reports", "poller_sessions"]
        assert "predictions" not in names

    def test_the_sequence_reset_runs_even_when_the_predictions_step_raises(self, monkeypatch):
        """IMPORTANT 3: a raise inside `_pull_predictions` must not skip
        `_reset_sequences`, and must not be swallowed either -- the caller
        (`cli.nightly`) is the one that decides a pull failure is
        non-fatal; this function must not make that decision silently."""
        monkeypatch.setattr(
            sync_job.pd,
            "read_sql",
            lambda *a, **k: pd.DataFrame(),
        )
        monkeypatch.setattr(sync_job.db_io, "copy_upsert", lambda *a, **k: 0)

        def boom(source, target):
            raise RuntimeError("adoption blew up")

        monkeypatch.setattr(sync_job, "_pull_predictions", boom)
        reset_calls: list[Any] = []
        monkeypatch.setattr(sync_job, "_reset_sequences", lambda *a, **k: reset_calls.append(a))

        try:
            sync_job.pull_live_records(source=cast(Engine, object()), target=cast(Engine, object()))
        except RuntimeError as exc:
            assert "adoption blew up" in str(exc)
        else:
            raise AssertionError("expected the RuntimeError to propagate")
        assert reset_calls, "the sequence reset must still run despite the raise"
