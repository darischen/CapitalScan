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
from collections.abc import Sequence
from datetime import date
from typing import Any

import pandas as pd
from psycopg.types.json import Jsonb
from sqlalchemy import Engine, MetaData, Table, create_engine, text
from sqlalchemy import types as sa_types
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.pool import NullPool, QueuePool

from capitalscan.jobs.db import _load_env, _psycopg3_url

_metadata_by_engine: dict[int, MetaData] = {}


def get_engine(url: str | None = None, use_null_pool: bool = False) -> Engine:
    """A SQLAlchemy engine against `DATABASE_URL_RESEARCH`.

    Ingest always targets the research database — the serving database is
    populated by `sync`, never by a fetcher (ADR 053).

    `use_null_pool=True` disables connection pooling (NullPool), needed for
    worker processes where each connection is short-lived. Without it,
    ProcessPoolExecutor workers hold onto pooled connections across tasks,
    exhausting max_connections on the server.
    """
    _load_env()
    if url is None:
        import os

        url = os.environ["DATABASE_URL_RESEARCH"]

    engine_kwargs: dict[str, Any] = {}
    if use_null_pool:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs["poolclass"] = QueuePool
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    return create_engine(_psycopg3_url(url), **engine_kwargs)


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


def copy_upsert(
    engine: Engine,
    table_name: str,
    frame: pd.DataFrame,
    conflict_cols: list[str],
    update_columns: list[str] | None = None,
) -> int:
    """`COPY` into a staging table, then one server-side upsert.

    **Same contract as `upsert`, different mechanics.** Use it where the row
    count is large enough that per-row Python dominates; `upsert` stays the
    default everywhere else and is unchanged.

    **Why.** Profiled during a full `cscan sync` on 2026-08-26: the Pi sat at
    load 1.11 of four cores with its SD card 15% utilised, while the
    workstation held 894 MB and 53.8% of *one* core. Neither the network nor
    the database was the constraint -- it was Python. `upsert` turns every
    row into a dict, SQLAlchemy re-binds each dict into parameters, and the
    rows make three full representations of themselves to travel between two
    databases that could have spoken directly. 7,469,519 rows took 114.2
    minutes, about 1,090 rows/s.

    `COPY` streams instead. No dict per row, no bind parameters, and the
    conflict resolution happens inside Postgres against a staging table.

    **One transaction, one connection.** The staging table is `TEMP`, so it
    is bound to this session and disappears with it -- there is no name to
    collide with a concurrent sync and nothing to clean up on failure.
    `upsert` opens a transaction per 1,000 rows; this opens one.

    **`ON COMMIT DROP` rather than an explicit drop**, so an exception
    between the COPY and the INSERT cannot leave the staging table behind.
    """
    if frame.empty:
        return 0

    table = _table(engine, table_name)
    table_col_names = [c.name for c in table.columns]
    cols = [c for c in table_col_names if c in frame.columns]
    if not cols:
        raise ValueError(f"{table_name!r}: the frame shares no columns with the table")
    missing_keys = [c for c in conflict_cols if c not in cols]
    if missing_keys:
        raise ValueError(
            f"{table_name!r}: conflict_cols {missing_keys!r} are absent from the frame, "
            "so ON CONFLICT could not match a row"
        )

    targets = (
        update_columns
        if update_columns is not None
        else [c for c in cols if c not in conflict_cols]
    )
    invalid = [c for c in targets if c not in table_col_names or c in conflict_cols]
    if invalid:
        raise ValueError(f"update_columns for {table_name!r} contains invalid entries {invalid!r}")

    quoted = ", ".join(f'"{c}"' for c in cols)
    stage = f"_copy_{table_name}"
    # NaN/NaT are pandas' absent markers and Postgres wants NULL. `object`
    # first, because `where` on a float column would coerce None back to NaN.
    payload = frame[cols].astype(object).where(pd.notna(frame[cols]), None)

    # **Integer columns need coercing back, per value rather than per
    # column.** A pandas column holding both integers and a null is
    # `float64`, so `10` is really `10.0`; `astype(object)` preserves the
    # float and `COPY` writes the text `"10.0"`, which Postgres rejects for
    # an integer column. The dict path never hit this because psycopg
    # adapts a Python float to an integer parameter; text COPY does not.
    #
    # Doing it as `payload[name] = payload[name].map(...)` does not work and
    # fails in both directions: assigning a Series back into an
    # object-dtype frame **re-infers the dtype**, turning the ints back into
    # floats *and* the Nones back into NaN. Measured -- `[10, 20, None]`
    # came out `[10.0, 20.0, nan]`. The conversion has to happen after the
    # frame is out of the way.
    #
    # The table's declared type is the authority, not the frame's dtype:
    # the frame's dtype is exactly what is wrong here.
    def _as_int(v: Any) -> Any:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return int(v)

    def _identity(v: Any) -> Any:
        return None if isinstance(v, float) and math.isnan(v) else v

    def _as_json(v: Any) -> Any:
        """`Jsonb`, because text COPY will not adapt a bare dict.

        The parameter path adapts a `dict` on its own -- psycopg infers
        JSONB from the target column. `COPY` has no column context, so the
        dumper is chosen from the Python type alone and a plain `dict`
        raises `cannot adapt type 'dict' using placeholder '%t'`. Wrapping
        names the type explicitly.

        Five columns across the synced tables reach this: `runs.params`,
        `signal_reports.state_json` and `.call_overlay_json`,
        `cell_stats.exit_mix`, `predictions.features_json`.
        """
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return Jsonb(v) if isinstance(v, (dict, list)) else v

    def _converter(col_type: Any) -> Any:
        if isinstance(col_type, sa_types.Integer):
            return _as_int
        if isinstance(col_type, sa_types.JSON):
            return _as_json
        return _identity

    converters = [_converter(table.columns[c].type) for c in cols]

    set_clause = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in targets)
    action = f"DO UPDATE SET {set_clause}" if targets else "DO NOTHING"
    conflict = ", ".join(f'"{c}"' for c in conflict_cols)

    with engine.begin() as conn:
        conn.exec_driver_sql(
            f'CREATE TEMP TABLE "{stage}" (LIKE "{table_name}" INCLUDING DEFAULTS) ON COMMIT DROP'
        )
        # The psycopg3 connection underneath SQLAlchemy's wrapper. `COPY`
        # is a driver feature with no SQLAlchemy surface, and it must run on
        # *this* connection -- the staging table is TEMP and invisible to
        # any other.
        raw = conn.connection.driver_connection
        if raw is None:  # pragma: no cover - a live connection always has one
            raise RuntimeError("no driver connection available for COPY")
        with raw.cursor() as cur:
            with cur.copy(f'COPY "{stage}" ({quoted}) FROM STDIN') as cp:
                for row in payload.itertuples(index=False, name=None):
                    cp.write_row([f(v) for f, v in zip(converters, row)])
        conn.exec_driver_sql(
            f'INSERT INTO "{table_name}" ({quoted}) SELECT {quoted} FROM "{stage}" '
            f"ON CONFLICT ({conflict}) {action}"
        )
    return len(payload)


