"""An adopted prediction survives the whole nightly chain onto serving's
end-of-day event (whole-branch review of `slot-keyed-adoption`, 2026-09-20,
CRITICAL 1).

**The break these tests pin.** The nightly pull adopts the Pi's prediction A
(live label `bb_lower_touch`) and links it to research event E
(`bull_close_below_lower`, ADR 194). `predict` is insert-only on `event_id`
(ADR 195), so it skips E and research never writes its own row R. Outbound,
`run_sync` used to re-resolve A's `event_id` from A's OWN label, which found
P, the Pi's provisional poller event on serving, and never E', serving's
copy of E. The serving sweep then deleted P. E' was left with no prediction
joined to it, and the site's views join `p.event_id = e.id`, so that signal
rendered with no probability. Before the branch it rendered R. That is the
normal case: 0 of 338 poller events matched research's label on 2026-09-20.

**Why a composition test.** Every step was tested alone and passed. No test
crossed pull, predict, outbound sync and sweep, which is how the break got
through review. `TestTheChainKeepsTheLiveProbabilityOnServing` crosses all
four against in-memory stores. Only storage is faked: `pd.read_sql`,
`Connection.execute`, `db_io.insert_new` and `db_io.copy_upsert` answer
from frames. Every resolution step runs as production code:
`_apply_slot_remap`, `_null_inbound_remap_collisions`,
`_null_duplicate_slot_targets`, `_apply_remap`, `_clear_remap_collisions`,
`_prepare_chunk` and `cli._sweep_provisional_poll_rows`. `predict` is the
one stand-in (`_predict`), reduced to the write it makes:
`insert_new(..., conflict_cols=["event_id"])`, the same call
`jobs/predict.py` sends.

The outbound step is `run_sync`'s per-chunk body (`_prepare_chunk` then
`copy_upsert`) over the real `_tables()` entries for `events` and
`predictions`, in `_tables()` order. The real SELECT strings are
dispatched to pandas equivalents; `scripts/verify_slot_adoption.py` step 5
runs the predictions SELECT itself against real Postgres.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from typing import Any, cast

import pandas as pd
import pytest
from sqlalchemy import Engine

from capitalscan.core.config import ServingParams
from capitalscan.jobs import cli, db_io
from capitalscan.jobs import sync as sync_job
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH

CHASH = DEFAULT_CONFIG_HASH
FLOOR = ServingParams().serving_id_floor
D = date(2026, 9, 18)
CUTOFF = date(2023, 9, 18)

_EVENT_COLS = ["id", "config_hash", "ticker", "signal_date", "signal_type", "entry_kind", "run_id"]
_PRED_COLS = [
    "id",
    "event_id",
    "config_hash",
    "ticker",
    "as_of",
    "signal_type",
    "entry_kind",
    "p_touch_3",
]
_EVENT_NATURAL_KEY = ["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"]

# The live number (CRS 2026-09-14 shape: the artifact live when the signal
# fired) and research's later one. They differ, which is what makes "which
# row did serving keep" observable.
LIVE_P = 0.823
RESEARCH_P = 0.700


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Store:
    """One database: `events` and `predictions` as frames, plus a sequence
    for `events.id`, which `copy_upsert` mints when `_drop_surrogate_id`
    has removed the source's id."""

    def __init__(self, events: pd.DataFrame, predictions: pd.DataFrame, next_id: int) -> None:
        self.tables: dict[str, pd.DataFrame] = {
            "events": events.copy(),
            "predictions": predictions.copy(),
        }
        self.next_id = next_id

    def mint(self) -> int:
        self.next_id += 1
        return self.next_id


class _Conn:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, stmt: object, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(stmt).split())
        params = params or {}
        tables = self.store.tables
        if sql.startswith('DELETE FROM "predictions"'):
            # `_clear_remap_collisions`.
            preds = tables["predictions"]
            mask = preds["event_id"].isin(params["claimed"]) & ~preds["id"].isin(params["keep"])
            tables["predictions"] = preds.loc[~mask].reset_index(drop=True)
            return _Result(int(mask.sum()))
        if sql.startswith("UPDATE signal_reports"):
            # The sweep's first statement. No `signal_reports` here.
            return _Result(0)
        if sql.startswith("DELETE FROM events"):
            # The sweep: this session's poller rows.
            ev = tables["events"]
            mask = (
                (ev["config_hash"] == params["chash"])
                & (ev["signal_date"] == params["d"])
                & (ev["run_id"].str.split("_").str[0] == "poll")
            )
            tables["events"] = ev.loc[~mask].reset_index(drop=True)
            return _Result(int(mask.sum()))
        raise AssertionError(f"unexpected statement: {sql}")


