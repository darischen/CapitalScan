"""`pull_live_records` adopts serving-born predictions (forward-log adoption,
2026-09-19).

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
`_apply_remap` to rewrite `event_id` into research's id space -- ADR 191's
remap, run in the direction it was not originally built for.

No real database anywhere in this file. `_apply_remap`'s one call of an
Engine (a `pd.read_sql` lookup) and `db_io.copy_upsert`'s write are both
faked, following the pattern `test_sync_remap.py` already established for
`_apply_remap`.
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
    """Answers `_apply_remap`'s lookup from a frame held in memory."""

    def __init__(self, events: pd.DataFrame) -> None:
        self.events = events


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
def copy_upsert_calls(monkeypatch):
    """Record what `_pull_predictions` writes, instead of hitting Postgres."""
    calls: list[dict[str, Any]] = []

    def fake_copy_upsert(engine, table_name, frame, key, update_columns=None):
        calls.append({"table": table_name, "key": list(key), "frame": frame.copy()})
        return len(frame)

    monkeypatch.setattr(sync_job.db_io, "copy_upsert", fake_copy_upsert)
    return calls


@pytest.fixture
def patched_read_sql(monkeypatch):
    """Route both of `_pull_predictions`'s reads (the select and the remap
    lookup) through the two fake engines, distinguished the way the real
    calls are: the select binds `:floor`, the remap lookup binds `:v0`, ...
    """

    def fake_read_sql(sql, con, params=None):
        params = params or {}
        if "floor" in params:
            assert isinstance(con, _FakeSourceEngine)
            frame = con.predictions
            return frame[frame["id"] >= params["floor"]].reset_index(drop=True)

        assert isinstance(con, _FakeTargetEngine)
        frame = con.events
        for i, col in enumerate(sync_job._PREDICTIONS_EVENT_REMAP.target_key):
            frame = frame[frame[col].isin(params[f"v{i}"])]
        cols = [*sync_job._PREDICTIONS_EVENT_REMAP.target_key, "id"]
        return frame[cols].reset_index(drop=True)

    monkeypatch.setattr("capitalscan.jobs.sync.pd.read_sql", fake_read_sql)
    return fake_read_sql


def _pull(source_predictions: pd.DataFrame, target_events: pd.DataFrame) -> tuple[int, int]:
    source = cast(Engine, _FakeSourceEngine(source_predictions))
    target = cast(Engine, _FakeTargetEngine(target_events))
    return sync_job._pull_predictions(source, target)