def fill_event_sector_and_mcap(engine: Engine, run_id: str) -> int:
    """Populate `events.sector` and `events.mcap_usd` for one run's rows.

    **A post-pass, not a hot-path change.** Both writers -- `run_events`
    and `run_backtest` -- would otherwise have to carry a sector lookup and
    a point-in-time universe lateral into every per-ticker worker.
    `add_cofire_count` already establishes the shape: do the cross-cutting
    part once, after the workers return.

    **Why the columns exist and were empty.** Migration c2b91e4a7d08
    backfilled 1.37M rows for the serving config, but `_EVENT_COLUMNS` has
    no slot for either name, so anything written afterwards was NULL again.
    That is the trap `BACKLOG.md` describes: an unqualified `sector` in a
    query over `events JOIN tickers` resolves to the `events` copy -- a
    column that exists, is spelled correctly, and is empty.

    **The two columns have different honesty**, and the migration's column
    comments say so in the database. `mcap_usd` is point-in-time: the
    universe evaluation in force at `signal_date`. `sector` is a current
    snapshot, because `tickers.sector` has no history -- the mild
    look-ahead ADR 135 names and the backlog accepts.

    Scoped to `run_id` so a re-run does not rewrite another run's rows, and
    idempotent: it only touches rows where the value is still NULL.
    """

    def _rows(result: Any) -> int:
        """`rowcount` is optional in DBAPI and absent on a stubbed cursor.

        The count is reported, never acted on -- the fill is idempotent and
        scoped by `run_id`, so a driver that declines to say how many rows
        it touched changes nothing. Returning 0 rather than raising keeps
        this from turning an informational number into a failure.
        """
        return int(getattr(result, "rowcount", 0) or 0)

    with engine.begin() as conn:
        mcap = _rows(
            conn.execute(
                text(
                    "UPDATE events e SET mcap_usd = ("
                    "  SELECT u.mcap_usd FROM universe u"
                    "   WHERE u.ticker = e.ticker AND u.as_of <= e.signal_date"
                    "     AND u.config_hash = e.config_hash AND u.mcap_usd IS NOT NULL"
                    "   ORDER BY u.as_of DESC LIMIT 1)"
                    " WHERE e.run_id = :run_id AND e.mcap_usd IS NULL"
                ),
                {"run_id": run_id},
            )
        )
        sector = _rows(
            conn.execute(
                text(
                    "UPDATE events e SET sector = t.sector FROM tickers t"
                    " WHERE t.ticker = e.ticker AND e.run_id = :run_id"
                    "   AND e.sector IS NULL AND t.sector IS NOT NULL"
                ),
                {"run_id": run_id},
            )
        )
    return mcap + sector


