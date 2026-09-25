"""`clear_predictions` clears research and serving, or neither.

**Why serving (2026-09-25).** Clearing research alone left serving holding
the old rows. The rescore mints new ids for the same events, `sync` copies
`predictions` keyed on `id`, and serving's unique `predictions_event_id`
rejected every one. BACKLOG, "`clear_predictions` does not clear serving".

**All checks before any delete.** A refusal must leave both stores as they
were; a half-cleared pair is the state this fix exists to remove.

No database here: both engines are recording fakes.
"""

from __future__ import annotations

import pytest

from capitalscan.jobs import predict as jp

CHASH = "f183b0f5209a4677"
FLOOR = 1_000_000_000


class _Result:
    def __init__(self, rows, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def scalar(self):
        return self._rows[0][0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Conn:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self._store.log.append(sql)
        if sql.startswith("SELECT count(*) FROM outcomes"):
            return _Result([(self._store.scored,)])
        if sql.startswith("SELECT id FROM predictions"):
            return _Result([(i,) for i in self._store.pi_born])
        if "id = ANY(:ids)" in sql:
            held = [i for i in (params or {})["ids"] if i in self._store.ids]
            return _Result([(len(held),)])
        if sql.startswith("DELETE FROM predictions"):
            return _Result([], rowcount=self._store.rows)
        return _Result([])


class _Store:
    def __init__(self, scored=0, rows=0, pi_born=(), ids=()):
        self.scored = scored
        self.rows = rows
        self.pi_born = list(pi_born)
        self.ids = set(ids)
        self.log: list[str] = []

    def connect(self):
        return _Conn(self)

    def begin(self):
        return _Conn(self)

    def deletes(self):
        return [q for q in self.log if q.startswith("DELETE")]


def test_both_stores_are_cleared():
    research, serving = _Store(rows=120), _Store(rows=118)
    got = jp.clear_predictions(research, CHASH, serving=serving, id_floor=FLOOR)
    assert got == jp.ClearReport(research=120, serving=118)
    assert research.deletes() and serving.deletes()


def test_without_a_serving_store_research_alone_is_cleared():
    research = _Store(rows=5)
    got = jp.clear_predictions(research, CHASH)
    assert got == jp.ClearReport(research=5, serving=None)


def test_outcomes_on_serving_refuse_before_anything_is_deleted():
    """Serving carries `outcomes` too (`sync` copies them). A refusal found
    only on serving must still leave research untouched."""
    research, serving = _Store(rows=10), _Store(scored=3, rows=10)
    with pytest.raises(ValueError, match="3 on serving"):
        jp.clear_predictions(research, CHASH, serving=serving, id_floor=FLOOR)
    assert not research.deletes() and not serving.deletes()


def test_drop_outcomes_clears_outcomes_on_both():
    research, serving = _Store(scored=2, rows=10), _Store(scored=2, rows=10)
    jp.clear_predictions(research, CHASH, drop_outcomes=True, serving=serving, id_floor=FLOOR)
    for store in (research, serving):
        assert any(q.startswith("DELETE FROM outcomes") for q in store.log)


def test_unadopted_pi_born_predictions_refuse_even_with_drop_outcomes():
    """Rows the poller minted that nightly has not pulled yet are the only
    copy of a forward-log record. `drop_outcomes` is about outcomes, not
    about these, so it does not override the refusal."""
    research = _Store(rows=10, ids={FLOOR + 1})
    serving = _Store(rows=12, pi_born=[FLOOR + 1, FLOOR + 2])
    with pytest.raises(ValueError, match="1 poller-born"):
        jp.clear_predictions(research, CHASH, drop_outcomes=True, serving=serving, id_floor=FLOOR)
    assert not research.deletes() and not serving.deletes()


def test_adopted_pi_born_predictions_do_not_block():
    research = _Store(rows=10, ids={FLOOR + 1, FLOOR + 2})
    serving = _Store(rows=12, pi_born=[FLOOR + 1, FLOOR + 2])
    got = jp.clear_predictions(research, CHASH, serving=serving, id_floor=FLOOR)
    assert got.serving == 12


def test_the_floor_defaults_to_serving_params():
    from capitalscan.core.config import ServingParams

    research, serving = _Store(), _Store()
    jp.clear_predictions(research, CHASH, serving=serving)
    assert ServingParams().serving_id_floor  # the default the call resolved
    assert any("id >= :floor" in q for q in serving.log)


def test_the_cli_passes_the_serving_store():
    """`cscan predict --clear` is the only caller. Wired to research alone,
    the fix would exist and never run."""
    import inspect

    from capitalscan.jobs import cli

    src = inspect.getsource(cli.predict)
    assert "clear_predictions(db_io.get_engine(), chash, serving=serving_store)" in src