class _Engine:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def connect(self) -> _Conn:
        return _Conn(self.store)

    def begin(self) -> _Conn:
        return _Conn(self.store)


def _store_of(con: Any) -> _Store:
    return cast(_Store, con.store)


_ANY = re.compile(r'"(\w+)" = ANY\(:(v\d+)\)')


def _fake_read_sql(sql: Any, con: Any, params: dict[str, Any] | None = None, **_: Any):
    text_sql = " ".join(str(sql).split())
    params = params or {}
    tables = _store_of(con).tables

    if "id >= :floor" in text_sql:
        # `_pull_predictions`'s selection on serving.
        preds = tables["predictions"]
        return preds.loc[preds["id"] >= params["floor"]].reset_index(drop=True)

    if "LEFT JOIN events e ON e.id = p.event_id" in text_sql:
        # The outbound predictions SELECT from `_tables()`.
        preds = tables["predictions"]
        ev = tables["events"][["id", *_EVENT_NATURAL_KEY]].rename(
            columns={"id": "__eid"} | {c: f"src_event_{c}" for c in _EVENT_NATURAL_KEY}
        )
        joined = preds.assign(__k=pd.to_numeric(preds["event_id"])).merge(
            ev.assign(__k=pd.to_numeric(ev["__eid"])), on="__k", how="left"
        )
        return joined.drop(columns=["__k", "__eid"]).astype(object).where(joined.notna(), None)

    if text_sql == "SELECT * FROM predictions":
        # The pre-fix outbound SELECT, used only by the control test.
        return tables["predictions"].copy()

    if text_sql.startswith("SELECT *, cluster_id::text AS __exact_cluster_id FROM events"):
        # The outbound events SELECT, with the exact-integer text copy
        # Postgres would return beside `cluster_id`.
        ev = tables["events"]
        out = ev.loc[
            (ev["config_hash"] == params["config_hash"])
            & ev["entry_kind"].isin(["next_open", "touch"])
        ].reset_index(drop=True)
        return out.assign(
            __exact_cluster_id=[None if pd.isna(v) else str(int(v)) for v in out["cluster_id"]]
        )

    if "id = ANY(:held)" in text_sql:
        # `_pull_predictions`: the rows research already holds.
        preds = tables["predictions"]
        return preds.loc[preds["id"].isin(params["held"]), ["id"]].reset_index(drop=True)

    if "__owner_id" in text_sql:
        # `_null_inbound_remap_collisions`.
        preds = tables["predictions"]
        hit = preds.loc[preds["event_id"].isin(params["claimed"])]
        return hit.rename(columns={"id": "__owner_id", "event_id": "__val"})[
            ["__owner_id", "__val"]
        ].reset_index(drop=True)

    pairs = _ANY.findall(text_sql)
    if pairs:
        # `_apply_slot_remap` and `_apply_remap`: filter on every bound
        # column, then return the selected columns.
        select = re.match(r"SELECT (.*?) FROM", text_sql)
        assert select is not None
        cols = [c.strip().strip('"') for c in select.group(1).split(",")]
        ev = tables["events"]
        for col, name in pairs:
            ev = ev.loc[ev[col].isin(params[name])]
        return ev[cols].reset_index(drop=True)

    raise AssertionError(f"unexpected read: {text_sql}")


def _fake_insert_new(engine: Any, table_name: str, data: Any, conflict_cols: list[str]) -> int:
    """`INSERT ... ON CONFLICT (conflict_cols) DO NOTHING`. NULLs never
    conflict, as in a Postgres unique index."""
    store = _store_of(engine)
    frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    existing = store.tables[table_name]
    inserted = []
    for row in frame.to_dict("records"):
        clash = pd.Series(True, index=existing.index)
        for col in conflict_cols:
            value = row.get(col)
            clash &= existing[col] == value if not pd.isna(value) else False
        if not clash.any():
            inserted.append(row)
            existing = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    store.tables[table_name] = existing
    _assert_unique_event_id(store)
    return len(inserted)


