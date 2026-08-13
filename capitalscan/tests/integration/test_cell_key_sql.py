"""`cell_key` executed by Postgres, diffed against `core/cells.py`.

`tests/unit/test_cells.py` pins string constants that were read out of
Postgres by hand on 2026-08-11. That catches a regression in the Python
implementation but cannot catch the SQL function changing underneath it,
and it goes stale the moment someone edits the migration. This tier closes
the gap by running the real function and comparing, which is the only
oracle that counts (session brief 12.1).

**Read-only.** This file issues `SELECT` only: no `TRUNCATE`, no `INSERT`,
no `UPDATE`, nothing scoped or otherwise. It is safe to run against the
live database while a poller is writing, which the rest of this directory
is emphatically not.

Run it directly, since the fast tier does not collect this directory:

    uv run pytest capitalscan/tests/integration/test_cell_key_sql.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.cells import cell_key, headline_grid
from capitalscan.core.config import StatsParams
from capitalscan.jobs import db_io


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

SQL = text(
    "SELECT cell_key(:signal_type, :side, :dd_bucket, :strength, :entry_kind, "
    ":split, :era, :horizon, CAST(:target AS numeric))"
)


def _postgres_cell_key(conn, **kwargs) -> str | None:
    result = conn.execute(SQL, kwargs).scalar_one()
    return None if result is None else str(result)


def _combinations() -> list[dict]:
    """Parameter sets spanning all four coalescing paths, both drop paths,
    and every `StatsParams.reach_targets` value.

    Built from the headline grid rather than written out, so a grid change
    widens the parity check instead of leaving it pinned to a stale list.
    """
    sp = StatsParams()
    combos: list[dict] = []

    # The Session 12 call shape: null strength, null era, every cell x every
    # ladder target. 12 x 4 = 48 on its own.
    for spec in headline_grid(sp):
        for target in sp.reach_targets:
            combos.append(
                dict(
                    signal_type=spec.signal_type,
                    side=spec.side,
                    dd_bucket=spec.dd_bucket,
                    strength=None,
                    entry_kind="next_open",
                    split="train",
                    era=None,
                    horizon=5,
                    target=target,
                )
            )

    base = dict(
        signal_type="bb_lower_touch",
        side="long",
        dd_bucket="0-10",
        strength=None,
        entry_kind="next_open",
        split="train",
        era=None,
        horizon=5,
        target=0.02,
    )

    # Coalescing path: null dd_bucket -> 'all'
    combos.append({**base, "dd_bucket": None})
    # Coalescing path: non-null strength, exercising the ::text cast
    combos += [{**base, "strength": s} for s in (1, 2, 3, 0, -1)]
    # Coalescing path: null era -> 'pooled', and each real era
    combos += [{**base, "era": e} for e in ("2010-2014", "2015-2019", "2020-2023", "2024+")]
    # Drop path: concat_ws omits nulls rather than emitting empty fields
    combos += [
        {**base, "signal_type": None},
        {**base, "side": None},
        {**base, "entry_kind": None},
        {**base, "split": None},
        {**base, "horizon": None},
        {**base, "target": None},
        {**base, "signal_type": None, "horizon": None, "target": None},
    ]
    # Target formatting: trailing-dot, half-up rounding, sign, wider values
    combos += [
        {**base, "target": t}
        for t in (0.0, 1.0, 0.125, 0.1235, 0.12345, 0.0005, -0.05, 12.5, 0.999, 0.9995)
    ]
    # Horizon values from the fwd_ret ladder
    combos += [{**base, "horizon": h} for h in sp.fwd_ret_horizons]
    # All four splits, including the one no headline cell may read
    combos += [{**base, "split": s} for s in ("train", "validate", "holdout")]

    return combos


def test_enough_combinations_to_be_worth_running():
    """The brief asks for at least 20."""
    assert len(_combinations()) >= 20


def test_python_matches_postgres_on_every_combination():
    engine = db_io.get_engine()
    mismatches = []
    with engine.connect() as conn:
        for params in _combinations():
            expected = _postgres_cell_key(conn, **params)
            actual = cell_key(
                params["signal_type"],
                params["side"],
                params["dd_bucket"],
                params["strength"],
                params["entry_kind"],
                params["split"],
                params["era"],
                params["horizon"],
                params["target"],
            )
            if actual != expected:
                mismatches.append((params, expected, actual))

    assert not mismatches, "\n".join(
        f"{p}\n  postgres: {e!r}\n  python:   {a!r}" for p, e, a in mismatches
    )


def test_all_four_coalescing_paths_are_actually_exercised():
    """Guards the combination builder itself. A parity test that never
    passes a null proves nothing about the coalescing branches, and it
    would still be green."""
    combos = _combinations()
    assert any(c["dd_bucket"] is None for c in combos)
    assert any(c["strength"] is None for c in combos)
    assert any(c["strength"] is not None for c in combos)
    assert any(c["era"] is None for c in combos)
    assert any(c["era"] is not None for c in combos)


def test_cell_key_is_immutable():
    """A `VOLATILE` function cannot back an index or a generated column,
    and nothing warns you at the point of use — the index creation simply
    fails much later, or the generated column is rejected outright."""
    engine = db_io.get_engine()
    with engine.connect() as conn:
        volatility = conn.execute(
            text("SELECT provolatile FROM pg_proc WHERE proname = 'cell_key'")
        ).scalar_one()
    assert volatility == "i", f"cell_key must be IMMUTABLE, pg_proc.provolatile is {volatility!r}"


def test_cell_key_is_called_on_null_input():
    """`RETURNS NULL ON NULL INPUT` would make every coalescing branch
    unreachable: the function would short-circuit to null before
    `coalesce` ever ran, and all four documented paths would be dead
    code."""
    engine = db_io.get_engine()
    with engine.connect() as conn:
        is_strict = conn.execute(
            text("SELECT proisstrict FROM pg_proc WHERE proname = 'cell_key'")
        ).scalar_one()
    assert is_strict is False


def test_signature_is_still_nine_parameters():
    """`cell_id` is derived from component columns and nothing else
    (invariant 5b). A tenth parameter — `config_hash` is the recurring
    proposal — would change every existing `cell_id` silently."""
    engine = db_io.get_engine()
    with engine.connect() as conn:
        nargs = conn.execute(
            text("SELECT pronargs FROM pg_proc WHERE proname = 'cell_key'")
        ).scalar_one()
    assert nargs == 9
