"""Row-level database IO for ingest jobs: engine, upsert, append (DESIGN §2.6).

`jobs/db.py` wraps Alembic (schema migrations). This module wraps row
writes against an already-migrated schema. Both live in `jobs/` because
`core/` performs no IO (invariant 1).

**Idempotency rule 1** (DESIGN §2.6): all writes are
`INSERT ... ON CONFLICT DO UPDATE`. No plain inserts on tables with a
natural key — `upsert()` is that path. `bar_rejects` and `runs` are the
two append-only exceptions (a reject or a run record is never revised in
place), and `append()` is theirs.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from sqlalchemy import Engine, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert

from capitalscan.jobs.db import _load_env, _psycopg3_url

_metadata_by_engine: dict[int, MetaData] = {}


def get_engine(url: str | None = None) -> Engine:
    """A SQLAlchemy engine against `DATABASE_URL_RESEARCH`.

    Ingest always targets the research database — the serving database is
    populated by `sync`, never by a fetcher (ADR 053).
    """
    _load_env()
    if url is None:
        import os

        url = os.environ["DATABASE_URL_RESEARCH"]
    return create_engine(_psycopg3_url(url))


def _table(engine: Engine, name: str) -> Table:
    metadata = _metadata_by_engine.setdefault(id(engine), MetaData())
    if name not in metadata.tables:
        Table(name, metadata, autoload_with=engine)
    return metadata.tables[name]


def _native(value: Any) -> Any:
    """Coerce numpy/pandas scalars to plain Python so the DBAPI can bind them."""
    if value is None or isinstance(value, (list, tuple, dict)):
        return value
    if hasattr(value, "item"):  # numpy scalar (int64, float64, bool_, ...)
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _rows_from(data: list[dict] | pd.DataFrame) -> list[dict]:
    if isinstance(data, pd.DataFrame):
        if data.empty:
            return []
        data = data.to_dict("records")
    return [{k: _native(v) for k, v in row.items()} for row in data]


def upsert(
    engine: Engine,
    table_name: str,
    data: list[dict] | pd.DataFrame,
    conflict_cols: list[str],
    update_columns: list[str] | None = None,
) -> int:
    """`INSERT ... ON CONFLICT (conflict_cols) DO UPDATE`. Returns rows sent.

    By default (`update_columns=None`), every non-key column is overwritten
    with the new value — "keep latest fetch" (DESIGN §2.3's duplicate-row
    rule) is the policy every existing caller (`run_indicators`,
    `run_universe`, the ingest jobs, and `run_events` before Session 9) uses,
    and that default is unchanged: this branch runs the exact same
    dict-comprehension it always did.

    `update_columns`, when given, narrows `DO UPDATE SET` to exactly that
    list (Ruling C4). This exists because `events` gets written by two jobs
    that share a natural key but compute disjoint columns — `run_events`
    (signal-side) and `run_backtest` (exit-side, Task 9b). Postgres fills
    any column absent from the INSERT's VALUES with NULL before `EXCLUDED`
    ever sees it, so an unscoped update from a row dict that only carries
    half the columns silently nulls the other half on every conflict. A
    column-scoped update is how each job avoids nulling the other's work.

    A name in `update_columns` that is not a real column, or that is itself
    one of `conflict_cols`, raises `ValueError` rather than being ignored.
    Silently dropping an unrecognized column is exactly the failure mode
    this function exists to close off — a typo would produce the same
    silent-NULL defect this whole feature was added to fix, just one layer
    up.

    An empty list (`update_columns=[]`, distinct from the `None` default)
    also raises `ValueError`. It means "update no columns at all," which is
    a no-op `DO UPDATE SET` and, on a natural-key table, almost certainly a
    caller bug — a programmatically-built list that came out empty, not a
    deliberate request. Left unchecked, `on_conflict_do_update(set_={})`
    would reach SQLAlchemy's own `set parameter dictionary must not be
    empty` error instead, which names a parameter the caller never passed
    and isn't specific to this codebase's column-ownership rule.

    Inserts in batches of 1,000 rows to avoid exceeding Postgres parameter limits.
    """
    rows = _rows_from(data)
    if not rows:
        return 0
    table = _table(engine, table_name)

    if update_columns is not None:
        if not update_columns:
            raise ValueError(
                f"update_columns for {table_name!r} is an empty list, which would "
                "update no columns at all — pass None for the default (every "
                "non-key column) or a non-empty list of the columns this caller "
                "actually computed"
            )
        table_col_names = {c.name for c in table.columns}
        invalid = [c for c in update_columns if c not in table_col_names or c in conflict_cols]
        if invalid:
            raise ValueError(
                f"update_columns for {table_name!r} contains invalid entries {invalid!r}: "
                "each name must be a real column on the table and must not be one of "
                f"conflict_cols {conflict_cols!r}"
            )

    batch_size = 1000
    total_inserted = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = pg_insert(table).values(batch)
        target_cols = (
            update_columns
            if update_columns is not None
            else [c.name for c in table.columns if c.name not in conflict_cols]
        )
        update_cols = {c: stmt.excluded[c] for c in target_cols}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols)
        with engine.begin() as conn:
            conn.execute(stmt)
        total_inserted += len(batch)

    return total_inserted


def append(engine: Engine, table_name: str, data: list[dict] | pd.DataFrame) -> int:
    """Plain insert for append-only tables (`bar_rejects`, `runs`)."""
    rows = _rows_from(data)
    if not rows:
        return 0
    table = _table(engine, table_name)
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
    return len(rows)
