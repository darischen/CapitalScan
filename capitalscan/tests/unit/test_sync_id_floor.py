"""`_reset_sequences` learns which store it is fixing (forward-log adoption,
2026-09-19).

**The collision this closes.** Serving's poller and research's nightly
`predict` each mint `predictions.id` from their own sequence over the same
numeric range -- the same shape of defect ADR 163 already fixed for
`events`, except `predictions` conflicts on `id` itself, so the surrogate
cannot simply be dropped the way `_drop_surrogate_id` drops it for `events`.
Measured 2026-09-19: serving's max id was 224,862, research's was 191,860,
and 3 ids were shared between rows describing different signals.

`ServingParams.serving_id_floor` (1e9) splits the range: serving mints at or
above it, research stays below. `_reset_sequences` is where the split is
enforced, on every sync and every pull, so it survives a Pi reflash or a
fresh serving store without a migration (see `docs/superpowers/specs/
2026-09-19-forward-log-adoption-design.md`, "The floor constant").

These tests assert on the SQL `_reset_sequences` builds, via a fake engine
that records what was executed -- the same no-real-database approach
`test_sync_remap.py` uses for `_apply_remap`. Nothing here opens a
connection.
"""

from __future__ import annotations

import dataclasses

import pytest

from capitalscan.core.config import Config, ServingParams
from capitalscan.jobs import sync as sync_job
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH


class _FakeConn:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def execute(self, stmt: object) -> None:
        self._sink.append(str(stmt))

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeEngine:
    """Answers `engine.begin()` and records the SQL text passed to `execute`.

    `_reset_sequences` never reads a result from the connection, so there is
    nothing to fake beyond capture.
    """

    def __init__(self) -> None:
        self.executed: list[str] = []

    def begin(self) -> _FakeConn:
        return _FakeConn(self.executed)


def _sql(*, serving: bool) -> str:
    engine = _FakeEngine()
    sync_job._reset_sequences(engine, serving=serving)  # type: ignore[arg-type]
    assert len(engine.executed) == 1, "one statement, one transaction"
    return " ".join(engine.executed[0].split())


class TestServingRaisesABelowFloorSequence:
    def test_it_takes_the_greater_of_the_max_id_and_the_floor(self) -> None:
        """`greatest(n, floor)` is what raises a below-floor max to the
        floor -- Postgres, not Python, decides which one wins, because the
        real max id is only known inside the DO block, per table."""
        sql = _sql(serving=True)
        floor = ServingParams().serving_id_floor
        assert f"greatest(n, {floor})" in sql

    def test_it_still_sets_the_max_for_every_other_table(self) -> None:
        """The floor logic is scoped to `predictions`; nothing else changes."""
        sql = _sql(serving=True)
        assert "coalesce(max(%I),0) FROM %s" in sql


class TestServingLeavesAnAboveFloorSequenceAlone:
    def test_the_clamp_is_one_directional(self) -> None:
        """`greatest`, never `least`. An id already minted above the floor
        (ordinary serving growth) must keep its own max, not get pulled back
        down to it -- `least` would silently lose rows to the next insert."""
        sql = _sql(serving=True)
        assert "least(" not in sql.lower()


class TestResearchExcludesIdsAtOrAboveTheFloor:
    def test_the_max_is_computed_under_the_floor(self) -> None:
        """Without the `WHERE` clause an adopted, billion-range id (pulled
        in from serving) would push research's own sequence into serving's
        range and recreate the collision one day later."""
        sql = _sql(serving=False)
        floor = ServingParams().serving_id_floor
        assert "%I < %L" in sql
        assert f"r.col, {floor}" in sql

    def test_research_never_clamps_upward(self) -> None:
        """Research's own allocation must never be pushed toward the floor --
        only filtered below it. `greatest` belongs to serving alone."""
        sql = _sql(serving=False)
        assert "greatest(" not in sql

    def test_a_store_holding_only_adopted_rows_is_left_untouched(self) -> None:
        """`max(id) WHERE id < floor` over a table holding only ids >= floor
        returns NULL -> coalesced to 0, and the existing `IF n > 0` guard
        already skips `setval(seq, 0)`, which Postgres rejects. Guards the
        premise: if that guard is ever removed, this stops being true and a
        research store repaired after a full adoption pull would crash."""
        sql = _sql(serving=False)
        assert "IF n > 0 THEN PERFORM setval" in sql


class TestTheFloorComesFromServingParams:
    def test_changing_the_dataclass_default_changes_the_generated_sql(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the value is read from `ServingParams`, not typed as a
        literal in `jobs/sync.py`: patching the dataclass's own default
        moves the number in the SQL this module builds."""
        patched = dataclasses.replace(ServingParams(), serving_id_floor=42)
        monkeypatch.setattr(sync_job, "ServingParams", lambda: patched)

        sql = _sql(serving=True)

        assert "greatest(n, 42)" in sql
        assert "1000000000" not in sql

    def test_jobs_sync_carries_no_floor_literal(self) -> None:
        """Invariant 9: no magic numbers outside `core/config.py`. The only
        place `1_000_000_000` (or its un-underscored form) may appear in
        this module is nowhere -- it must be reached through
        `ServingParams().serving_id_floor` every time."""
        import inspect

        src = inspect.getsource(sync_job)
        assert "1_000_000_000" not in src
        assert "1000000000" not in src


class TestServingParamsIsStillNotAFieldOfConfig:
    def test_serving_params_is_not_reachable_from_config(self) -> None:
        """Folding `ServingParams` into `Config` would move `config_hash`
        for every row already keyed on it -- the same argument the class
        docstring already makes for `history_years` and
        `breadth_rank_floor`."""
        field_types = {f.type for f in dataclasses.fields(Config)}
        assert "ServingParams" not in field_types

    def test_the_default_config_hash_did_not_move(self) -> None:
        from capitalscan.jobs.config import config_hash

        assert config_hash(Config()) == DEFAULT_CONFIG_HASH


class TestPredictionsMaxIdSql:
    """`predictions_max_id_sql` is the seam `scripts/verify_id_floor.py`
    executes against its `zz_` scratch tables (Task 5c) instead of
    hand-copying the comparison. Pinned here so the two branches cannot
    silently swap or reverse without a unit test failing first."""

    def test_serving_takes_the_greater_of_max_and_floor(self) -> None:
        sql = sync_job.predictions_max_id_sql("zz_serving", 1_000, serving=True)
        assert sql == 'SELECT greatest(coalesce(max("id"),0), 1000) FROM "zz_serving"'

    def test_research_excludes_anything_at_or_above_the_floor(self) -> None:
        sql = sync_job.predictions_max_id_sql("zz_research", 1_000, serving=False)
        assert sql == 'SELECT coalesce(max("id"),0) FROM "zz_research" WHERE "id" < 1000'
        # The comparison direction is the one clause keeping research
        # below the floor -- a reversed `>=` here would silently let
        # research's own allocation race into serving's range.
        assert "< 1000" in sql
        assert ">= 1000" not in sql