def _fake_copy_upsert(
    engine: Any, table_name: str, frame: pd.DataFrame, conflict_cols: list[str], *_: Any
) -> int:
    """`ON CONFLICT (conflict_cols) DO UPDATE`, with `events.id` minted by the
    target when the frame carries none."""
    store = _store_of(engine)
    existing = store.tables[table_name]
    assert set(frame.columns) <= set(existing.columns), (
        f"{table_name}: columns the target does not have: "
        f"{sorted(set(frame.columns) - set(existing.columns))}"
    )
    for row in frame.to_dict("records"):
        mask = pd.Series(True, index=existing.index)
        for col in conflict_cols:
            mask &= existing[col] == row[col]
        if mask.any():
            for name, value in row.items():
                existing.loc[mask, name] = value
        else:
            new = dict(row)
            if "id" not in new:
                new["id"] = store.mint()
            existing = pd.concat([existing, pd.DataFrame([new])], ignore_index=True)
    store.tables[table_name] = existing
    _assert_unique_event_id(store)
    return len(frame)


def _assert_unique_event_id(store: _Store) -> None:
    """`predictions_event_id` is UNIQUE on both stores. A write breaking it
    raises in Postgres, so it raises here."""
    ids = store.tables["predictions"]["event_id"].dropna()
    assert not ids.duplicated().any(), f"predictions_event_id violated: {ids.tolist()}"


@pytest.fixture(autouse=True)
def _fake_storage(monkeypatch):
    monkeypatch.setattr(sync_job.pd, "read_sql", _fake_read_sql)
    monkeypatch.setattr(db_io, "insert_new", _fake_insert_new)
    monkeypatch.setattr(db_io, "copy_upsert", _fake_copy_upsert)


def _events(rows: list[tuple]) -> pd.DataFrame:
    # `cluster_id` at real magnitude (a 63-bit hash, above float64's 2**53),
    # so the chain is exercised on the values `EXACT_INT_COLUMNS` protects.
    frame = pd.DataFrame(rows, columns=_EVENT_COLS)
    return frame.assign(
        cluster_id=pd.Series(
            [648924461278083920 + i for i in range(len(frame))], index=frame.index, dtype=object
        )
    )


def _preds(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_PRED_COLS).astype({"event_id": object})


def _table(name: str) -> sync_job.SyncTable:
    return next(t for t in sync_job._tables(CUTOFF, CHASH) if t.name == name)


def _predict(research: _Engine, first_id: int) -> int:
    """`predict`'s write, reduced to what matters here: one row per research
    event, research's own label and number, insert-only on `event_id`
    (ADR 195, `jobs/predict.py`)."""
    ev = research.store.tables["events"]
    rows = [
        {
            "id": first_id + i,
            "event_id": int(e["id"]),
            "config_hash": e["config_hash"],
            "ticker": e["ticker"],
            "as_of": e["signal_date"],
            "signal_type": e["signal_type"],
            "entry_kind": e["entry_kind"],
            "p_touch_3": RESEARCH_P,
        }
        for i, e in enumerate(ev.to_dict("records"))
    ]
    return db_io.insert_new(cast(Engine, research), "predictions", rows, conflict_cols=["event_id"])


def _sync_out(
    research: _Engine,
    serving: _Engine,
    predictions_table: sync_job.SyncTable | None = None,
) -> None:
    """`run_sync`'s per-chunk body over `_tables()`'s `events` then
    `predictions`, in that order, as the real loop runs them."""
    for table in (_table("events"), predictions_table or _table("predictions")):
        chunk = _fake_read_sql(table.sql, research, {"cutoff": CUTOFF, "config_hash": CHASH})
        db_io.copy_upsert(
            cast(Engine, serving),
            table.name,
            sync_job._prepare_chunk(chunk, cast(Engine, serving), table),
            list(table.key),
        )


def _served_probability(serving: _Engine) -> pd.DataFrame:
    """What the site renders: serving's events LEFT JOIN predictions on
    `p.event_id = e.id`, the join every serving view makes."""
    ev = serving.store.tables["events"]
    preds = serving.store.tables["predictions"]
    joined = ev.merge(
        preds[["event_id", "id", "p_touch_3"]].rename(columns={"id": "prediction_id"}),
        left_on=pd.to_numeric(ev["id"]),
        right_on=pd.to_numeric(preds["event_id"]),
        how="left",
    )
    return joined[["id", "signal_type", "run_id", "prediction_id", "p_touch_3"]]


# --- The USB 2026-09-18 night, before the chain runs. ---
E_RESEARCH = 81001  # research's end-of-day event
P_SERVING = 900001  # the Pi's provisional poller event on serving
A_ID = FLOOR + 1  # the Pi's prediction, minted on serving above the floor