def fill_event_derived_state(engine: Engine, run_id: str) -> int:
    """Populate `bb_mid`, `close` and `vix_pct_252d` for one run's rows.

    DESIGN §7.3's last three features (f1c8a260d94e). Each exists at t-1 in
    a source table; this copies it onto the event row so the training frame
    can read it, the same way `fill_event_sector_and_mcap` does.

    **Every lookup is `ts < signal_date`**, reproducing `_prior_indicator`'s
    "latest strictly before" (Ruling C3). Verified rather than assumed:
    running this lateral for `bb_pctb`, a column the worker already
    computes, matched the stored value on **20,000 of 20,000** rows with
    zero mismatches. Invariant 3 is the highest-risk silent failure here, so
    it gets a proof and not a comment.

    **`close` is the t-1 close, never `entry_price`**, which is priced at
    *t*. Using the entry as the denominator of `atr_14 / close` would put
    it into a state-at-signal feature.

    Scoped to `run_id` and only touching NULLs, so it is idempotent and
    cannot rewrite another run's rows.
    """

    def _rows(result: Any) -> int:
        return int(getattr(result, "rowcount", 0) or 0)

    with engine.begin() as conn:
        return _rows(
            conn.execute(
                text(
                    "UPDATE events e SET"
                    "  bb_mid = (SELECT i.bb_mid FROM indicators i"
                    "             WHERE i.ticker = e.ticker AND i.interval = '1d'"
                    "               AND i.ts < e.signal_date"
                    "             ORDER BY i.ts DESC LIMIT 1),"
                    "  close = (SELECT b.close FROM bars b"
                    "            WHERE b.ticker = e.ticker AND b.interval = '1d'"
                    "              AND b.ts < e.signal_date"
                    "            ORDER BY b.ts DESC LIMIT 1),"
                    "  vix_pct_252d = (SELECT m.vix_pct_252d FROM market_days m"
                    "                   WHERE m.ts < e.signal_date"
                    "                   ORDER BY m.ts DESC LIMIT 1)"
                    " WHERE e.run_id = :run_id"
                    "   AND (e.bb_mid IS NULL OR e.close IS NULL OR e.vix_pct_252d IS NULL)"
                ),
                {"run_id": run_id},
            )
        )


def json_safe(value: object) -> object:
    """Coerce one value into something `json.dumps` accepts.

    JSONB columns are serialised by psycopg with the stdlib encoder, which
    raises on `datetime.date`, on numpy scalars, and on anything else a
    DataFrame row hands over.

    Found 2026-08-26: `cscan nightly` died writing a `bar_rejects` payload
    whose `filed_on` was a `date`, **after** `run_shares` had written
    236,008 rows. The data landed, the job reported `failed`, and that
    aborted the eight remaining nightly steps. A reject log took down the
    pipeline it exists to annotate.
    """
    import math
    from datetime import date as _date
    from datetime import datetime as _datetime

    import numpy as np

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # `json.dumps` emits bare `NaN`/`Infinity` for these, which is not
        # valid JSON and Postgres rejects into JSONB. Absent stays absent.
        return value if math.isfinite(value) else None
    if value is pd.NaT:
        # `NaT` is a `datetime` subclass, so it reaches the branch below and
        # `isoformat()` returns the literal string "NaT" -- a missing date
        # recorded as though it were real.
        return None
    if isinstance(value, (_datetime, _date)):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, np.bool_):
        return bool(value)
    return str(value)