class TestSelectionIsFloorScoped:
    def test_below_floor_rows_are_not_selected(self, patched_read_sql, copy_upsert_calls):
        """Research-born predictions stay below the floor (`_reset_sequences`);
        the pull must not try to adopt research's own rows back into itself.
        """
        below = _predictions([(FLOOR - 1, 1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")])
        _pull(below, _events([(1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")]))
        assert copy_upsert_calls == [], "a below-floor row was adopted"

    def test_at_and_above_the_floor_rows_are_selected(self, patched_read_sql, copy_upsert_calls):
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
        adopted, unmapped = _pull(rows, events)
        assert adopted == 2
        assert unmapped == 0
        assert len(copy_upsert_calls[0]["frame"]) == 2

    def test_an_empty_selection_writes_nothing(self, patched_read_sql, copy_upsert_calls):
        below = _predictions([(FLOOR - 5, 1, CHASH, "AA", "2026-09-18", "bb_upper_touch", "touch")])
        adopted, unmapped = _pull(below, _events([]))
        assert (adopted, unmapped) == (0, 0)
        assert copy_upsert_calls == [], "an empty frame must not reach copy_upsert"


class TestTheRemapResolvesThroughTheEventsNaturalKey:
    def test_event_id_is_rewritten_into_researchs_id_space(
        self, patched_read_sql, copy_upsert_calls
    ):
        """The Pi's `event_id` names a row in serving's id space. Research
        must see its own id for the same signal, resolved through
        `(config_hash, ticker, signal_date, signal_type, entry_kind)`."""
        source = _predictions(
            [(FLOOR, 987_654, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        target_events = _events(
            [(42, CHASH, "KO", "2026-09-19", "bear_close_above_upper", "touch")]
        )
        _pull(source, target_events)
        written = copy_upsert_calls[0]["frame"]
        assert written.loc[0, "event_id"] == 42
        assert written.loc[0, "event_id"] != 987_654, "the serving-side id survived the pull"

    def test_entry_kind_separates_the_two_grains(self, patched_read_sql, copy_upsert_calls):
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
        written = copy_upsert_calls[0]["frame"]
        assert written.loc[0, "event_id"] == 55964060


class TestAnUnmatchedKeyIsNullNotTheSourceId:
    def test_no_matching_event_yields_null(self, patched_read_sql, copy_upsert_calls):
        """A provisional poller event the ADR 150 sweep already removed:
        the prediction still adopts, but `event_id` is honest about having
        nothing to point at."""
        source = _predictions([(FLOOR, 555, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch")])
        adopted, unmapped = _pull(source, _events([]))
        assert adopted == 1
        assert unmapped == 1
        written = copy_upsert_calls[0]["frame"]
        assert pd.isna(written.loc[0, "event_id"])
        assert written.loc[0, "event_id"] != 555, "the source id must not survive a miss"

    def test_unmapped_count_is_scoped_to_the_misses_only(self, patched_read_sql, copy_upsert_calls):
        source = _predictions(
            [
                (FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch"),
                (FLOOR + 1, 2, CHASH, "ZZ", "2026-09-19", "bb_lower_touch", "touch"),
            ]
        )
        target_events = _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        adopted, unmapped = _pull(source, target_events)
        assert adopted == 2
        assert unmapped == 1


class TestTheWriteIsKeyedOnId:
    def test_copy_upsert_conflicts_on_id_alone(self, patched_read_sql, copy_upsert_calls):
        """Keying on `event_id` would be wrong: NULLs are distinct in a
        unique index, so every unmapped row would duplicate on every
        nightly pull. `id` is the one column serving and research now agree
        names the same row -- that agreement is the whole point of the
        floor split -- so it is what makes a repeated pull a no-op."""
        source = _predictions([(FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        _pull(source, _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")]))
        assert copy_upsert_calls[0]["table"] == "predictions"
        assert copy_upsert_calls[0]["key"] == ["id"]

    def test_a_second_pull_over_the_same_rows_sends_the_same_ids(
        self, patched_read_sql, copy_upsert_calls
    ):
        """Unit-level proxy for idempotency: `copy_upsert`'s own `ON
        CONFLICT (id) DO UPDATE` is what makes the second call a no-op at
        the database layer (covered where `copy_upsert` itself is tested).
        What this step owns is sending the *same* identity both times, so
        that guarantee actually applies -- a step that re-keyed or dropped
        `id` between runs would defeat it even with a correct `copy_upsert`.
        """
        source = _predictions([(FLOOR, 1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        events = _events([(1, CHASH, "AA", "2026-09-19", "bb_upper_touch", "touch")])
        _pull(source, events)
        _pull(source, events)
        assert len(copy_upsert_calls) == 2
        first_ids = copy_upsert_calls[0]["frame"]["id"].tolist()
        second_ids = copy_upsert_calls[1]["frame"]["id"].tolist()
        assert first_ids == second_ids == [FLOOR]


class TestItIsWiredIntoThePull:
    def test_pull_live_records_reports_predictions_and_unmapped(self, monkeypatch):
        """`pull_live_records` itself, with everything below it faked, so
        this pins the wiring rather than re-testing `_pull_predictions`."""
        monkeypatch.setattr(sync_job, "_pull_predictions", lambda source, target: (3, 1))
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
        assert out["predictions_unmapped"] == 1

    def test_the_existing_three_tables_are_unaffected(self):
        """The brief's constraint: their behaviour and order are unchanged.
        `predictions` must not appear among them."""
        names = [name for name, _predicate, _key in sync_job._LIVE_DURABLE_TABLES]
        assert names == ["runs", "signal_reports", "poller_sessions"]
        assert "predictions" not in names