def _night() -> tuple[_Engine, _Engine]:
    research = _Engine(
        _Store(
            _events([(E_RESEARCH, CHASH, "USB", D, "bull_close_below_lower", "touch", "events_1")]),
            _preds([]),
            next_id=90000,
        )
    )
    serving = _Engine(
        _Store(
            _events([(P_SERVING, CHASH, "USB", D, "bb_lower_touch", "touch", "poll_20260918")]),
            _preds([(A_ID, P_SERVING, CHASH, "USB", D, "bb_lower_touch", "touch", LIVE_P)]),
            next_id=950000,
        )
    )
    return research, serving


def _run_chain(predictions_table: sync_job.SyncTable | None = None) -> tuple[_Engine, _Engine]:
    research, serving = _night()
    # 1. pull: A adopts into research, linked to E on the slot.
    sync_job._pull_predictions(cast(Engine, serving), cast(Engine, research))
    # 2. predict: E is already scored, so research writes nothing for it.
    _predict(research, first_id=1)
    # 3. outbound sync.
    _sync_out(research, serving, predictions_table)
    # 4. serving sweep.
    cli._sweep_provisional_poll_rows(cast(Engine, serving), CHASH, D)
    return research, serving


class TestTheChainKeepsTheLiveProbabilityOnServing:
    def test_the_end_of_day_event_renders_the_pis_number_after_the_sweep(self):
        research, serving = _run_chain()

        adopted = research.store.tables["predictions"]
        assert adopted["id"].tolist() == [A_ID], "predict wrote a second row for E"
        assert adopted.loc[0, "event_id"] == E_RESEARCH

        served = _served_probability(serving)
        assert served["signal_type"].tolist() == ["bull_close_below_lower"], (
            "the sweep should leave only E'"
        )
        assert served.loc[0, "prediction_id"] == A_ID, (
            "serving's end-of-day event has no prediction joined to it: the "
            "site renders it with no probability"
        )
        assert served.loc[0, "p_touch_3"] == LIVE_P

    def test_the_old_own_label_resolution_loses_it_which_is_what_this_pins(self):
        """The control. Swapping the pre-fix remap back in reproduces the
        review's trace: A lands on P, the sweep deletes P, E' is bare."""
        old = dataclasses.replace(
            _table("predictions"),
            sql="SELECT * FROM predictions",
            remaps=(sync_job._PREDICTIONS_EVENT_REMAP,),
            helper_columns=(),
        )
        _, serving = _run_chain(old)
        served = _served_probability(serving)
        assert served["signal_type"].tolist() == ["bull_close_below_lower"]
        assert pd.isna(served.loc[0, "prediction_id"])


# --- The outbound step alone. ---


def _outbound(
    research_events: pd.DataFrame,
    research_preds: pd.DataFrame,
    serving_events: pd.DataFrame,
    serving_preds: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, _Engine, _Engine]:
    research = _Engine(_Store(research_events, research_preds, next_id=0))
    serving = _Engine(
        _Store(serving_events, serving_preds if serving_preds is not None else _preds([]), 0)
    )
    table = _table("predictions")
    chunk = _fake_read_sql(table.sql, research)
    out = sync_job._prepare_chunk(chunk, cast(Engine, serving), table)
    return out, research, serving


class TestResearchWrittenRowsResolveExactlyAsBefore:
    """The ~36,000 rows that work today. A research-written prediction
    carries its event's label, so the event's key IS its own key and the
    new resolution must equal the old one row for row, misses included."""

    def test_every_row_matches_the_old_own_key_resolution(self):
        research_events = _events(
            [
                (1, CHASH, "AA", D, "bb_lower_touch", "touch", "events_1"),
                # One ticker, one day, both sides.
                (2, CHASH, "KO", D, "bb_upper_touch", "touch", "events_1"),
                (3, CHASH, "KO", D, "bear_close_above_upper", "touch", "events_1"),
                # The two grains of one signal.
                (4, CHASH, "KO", D, "bear_close_above_upper", "next_open", "events_1"),
                # Aged out of serving: no copy there.
                (5, CHASH, "ZZ", date(2019, 1, 2), "bb_lower_touch", "touch", "events_1"),
            ]
        )
        research_preds = _preds(
            [
                (10 + i, e["id"], e["config_hash"], e["ticker"], e["signal_date"],
                 e["signal_type"], e["entry_kind"], RESEARCH_P)
                for i, e in enumerate(research_events.to_dict("records"))
            ]
        )  # fmt: skip
        serving_events = _events(
            [
                (901, CHASH, "AA", D, "bb_lower_touch", "touch", "events_1"),
                (902, CHASH, "KO", D, "bb_upper_touch", "touch", "events_1"),
                (903, CHASH, "KO", D, "bear_close_above_upper", "touch", "events_1"),
                (904, CHASH, "KO", D, "bear_close_above_upper", "next_open", "events_1"),
            ]
        )
        new, _, serving = _outbound(research_events, research_preds, serving_events)
        old = sync_job._apply_remap(
            research_preds, cast(Engine, serving), sync_job._PREDICTIONS_EVENT_REMAP
        )
        assert new["event_id"].tolist()[:4] == old["event_id"].tolist()[:4] == [901, 902, 903, 904]
        assert pd.isna(new["event_id"].iloc[4]) and pd.isna(old["event_id"].iloc[4])