def json_safe_payload(payload: dict) -> dict:
    return {k: json_safe(v) for k, v in payload.items()}


def append(engine: Engine, table_name: str, data: list[dict] | pd.DataFrame) -> int:
    """Plain insert for append-only tables (`bar_rejects`, `runs`)."""
    rows = _rows_from(data)
    if not rows:
        return 0
    # **Coerced here, not only at the call sites.** Two construction sites
    # were wrapped on 2026-08-26 and a third was missed, so the same failure
    # recurred two minutes after the "fix" was committed. Every reject
    # passes through this function, which makes it the one place the
    # guarantee can actually hold.
    rows = [
        {k: json_safe_payload(v) if isinstance(v, dict) else v for k, v in row.items()}
        for row in rows
    ]
    table = _table(engine, table_name)
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
    return len(rows)


def purge_bars(
    engine: Engine, ticker: str, dates: Sequence[date] | None = None, *, all_dates: bool = False
) -> dict[str, int]:
    """Delete daily bars and everything computed from them.

    Returns a count per table.

    **A bar is not a leaf.** `indicators` is computed per bar, and `events`
    reference bars three ways -- `signal_date` (the bar the signal fired
    on), `entry_date` (the fill bar, which for `next_open` is t+1, a
    *different* bar), and `exit_date`. None of those are foreign keys:
    `events` has exactly one FK, to `runs`. So deleting from `bars` leaves
    dependents pointing at nothing and the database raises nothing.

    **This function exists because that happened.** On 2026-08-27 a purge
    of 29,242 fabricated bars left ~300 orphaned events. Nothing complained
    at delete time; it surfaced 75 minutes later as three failing harness
    checks (`entry_sanity`, `exit_sanity`, `non_overlap`) and took two
    rounds to clear, because the first cleanup matched `signal_date` only
    and `next_open` fills a day later.

    The ordering below is the lesson: **`entry_date` and `exit_date` are
    separate references and each needs its own pass.** Matching one is
    what made the first attempt look complete when it was not.

    Every deletion is logged to `bar_rejects` by the caller, not here --
    this is the mechanism, and the reason belongs to whoever invoked it.
    """
    if not all_dates and not dates:
        return {}

    where_bars = "ticker = :t AND interval = '1d'"
    params: dict[str, object] = {"t": ticker}
    if not all_dates:
        where_bars += " AND ts = ANY(:dates)"
        params["dates"] = list(dates or [])

    counts: dict[str, int] = {}
    with engine.begin() as conn:
        # The bar dates being removed, captured before the delete so the
        # dependent passes can match against them.
        doomed = [
            r[0]
            for r in conn.execute(
                text(f"SELECT ts FROM bars WHERE {where_bars}"), params
            ).fetchall()
        ]
        if not doomed:
            return {}

        counts["bars"] = int(
            conn.execute(text(f"DELETE FROM bars WHERE {where_bars}"), params).rowcount or 0
        )
        counts["indicators"] = int(
            conn.execute(
                text("DELETE FROM indicators WHERE ticker = :t AND ts = ANY(:d)"),
                {"t": ticker, "d": doomed},
            ).rowcount
            or 0
        )
        # Three passes, deliberately. An event whose fill bar is gone is
        # just as broken as one whose signal bar is gone, and they are
        # different rows.
        total_events = 0
        for column in ("signal_date", "entry_date", "exit_date"):
            total_events += int(
                conn.execute(
                    text(f"DELETE FROM events WHERE ticker = :t AND {column} = ANY(:d)"),
                    {"t": ticker, "d": doomed},
                ).rowcount
                or 0
            )
        counts["events"] = total_events
    return counts
