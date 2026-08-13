"""`write_cell_stats` against the real composite key (Session 12.3).

ADR 096 changed `cell_stats`' primary key from `cell_id` to
`(cell_id, config_hash)` so that one Phase 4 run per config stops
overwriting the last. Session 9's sweep wrote 18 distinct `config_hash`
values into `events`, and comparing those configs is the entire reason they
were swept.

**That key had never been exercised.** Nothing wrote two configs and
checked both survived. A `cell_id`-only key would pass every other test in
the suite and silently keep one snapshot at a time.

**Writes only its own rows and deletes them.** Scoping is by generated
`config_hash` values, so a cleanup failure cannot reach a production row.

    uv run pytest capitalscan/tests/integration/test_cell_stats_write.py
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import db_io
from capitalscan.research.cell_stats import write_cell_stats


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture()
def scoped():
    engine = db_io.get_engine()
    token = uuid.uuid4().hex[:10]
    run_id = f"test_write_{token}"
    config_a = f"wcfgA{token}"
    config_b = f"wcfgB{token}"

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (run_id, job, started_at, status, git_sha, params) "
                "VALUES (:run_id, 'test', now(), 'ok', 'test', '{}'::jsonb)"
            ),
            {"run_id": run_id},
        )
    try:
        yield {"engine": engine, "run_id": run_id, "config_a": config_a, "config_b": config_b}
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM cell_stats WHERE config_hash IN (:a, :b)"),
                {"a": config_a, "b": config_b},
            )
            conn.execute(text("DELETE FROM runs WHERE run_id = :run_id"), {"run_id": run_id})


def _rows(ctx, config_hash: str, p_hit: float) -> pd.DataFrame:
    """Two cells, in `cell_stats` shape. `cell_id` is deliberately identical
    across configs: that collision is exactly what the composite key exists
    to permit."""
    return pd.DataFrame(
        [
            {
                "cell_id": f"probe_{i}|long|0-10|all|next_open|train|pooled|h5|t0.03",
                "run_id": ctx["run_id"],
                "config_hash": config_hash,
                "signal_type": "bb_lower_touch",
                "side": "long",
                "dd_bucket": "0-10",
                "n_events": 100 + i,
                "n_eff": 50.0,
                "p_hit": p_hit,
                "exit_mix": {"timeout": 0.6, "target": 0.4},
                "suppressed": False,
                "computed_at": datetime.now(UTC),
                "git_sha": "test",
            }
            for i in range(2)
        ]
    )


def _read(conn, config_hash: str) -> list[Any]:
    rows = conn.execute(
        text("SELECT cell_id, p_hit, arm FROM cell_stats WHERE config_hash = :c ORDER BY cell_id"),
        {"c": config_hash},
    ).fetchall()
    return list(rows)


def test_a_second_config_adds_rows_rather_than_replacing(scoped):
    """ADR 096's composite key, exercised for the first time."""
    engine = scoped["engine"]
    write_cell_stats(engine, _rows(scoped, scoped["config_a"], 0.41))
    write_cell_stats(engine, _rows(scoped, scoped["config_b"], 0.62))

    with engine.connect() as conn:
        rows_a = _read(conn, scoped["config_a"])
        rows_b = _read(conn, scoped["config_b"])

    assert len(rows_a) == 2
    assert len(rows_b) == 2
    # The first config's values survived the second write untouched. Under a
    # `cell_id`-only key the second write would have overwritten them and
    # `rows_a` would read 0.62.
    assert {float(r.p_hit) for r in rows_a} == {0.41}
    assert {float(r.p_hit) for r in rows_b} == {0.62}


def test_the_two_configs_share_cell_ids(scoped):
    """Guards the test above. If the fixtures happened to produce distinct
    `cell_id` values, both writes would insert cleanly under a `cell_id`-only
    key too, and nothing would have been proven."""
    engine = scoped["engine"]
    write_cell_stats(engine, _rows(scoped, scoped["config_a"], 0.41))
    write_cell_stats(engine, _rows(scoped, scoped["config_b"], 0.62))

    with engine.connect() as conn:
        ids_a = {r.cell_id for r in _read(conn, scoped["config_a"])}
        ids_b = {r.cell_id for r in _read(conn, scoped["config_b"])}
    assert ids_a == ids_b


def test_rewriting_one_config_updates_in_place(scoped):
    """The other half of `ON CONFLICT`: a re-run of the same config must
    refresh its rows rather than duplicate them."""
    engine = scoped["engine"]
    write_cell_stats(engine, _rows(scoped, scoped["config_a"], 0.41))
    write_cell_stats(engine, _rows(scoped, scoped["config_a"], 0.55))

    with engine.connect() as conn:
        rows = _read(conn, scoped["config_a"])
    assert len(rows) == 2
    assert {float(r.p_hit) for r in rows} == {0.55}


def test_written_rows_carry_the_signal_arm(scoped):
    """ADR 105. The writer does not set `arm`; the column default does, and
    `signal` is correct for everything Session 12 produces."""
    engine = scoped["engine"]
    write_cell_stats(engine, _rows(scoped, scoped["config_a"], 0.41))
    with engine.connect() as conn:
        assert {r.arm for r in _read(conn, scoped["config_a"])} == {"signal"}


def test_exit_mix_round_trips_as_jsonb(scoped):
    engine = scoped["engine"]
    write_cell_stats(engine, _rows(scoped, scoped["config_a"], 0.41))
    with engine.connect() as conn:
        mix = conn.execute(
            text("SELECT exit_mix FROM cell_stats WHERE config_hash = :c LIMIT 1"),
            {"c": scoped["config_a"]},
        ).scalar_one()
    assert mix == {"timeout": 0.6, "target": 0.4}


def test_an_empty_frame_writes_nothing(scoped):
    assert write_cell_stats(scoped["engine"], pd.DataFrame()) == 0
