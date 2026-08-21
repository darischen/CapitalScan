"""Push the serving subset to the cloud store (ADR 053, ADR 137).

**One direction, always.** Local research is the source of truth and the
cloud copy is derived. Nothing here reads a row from serving and writes it
back, and nothing on the serving side is authored — a serving database can
be dropped and rebuilt from this job at any time, which is what makes it
safe to point a public site at.

**A serving cut, never a statistical one.** The subset is smaller in
*dates*, not in *answers*: `cell_stats`, `benchmarks` and every measured
number are computed locally against full history and shipped whole. A
reader of the deployed site sees fewer sessions on a chart, never a
different hit rate.

**Why the tables are enumerated rather than discovered.** The list below
came from `pg_depend` on the serving views, but it is written down because
a table appearing in a view is not consent to publish it: `bar_rejects`,
`runs` and `quotes_live` are all local diagnostics, and a discovery-based
sync would ship whichever of them a future view happened to touch. Adding
a table here is a deliberate act.

**The live session is not synced, and that is a decision rather than an
omission.** `bars_live` holds today's partial candle, rewritten every five
minutes by a poller that runs on this workstation. A nightly copy would
give the deployed site a price frozen at whenever the sync last ran and
label it live — the exact failure ADR 131 and ADR 134 exist to prevent, and
worse remotely because nobody there can see the poller is not running.

So the deployed site has no live candle and no live price. `liveBar`
returns `None` against an empty table, the chart stops at yesterday's
close, and the header shows the close alone. That is honest: live data is
local because the poller is local. `test_bars_live_isolation.py` caught the
first version of this file shipping it.

**Conflict keys are the tables' real constraints, checked against
`pg_constraint` rather than guessed.** The first version of this file
guessed three of them wrong — `serving_config` conflicts on `only_row` not
`id`, `cell_stats` on `(cell_id, config_hash)` with `run_id` deliberately
outside it, and `predictions` on `id`. Postgres rejects an `ON CONFLICT`
that names no unique constraint, so the wrong two failed loudly; the
`cell_stats` one would not have. Adding `run_id` to that key makes every
re-run insert a second row for the same cell instead of replacing it, and
the serving store would accumulate stale statistics that look current.
`test_sync.py` now asserts each key against the live constraint.

**Ordering is a foreign-key requirement, not a preference.** `tickers`
before everything that references it, `universe` before `events` reads it
for membership. `TABLES` is applied in order and `test_sync.py` asserts
that order satisfies the real constraints rather than trusting the list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import ServingParams
from capitalscan.jobs import db_io
from capitalscan.jobs.ingest import run_job

# Rows read from the source per round trip. Neon is over the network rather
# than a local socket, so the trip dominates and small batches are slow.
BATCH_ROWS = 5_000

# **Postgres accepts at most 65,535 bound parameters in one statement**, and
# SQLAlchemy's `insertmanyvalues` splits a multi-row INSERT into pages of
# 1,000 rows by default. That default is a latent overflow for any table
# wider than 65 columns:
#
#     events   75 columns x 1,000 rows = 75,000 parameters   FAILS
#     bars     14 columns x 1,000 rows = 14,000              fine
#
# `events` is 75 columns, so the third sync attempt died on it after seven
# minutes of successfully copying the narrow tables. The failure is a
# driver-level `OperationalError` reading "number of parameters must be
# between 0 and 65535", which names neither the table nor the page size.
#
# Chunked here rather than through SQLAlchemy's `insertmanyvalues_page_size`,
# which an `ON CONFLICT DO UPDATE` does not honour — setting it on the
# engine changed nothing and `events` failed identically. Computing the
# batch from the frame's own width is also self-adjusting: a column added
# to `events` shrinks the batch instead of breaking the sync.
MAX_BIND_PARAMS = 60_000


def _rows_per_batch(n_columns: int) -> int:
    """How many rows fit in one statement, given the table's width.

    `MAX_BIND_PARAMS` is 60,000 against Postgres's hard 65,535, leaving
    headroom for the `ON CONFLICT DO UPDATE SET` clause, which binds no
    parameters today but is one refactor away from doing so.
    """
    if n_columns <= 0:
        return BATCH_ROWS
    return max(1, min(BATCH_ROWS, MAX_BIND_PARAMS // n_columns))


@dataclass(frozen=True)
class SyncTable:
    """One table's subset, as a query and a conflict key.

    `sql` is a full SELECT rather than a WHERE fragment so a table that
    needs a join to be scoped — `bars` and `indicators` are scoped by
    trade-universe membership, which lives in another table — can express
    it without this module growing a query builder.
    """

    name: str
    sql: str
    key: tuple[str, ...]


def _tables(cutoff: date, config_hash: str) -> tuple[SyncTable, ...]:
    """The serving subset, in foreign-key order.

    `cutoff` bounds the three large tables and nothing else. Reference data
    — tickers, the calendar, the universe evaluations — is small enough
    that trimming it would trade a rounding error in size for a class of
    bug where a chart resolves a date the calendar no longer knows.
    """
    return (
        SyncTable("tickers", "SELECT * FROM tickers", ("ticker",)),
        SyncTable("trading_days", "SELECT * FROM trading_days", ("d",)),
        SyncTable("market_days", "SELECT * FROM market_days", ("ts",)),
        SyncTable("universe", "SELECT * FROM universe", ("ticker", "as_of")),
        SyncTable("serving_config", "SELECT * FROM serving_config", ("only_row",)),
        # Scoped by trade-universe membership rather than by ticker list:
        # the deployed chart only ever draws a name the screener can show.
        SyncTable(
            "bars",
            "SELECT b.* FROM bars b WHERE b.interval = '1d' AND b.ts >= :cutoff "
            "AND EXISTS (SELECT 1 FROM universe u WHERE u.ticker = b.ticker AND u.in_trade)",
            ("ticker", "ts", "interval"),
        ),
        SyncTable(
            "indicators",
            "SELECT i.* FROM indicators i WHERE i.interval = '1d' AND i.ts >= :cutoff "
            "AND EXISTS (SELECT 1 FROM universe u WHERE u.ticker = i.ticker AND u.in_trade)",
            ("ticker", "ts", "interval"),
        ),
        # **`runs`, narrowed to what the foreign key needs.** ADR 053 keeps
        # this table local and that is still right: 1,057 rows of job
        # params, most of them ingests the serving store has no use for.
        # But `events.run_id` references it, so shipping zero rows makes
        # every event insert fail the constraint.
        #
        # Six rows, measured — the backtests and event runs that produced
        # the synced window. That is what invariant 6 asks for: "every
        # generated row carries `run_id` and `git_sha`", which is only true
        # if the id resolves. Dropping the FK on serving instead would make
        # the two schemas differ, and ADR 053's "same migrations applied to
        # both" is the property `test_schema_drift.py` checks.
        #
        # Ordered before `events` because that is what a foreign key means.
        SyncTable(
            "runs",
            "SELECT * FROM runs WHERE run_id IN ("
            "  SELECT DISTINCT run_id FROM events"
            "   WHERE config_hash = :config_hash AND entry_kind IN ('next_open', 'touch')"
            "     AND signal_date >= :cutoff AND run_id IS NOT NULL)",
            ("run_id",),
        ),
        # **One config, two grains.** `v_screen` reads `next_open` and
        # `v_screen_live` reads `touch`; shipping one would leave a route
        # silently empty. The other 21 config hashes are the sweep and stay
        # local.
        SyncTable(
            "events",
            "SELECT * FROM events WHERE config_hash = :config_hash "
            "AND entry_kind IN ('next_open', 'touch') AND signal_date >= :cutoff",
            ("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
        ),
        SyncTable(
            "signal_reports",
            "SELECT * FROM signal_reports WHERE fired_at >= :cutoff",
            ("id",),
        ),
        SyncTable("cell_stats", "SELECT * FROM cell_stats", ("cell_id", "config_hash")),
        SyncTable("benchmarks", "SELECT * FROM benchmarks", ("id",)),
        SyncTable("predictions", "SELECT * FROM predictions", ("id",)),
        SyncTable("positions", "SELECT * FROM positions", ("id",)),
    )


def serving_engine() -> Engine:
    """An engine against `DATABASE_URL_SERVING`.

    Raises rather than falling back to the research URL. Every other
    resolver in this codebase falls back with a warning, and that is right
    for a read: the worst case is a developer reading local data. Here the
    worst case is a *write* — a sync that silently upserted the serving
    subset back into the research store, on top of the rows it just read.
    """
    import os

    db_io._load_env()
    url = os.environ.get("DATABASE_URL_SERVING", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL_SERVING is not set. `cscan sync` writes to the cloud "
            "serving store (ADR 053) and will not fall back to the research "
            "database, because the fallback would write research rows onto "
            "themselves. Set it in .env.local."
        )
    return db_io.get_engine(url)


def cutoff_date(sp: ServingParams | None = None, today: date | None = None) -> date:
    """The oldest date the serving store carries.

    Calendar years rather than trading days: this bounds a *download*, not
    a measurement, and a reader asking for "three years" means the calendar
    kind. Nothing statistical is computed from the result.
    """
    sp = sp or ServingParams()
    return (today or date.today()) - timedelta(days=365 * sp.history_years)


@dataclass
class SyncReport:
    rows: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def run_sync(
    source: Engine | None = None,
    target: Engine | None = None,
    sp: ServingParams | None = None,
    today: date | None = None,
    config_hash: str | None = None,
) -> SyncReport:
    """Copy the serving subset from `source` to `target`.

    Upserts rather than truncating. A truncate-and-reload would leave the
    served site empty for the duration of the load — brief, but the failure
    mode is that an interrupted sync leaves it empty *until the next one*,
    and a public page showing no signals is indistinguishable from a day
    when nothing fired.

    **Never deletes.** A row that leaves the subset — an event ageing past
    the cutoff — stays in the serving store until someone prunes it
    deliberately. That is the safe direction: the alternative is a bug in
    the cutoff arithmetic silently emptying the served history.
    """
    source = source or db_io.get_engine()
    target = target or serving_engine()
    cutoff = cutoff_date(sp, today)

    with run_job(source, "sync", {"cutoff": str(cutoff)}) as report:
        if config_hash is None:
            with source.connect() as conn:
                config_hash = conn.execute(
                    text("SELECT current_setting('capitalscan.default_config_hash', true)")
                ).scalar_one()

        rows: dict[str, int] = {}
        for table in _tables(cutoff, str(config_hash)):
            frame = pd.read_sql(
                text(table.sql),
                source,
                params={"cutoff": cutoff, "config_hash": config_hash},
            )
            written = 0
            per_batch = _rows_per_batch(len(frame.columns))
            for start in range(0, len(frame), per_batch):
                batch = frame.iloc[start : start + per_batch]
                written += db_io.upsert(
                    target, table.name, batch.to_dict("records"), list(table.key)
                )
            rows[table.name] = written

        _pin_config_hash(target, str(config_hash))
        report.rows_written = sum(rows.values())
    return SyncReport(rows=rows)


def _pin_config_hash(target: Engine, config_hash: str) -> None:
    """Set `capitalscan.default_config_hash` on the serving database.

    **Without this a freshly synced store serves zero rows**, silently.
    Every screener and statistics view filters on
    `current_setting('capitalscan.default_config_hash', true)`, and the
    `true` makes it return NULL rather than raising when unset — so
    `config_hash = NULL` matches nothing and `/` renders an empty screener
    that looks exactly like a quiet day.

    ADR 100 records the GUC as a manual step: "must be re-pinned by hand —
    no migration records it." That is right for the research database,
    where a human decides when a new config becomes the default. It is
    wrong here: the sync has just copied rows *for one specific hash*, so
    it already knows the only answer that makes the copy readable, and
    leaving a human to remember it is leaving the serving store broken by
    default.

    `ALTER DATABASE` applies to new connections, not the one issuing it,
    which is why the caller verifies through a fresh connect rather than
    reading it back here.
    """
    with target.begin() as conn:
        database = conn.execute(text("SELECT current_database()")).scalar_one()
        # Identifier, so it cannot be a bound parameter. `database` comes
        # from the server itself and the hash is 16 hex characters from
        # `jobs.config.config_hash`; both are checked before interpolation.
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise ValueError(f"refusing to ALTER DATABASE {database!r}: unexpected name")
        if not re.fullmatch(r"[0-9a-f]{16}", config_hash):
            raise ValueError(f"refusing to pin {config_hash!r}: not a config hash")
        conn.execute(
            text(
                f'ALTER DATABASE "{database}" SET capitalscan.default_config_hash = :h'.replace(
                    ":h", f"'{config_hash}'"
                )
            )
        )


def describe(sp: ServingParams | None = None, today: date | None = None) -> dict[str, Any]:
    """What a sync would carry, without doing it. Used by `--dry-run`."""
    return {"cutoff": cutoff_date(sp, today), "tables": [t.name for t in _tables(date.min, "")]}