class TestAnAdoptedRowResolvesToTheEndOfDayEvent:
    def test_it_lands_on_e_prime_not_on_the_provisional_poller_event(self):
        research_events = _events(
            [(E_RESEARCH, CHASH, "USB", D, "bull_close_below_lower", "touch", "events_1")]
        )
        research_preds = _preds(
            [(A_ID, E_RESEARCH, CHASH, "USB", D, "bb_lower_touch", "touch", LIVE_P)]
        )
        serving_events = _events(
            [
                (P_SERVING, CHASH, "USB", D, "bb_lower_touch", "touch", "poll_20260918"),
                (950001, CHASH, "USB", D, "bull_close_below_lower", "touch", "events_1"),
            ]
        )
        out, _, _ = _outbound(research_events, research_preds, serving_events)
        assert out.loc[0, "event_id"] == 950001
        assert out.loc[0, "signal_type"] == "bb_lower_touch", "the live label was rewritten"

    def test_a_null_research_event_id_resolves_to_null(self):
        """Even when the row's own label would find a serving event."""
        research_preds = _preds([(A_ID, None, CHASH, "USB", D, "bb_lower_touch", "touch", LIVE_P)])
        serving_events = _events(
            [(P_SERVING, CHASH, "USB", D, "bb_lower_touch", "touch", "poll_20260918")]
        )
        out, _, _ = _outbound(_events([]), research_preds, serving_events)
        assert pd.isna(out.loc[0, "event_id"])


class TestCollisionClearingStillWorks:
    def test_a_serving_row_already_holding_e_prime_gives_way(self):
        """`unique_on_target` still reaches `_clear_remap_collisions`: a
        serving row with another id on E' is deleted so the write does not
        break `predictions_event_id`."""
        research_events = _events(
            [(E_RESEARCH, CHASH, "USB", D, "bull_close_below_lower", "touch", "events_1")]
        )
        research_preds = _preds(
            [(A_ID, E_RESEARCH, CHASH, "USB", D, "bb_lower_touch", "touch", LIVE_P)]
        )
        serving_events = _events(
            [(950001, CHASH, "USB", D, "bull_close_below_lower", "touch", "events_1")]
        )
        stale = _preds([(777, 950001, CHASH, "USB", D, "bull_close_below_lower", "touch", 0.5)])
        out, _, serving = _outbound(research_events, research_preds, serving_events, stale)
        assert serving.store.tables["predictions"].empty, "the colliding row survived"
        db_io.copy_upsert(cast(Engine, serving), "predictions", out, ["id"])
        assert serving.store.tables["predictions"]["id"].tolist() == [A_ID]

    def test_the_outbound_remap_still_declares_it(self):
        assert sync_job._PREDICTIONS_OUTBOUND_REMAP.unique_on_target


class TestHelperColumnsNeverReachServing:
    def test_the_written_frame_has_exactly_the_predictions_columns(self):
        research_events = _events(
            [(E_RESEARCH, CHASH, "USB", D, "bull_close_below_lower", "touch", "events_1")]
        )
        research_preds = _preds(
            [(A_ID, E_RESEARCH, CHASH, "USB", D, "bb_lower_touch", "touch", LIVE_P)]
        )
        out, _, _ = _outbound(research_events, research_preds, _events([]))
        assert list(out.columns) == _PRED_COLS

    def test_the_table_drops_every_column_the_remap_reads(self):
        table = _table("predictions")
        assert table.helper_columns == sync_job._PREDICTIONS_OUTBOUND_REMAP.source_key
        assert all(c.startswith("src_event_") for c in table.helper_columns)

    def test_the_select_carries_each_helper_as_an_alias(self):
        sql = " ".join(_table("predictions").sql.split())
        for col in _table("predictions").helper_columns:
            assert f"AS {col}" in sql
        assert "LEFT JOIN events e ON e.id = p.event_id" in sql

    def test_no_other_table_carries_helpers(self):
        others = [t for t in sync_job._tables(CUTOFF, CHASH) if t.name != "predictions"]
        assert all(t.helper_columns == () for t in others)
