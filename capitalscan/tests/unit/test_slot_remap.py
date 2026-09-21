"""The inbound remap resolves `event_id` on the debounce slot, not the
label (design doc, 2026-09-20).

**Why this file exists.** `_apply_remap` (`test_sync_remap.py`) resolves a
natural key that already exists as columns on both sides:
`(config_hash, ticker, as_of/signal_date, signal_type, entry_kind)`. That
key assumes a Pi-born prediction and its research event were labelled the
same way. Measured 2026-09-20: of 338 poller-written events since
2026-09-08, **zero** matched research's natural key -- `breach_live` (the
Pi) has no close to confirm against and emits `bb_lower_touch`, while the
end-of-day pass sees the close inside the band and labels the same
debounce slot `bull_close_below_lower` (ADR 194). `_apply_slot_remap`
resolves `(config_hash, ticker, signal_date, side, entry_kind)` instead --
`side` derived from `signal_type` through `slot_side` -- which is stable
across that disagreement because both labels are `LONG_SIGNALS`.

The label-mismatch case is what these tests are mostly about, the same way
`test_sync_remap.py`'s wrong-link case was the point there.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd
import pytest
from sqlalchemy import Engine

from capitalscan.jobs.sync import _apply_slot_remap


def slot_remap(frame: pd.DataFrame, engine: Any) -> tuple[pd.DataFrame, int, int]:
    """`_apply_slot_remap` with the fake engine cast at one place."""
    return _apply_slot_remap(frame, cast(Engine, engine))


class _FakeEngine:
    """Answers the slot lookup from a frame, mirroring `test_sync_remap.py`'s
    `_FakeEngine` for `_apply_remap`.

    The real query filters on `config_hash`, `ticker`, `signal_date`,
    `entry_kind` only -- `side` is not a database column, so it is never
    part of the `WHERE`. The fixture mirrors exactly that filter.
    """

    def __init__(self, events: pd.DataFrame) -> None:
        self.events = events


@pytest.fixture
def patched_read_sql(monkeypatch):
    select_cols = ("config_hash", "ticker", "signal_date", "entry_kind")

    def fake_read_sql(sql, con, params=None):
        frame = con.events
        for i, col in enumerate(select_cols):
            frame = frame[frame[col].isin(params[f"v{i}"])]
        return frame[
            ["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind"]
        ].reset_index(drop=True)

    monkeypatch.setattr("capitalscan.jobs.sync.pd.read_sql", fake_read_sql)
    return fake_read_sql


def _events(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind"],
    )


def _predictions(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["id", "event_id", "config_hash", "ticker", "as_of", "signal_type", "entry_kind"],
    )


class TestALabelMismatchStillLinks:
    def test_bb_lower_touch_links_onto_a_bull_close_below_lower_event(
        self, patched_read_sql
    ) -> None:
        """The USB 2026-09-18 shape: the Pi's live read and the end-of-day
        pass fill the same slot with different labels, both LONG."""
        events = _events([(81001, "h1", "USB", "2026-09-18", "bull_close_below_lower", "touch")])
        predictions = _predictions(
            [(500, 999_999, "h1", "USB", "2026-09-18", "bb_lower_touch", "touch")]
        )
        out, no_slot, ambiguous = slot_remap(predictions, _FakeEngine(events))
        assert out.loc[0, "event_id"] == 81001
        assert no_slot == 0
        assert ambiguous == 0

    def test_the_short_side_links_the_same_way(self, patched_read_sql) -> None:
        events = _events([(2, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch")])
        predictions = _predictions([(10, 0, "h1", "KO", "2026-08-24", "bb_upper_touch", "touch")])
        out, no_slot, ambiguous = slot_remap(predictions, _FakeEngine(events))
        assert out.loc[0, "event_id"] == 2
        assert (no_slot, ambiguous) == (0, 0)

    def test_the_adopted_frames_signal_type_is_unchanged_by_resolution(
        self, patched_read_sql
    ) -> None:
        """Adoption relabels nothing -- the row keeps the live label the
        reader saw, even though it links to a differently-labelled event."""
        events = _events([(81001, "h1", "USB", "2026-09-18", "bull_close_below_lower", "touch")])
        predictions = _predictions([(500, 0, "h1", "USB", "2026-09-18", "bb_lower_touch", "touch")])
        out, _no_slot, _ambiguous = slot_remap(predictions, _FakeEngine(events))
        assert out.loc[0, "signal_type"] == "bb_lower_touch"

    def test_entry_kind_still_separates_the_two_grains(self, patched_read_sql) -> None:
        events = _events(
            [
                (1, "h1", "KO", "2026-08-24", "bear_close_above_upper", "next_open"),
                (2, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch"),
            ]
        )
        predictions = _predictions([(10, 0, "h1", "KO", "2026-08-24", "bb_upper_touch", "touch")])
        out, _n, _a = slot_remap(predictions, _FakeEngine(events))
        assert out.loc[0, "event_id"] == 2


class TestNoMatchingSlotIsNull:
    def test_no_event_at_all_gives_null_and_counts_no_slot(self, patched_read_sql) -> None:
        events = _events([])
        predictions = _predictions([(10, 555, "h1", "ZZ", "2026-09-19", "bb_lower_touch", "touch")])
        out, no_slot, ambiguous = slot_remap(predictions, _FakeEngine(events))
        assert pd.isna(out.loc[0, "event_id"])
        assert (no_slot, ambiguous) == (1, 0)

    def test_an_event_on_the_wrong_side_of_the_slot_does_not_match(self, patched_read_sql) -> None:
        """Same ticker, same day, but the only event present is the
        opposite side -- a real event exists yet the slot has none."""
        events = _events([(1, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch")])
        predictions = _predictions([(10, 0, "h1", "KO", "2026-08-24", "bb_lower_touch", "touch")])
        out, no_slot, ambiguous = slot_remap(predictions, _FakeEngine(events))
        assert pd.isna(out.loc[0, "event_id"])
        assert (no_slot, ambiguous) == (1, 0)


class TestTwoEventsInOneSlotIsNullNeverAPick:
    def test_two_events_share_a_slot_and_neither_is_picked(self, patched_read_sql) -> None:
        """Measured: 15 of 182,921 events since 2026-08-01 hold two events
        in the same slot, different `signal_type`, same side. ADR 191:
        picking one is a guess, and a confidently wrong link is worse than
        an absent one."""
        events = _events(
            [
                (1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (2, "h1", "AA", "2026-09-09", "confluence_low", "touch"),
            ]
        )
        predictions = _predictions([(10, 0, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        out, no_slot, ambiguous = slot_remap(predictions, _FakeEngine(events))
        assert pd.isna(out.loc[0, "event_id"])
        assert out.loc[0, "event_id"] != 1, "must never pick the first match"
        assert out.loc[0, "event_id"] != 2, "must never pick the second match"
        assert (no_slot, ambiguous) == (0, 1)

    def test_a_third_row_in_a_different_slot_is_unaffected(self, patched_read_sql) -> None:
        """The ambiguity is scoped to its own slot; a clean row elsewhere
        in the same pull still resolves."""
        events = _events(
            [
                (1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (2, "h1", "AA", "2026-09-09", "confluence_low", "touch"),
                (3, "h1", "BB", "2026-09-09", "bb_upper_touch", "touch"),
            ]
        )
        predictions = _predictions(
            [
                (10, 0, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch"),
                (11, 0, "h1", "BB", "2026-09-09", "bb_upper_touch", "touch"),
            ]
        )
        out, no_slot, ambiguous = slot_remap(predictions, _FakeEngine(events))
        out = out.set_index("id")
        assert pd.isna(out.loc[10, "event_id"])
        assert out.loc[11, "event_id"] == 3
        assert (no_slot, ambiguous) == (0, 1)


class TestSideDerivationRaisesRatherThanDefaulting:
    def test_an_unknown_signal_type_on_the_incoming_frame_raises(self) -> None:
        predictions = _predictions([(10, 0, "h1", "AA", "2026-09-09", "not_a_real_type", "touch")])
        with pytest.raises(ValueError, match="has no side"):
            slot_remap(predictions, _FakeEngine(_events([])))


class TestTheTableParameterIsAdditiveOnly:
    """`table` (task 5, real-Postgres verification) defaults to `"events"`
    so every real caller -- `_pull_predictions` and
    `scripts/backfill_prediction_event_ids.py`, both of which call this
    positionally with two arguments -- emits byte-for-byte the same SQL as
    before. `scripts/verify_slot_adoption.py` is the only caller that
    passes a non-default value, pointed at a `zz_` scratch table."""

    def test_the_default_call_selects_from_events(self, monkeypatch) -> None:
        captured: dict[str, str] = {}

        def fake_read_sql(sql, con, params=None):
            captured["text"] = str(sql)
            return _events([])

        monkeypatch.setattr("capitalscan.jobs.sync.pd.read_sql", fake_read_sql)
        predictions = _predictions([(10, 0, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        slot_remap(predictions, _FakeEngine(_events([])))
        assert 'FROM "events"' in captured["text"]

    def test_a_non_default_table_is_used_in_place_of_events(self, monkeypatch) -> None:
        captured: dict[str, str] = {}

        def fake_read_sql(sql, con, params=None):
            captured["text"] = str(sql)
            return _events([])

        monkeypatch.setattr("capitalscan.jobs.sync.pd.read_sql", fake_read_sql)
        predictions = _predictions([(10, 0, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        _apply_slot_remap(
            predictions, cast(Engine, _FakeEngine(_events([]))), table="zz_verify_events"
        )
        assert 'FROM "zz_verify_events"' in captured["text"]
        assert 'FROM "events"' not in captured["text"]


class TestItRefusesWhatItCannotDo:
    def test_a_frame_missing_the_natural_key_raises(self) -> None:
        frame = pd.DataFrame({"id": [1], "event_id": [2], "signal_type": ["bb_lower_touch"]})
        with pytest.raises(ValueError, match="widen the query's SELECT"):
            slot_remap(frame, _FakeEngine(_events([])))

    def test_an_empty_frame_is_returned_unchanged(self) -> None:
        out, no_slot, ambiguous = slot_remap(_predictions([]), _FakeEngine(_events([])))
        assert out.empty
        assert (no_slot, ambiguous) == (0, 0)


class TestTheFrameStaysWritable:
    def test_column_order_is_preserved_and_side_is_dropped(self, patched_read_sql) -> None:
        events = _events([(1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        predictions = _predictions([(10, 5, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        out, _n, _a = slot_remap(predictions, _FakeEngine(events))
        assert list(out.columns) == list(predictions.columns)
        assert "side" not in out.columns

    def test_no_rows_are_added_or_lost_on_a_clean_pull(self, patched_read_sql) -> None:
        events = _events([(1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        predictions = _predictions([(10, 5, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        out, _n, _a = slot_remap(predictions, _FakeEngine(events))
        assert len(out) == 1
