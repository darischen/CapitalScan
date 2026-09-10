"""A surrogate reference is rewritten into the target's id space.

**This file exists because copying it produced links that resolved and were
wrong.** `events` syncs on a natural tuple, so serving mints `events.id`
from its own sequence; research mints from its own. A `predictions.event_id`
copied verbatim names whatever row happens to hold that integer on the other
side.

Measured on serving 2026-09-09, before the remap: of 20,200 predictions,
7,403 pointed at no event and **3,035 pointed at the wrong one** -- PRGO's
2026-08-05 prediction at an SMTC event from 2020-07-13, NRG's at PKX from
2018.

The wrong-link case is what these tests are mostly about. A dangling id is
findable with one outer join; an id that resolves to a real row of the wrong
ticker is invisible to every check that asks "does it join".
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd
import pytest
from sqlalchemy import Engine

from capitalscan.jobs.sync import Remap, _apply_remap


def remap(frame: pd.DataFrame, engine: Any, spec: Remap) -> pd.DataFrame:
    """`_apply_remap` with the fake engine cast at one place.

    The fake answers the one call `_apply_remap` makes of an Engine — the
    lookup, which the fixture routes through `pd.read_sql`. Casting here
    rather than widening the production signature: the function should
    keep demanding a real Engine.
    """
    return _apply_remap(frame, cast(Engine, engine), spec)


EVENT_REMAP = Remap(
    column="event_id",
    table="events",
    source_key=("config_hash", "ticker", "as_of", "signal_type", "entry_kind"),
    target_key=("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
    unique_on_target=True,
)


class _FakeEngine:
    """Answers the lookup from a frame, recording what was asked.

    A real engine would need a database; the behaviour under test is the
    key matching and the NULL-on-miss rule, neither of which is about SQL.
    """

    def __init__(self, events: pd.DataFrame) -> None:
        self.events = events
        self.calls: list[dict[str, list]] = []


@pytest.fixture
def patched_read_sql(monkeypatch):
    """Route `_apply_remap`'s read through the fake engine's frame."""

    def fake_read_sql(sql, con, params=None):
        con.calls.append(dict(params or {}))
        frame = con.events
        # Mirror the ANY() superset filter the real query applies.
        for i, col in enumerate(EVENT_REMAP.target_key):
            frame = frame[frame[col].isin(params[f"v{i}"])]
        return frame[[*EVENT_REMAP.target_key, "id"]].reset_index(drop=True)

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


class TestItRewritesIntoTheTargetsIdSpace:
    def test_the_id_becomes_the_targets_own(self, patched_read_sql) -> None:
        engine = _FakeEngine(
            _events([(72728015, "h1", "AA", "2026-09-09", "bb_upper_touch", "touch")])
        )
        frame = _predictions(
            [(44589, 75022087, "h1", "AA", "2026-09-09", "bb_upper_touch", "touch")]
        )
        out = remap(frame, engine, EVENT_REMAP)
        assert out.loc[0, "event_id"] == 72728015, "kept the source id"

    def test_the_wrong_link_is_the_case_that_matters(self, patched_read_sql) -> None:
        """The source id resolves on the target, to a different event.

        This is the PRGO/SMTC shape. Before the remap the id joined
        cleanly and named an unrelated 2020 row, so no join-based check
        could see it.
        """
        engine = _FakeEngine(
            _events(
                [
                    # The id the prediction carries, belonging to something else.
                    (39629, "h1", "SMTC", "2020-07-13", "confluence_high", "touch"),
                    # The row it is actually about.
                    (81001, "h1", "PRGO", "2026-08-05", "bb_upper_touch", "touch"),
                ]
            )
        )
        frame = _predictions([(500, 39629, "h1", "PRGO", "2026-08-05", "bb_upper_touch", "touch")])
        out = remap(frame, engine, EVENT_REMAP)
        assert out.loc[0, "event_id"] == 81001
        assert out.loc[0, "event_id"] != 39629, "the plausible wrong id survived"

    def test_each_row_resolves_independently(self, patched_read_sql) -> None:
        """The ANY() filter is a superset; the pairing is the merge.

        Two tickers and two dates make four combinations, of which only
        two are real events. A row must take its own match, not any row
        the filter admitted.
        """
        engine = _FakeEngine(
            _events(
                [
                    (1, "h1", "AA", "2026-09-08", "bb_lower_touch", "touch"),
                    (2, "h1", "BB", "2026-09-09", "bb_lower_touch", "touch"),
                ]
            )
        )
        frame = _predictions(
            [
                (10, 999, "h1", "AA", "2026-09-08", "bb_lower_touch", "touch"),
                (11, 998, "h1", "BB", "2026-09-09", "bb_lower_touch", "touch"),
            ]
        )
        out = remap(frame, engine, EVENT_REMAP).set_index("id")
        assert out.loc[10, "event_id"] == 1
        assert out.loc[11, "event_id"] == 2

    def test_signal_type_is_part_of_the_key(self, patched_read_sql) -> None:
        """One ticker can fire twice in a day on opposite sides."""
        engine = _FakeEngine(
            _events(
                [
                    (1, "h1", "KO", "2026-08-24", "bb_upper_touch", "touch"),
                    (2, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch"),
                ]
            )
        )
        frame = _predictions([(10, 0, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch")])
        out = remap(frame, engine, EVENT_REMAP)
        assert out.loc[0, "event_id"] == 2

    def test_entry_kind_separates_the_two_grains(self, patched_read_sql) -> None:
        """`next_open` and `touch` are different events for one signal.

        Measured on KO 2026-08-24: two predictions, `p_touch_3` 28.2% and
        39.6%, distinguished only by this column.
        """
        engine = _FakeEngine(
            _events(
                [
                    (55964059, "h1", "KO", "2026-08-24", "bear_close_above_upper", "next_open"),
                    (55964060, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch"),
                ]
            )
        )
        frame = _predictions(
            [(14779, 0, "h1", "KO", "2026-08-24", "bear_close_above_upper", "touch")]
        )
        out = remap(frame, engine, EVENT_REMAP)
        assert out.loc[0, "event_id"] == 55964060


class TestAMissIsNullNotTheSourceId:
    def test_an_unresolvable_key_becomes_null(self, patched_read_sql) -> None:
        """Serving carries a three-year subset (ADR 137), so a prediction
        for an aged-out event has no target row.

        NULL, not the source id: an id from the other store is not a worse
        answer than NULL, it is a confidently wrong one, and that is what
        produced 3,035 bad links.
        """
        engine = _FakeEngine(_events([(1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")]))
        frame = _predictions([(10, 75022087, "h1", "ZZ", "2019-01-02", "bb_lower_touch", "touch")])
        out = remap(frame, engine, EVENT_REMAP)
        assert pd.isna(out.loc[0, "event_id"])

    def test_an_empty_target_nulls_every_row(self, patched_read_sql) -> None:
        engine = _FakeEngine(_events([]))
        frame = _predictions([(10, 5, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        out = remap(frame, engine, EVENT_REMAP)
        assert out["event_id"].isna().all()


class TestItRefusesWhatItCannotDo:
    def test_a_frame_missing_the_natural_key_raises(self) -> None:
        """Better than silently nulling every row: the SELECT is wrong and
        someone has to widen it."""
        frame = pd.DataFrame({"id": [1], "event_id": [2]})
        with pytest.raises(ValueError, match="widen the table's SELECT"):
            remap(frame, _FakeEngine(_events([])), EVENT_REMAP)

    def test_an_empty_frame_is_returned_unchanged(self) -> None:
        frame = _predictions([])
        assert remap(frame, _FakeEngine(_events([])), EVENT_REMAP).empty


class TestTheFrameStaysWritable:
    def test_column_order_is_preserved(self, patched_read_sql) -> None:
        """`copy_upsert` builds its column list from the frame, so a
        reordered frame changes the COPY."""
        engine = _FakeEngine(_events([(1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")]))
        frame = _predictions([(10, 5, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        out = remap(frame, engine, EVENT_REMAP)
        assert list(out.columns) == list(frame.columns)

    def test_no_rows_are_added_or_lost(self, patched_read_sql) -> None:
        """A `merge` against a target with duplicate keys would multiply
        rows, which on `predictions` would be a duplicate probability."""
        engine = _FakeEngine(
            _events(
                [
                    (1, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch"),
                    (2, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch"),
                ]
            )
        )
        frame = _predictions([(10, 5, "h1", "AA", "2026-09-09", "bb_lower_touch", "touch")])
        out = remap(frame, engine, EVENT_REMAP)
        assert len(out) == 1, "a duplicated target key multiplied the frame"
