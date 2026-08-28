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

**The live session is not in the nightly cut, and ADR 153 is why that is
still right.** `bars_live` and `quotes_live` hold today's partial candle
and last quote, rewritten every five minutes by the poller. A *nightly*
copy would give the deployed site a price frozen at whenever the sync ran
and label it live — the exact failure ADR 131 and ADR 134 fixed, and worse
remotely because nobody there can see the poller is not running.

That reasoning is about the copy **frequency**, not about the tables. So
they are absent from `_tables()` below and present in `_live_tables()`,
which `run_live_sync` pushes after every poll tick. The serving store is
then in the position the workstation is already in: ADR 131's 45-second
client poll and ADR 134's session-hours guard both live in the view and
API layers and apply unchanged, and `poller_sessions` ships as a heartbeat
so a quiet session is distinguishable from a dead poller.

Adding either table to `_tables()` would reintroduce the original bug.
`test_sync_live.py::test_the_nightly_cut_still_excludes_the_live_session`
is what holds that line.

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

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

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
        # **Keyed on the hash too, and scoped to it.** `universe` gained
        # `config_hash` in d4a17c93f60b so ablation arms can coexist locally.
        # Serving reads exactly one generation, so shipping the others would
        # copy rows no query there can reach -- and shipping them under the
        # old two-column key would collapse them onto each other.
        SyncTable(
            "universe",
            "SELECT * FROM universe WHERE config_hash = :config_hash",
            ("ticker", "as_of", "config_hash"),
        ),
        SyncTable("serving_config", "SELECT * FROM serving_config", ("only_row",)),
        # Scoped by trade-universe membership rather than by ticker list:
        # the deployed chart only ever draws a name the screener can show.
        # `GREATEST(:cutoff, :bars_from)` -- the cutoff is the history
        # boundary and `bars_from` is the incremental one. Whichever is
        # later wins, so a NULL `bars_from` (an empty or unknown target)
        # degrades to the full cutoff pass rather than to nothing.
        SyncTable(
            "bars",
            "SELECT b.* FROM bars b WHERE b.interval = '1d' "
            "AND b.ts >= GREATEST(:cutoff, COALESCE(CAST(:bars_from AS date), :cutoff)) "
            "AND EXISTS (SELECT 1 FROM universe u WHERE u.ticker = b.ticker AND u.in_trade "
            "AND u.config_hash = :config_hash)",
            ("ticker", "ts", "interval"),
        ),
        SyncTable(
            "indicators",
            "SELECT i.* FROM indicators i WHERE i.interval = '1d' "
            "AND i.ts >= GREATEST(:cutoff, COALESCE(CAST(:indicators_from AS date), :cutoff)) "
            "AND EXISTS (SELECT 1 FROM universe u WHERE u.ticker = i.ticker AND u.in_trade "
            "AND u.config_hash = :config_hash)",
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
            "AND entry_kind IN ('next_open', 'touch') "
            "AND signal_date >= GREATEST(:cutoff, COALESCE(CAST(:events_from AS date), :cutoff))",
            ("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
        ),
        SyncTable(
            "signal_reports",
            "SELECT * FROM signal_reports WHERE "
            "fired_at >= GREATEST(:cutoff, COALESCE(CAST(:reports_from AS date), :cutoff))",
            ("id",),
        ),
        # Scoped to the config being served. Unfiltered, these shipped every
        # hash the research store had ever held, and `run_sync` never
        # deletes -- so each rebuild left another generation on serving
        # forever. That is what filled the 512 MB free tier on 2026-08-21,
        # where 90% of the events belonged to a hash nothing reads.
        #
        # Safe because the serving store reads exactly one hash:
        # `serving_config` pins it and `web/lib/db.ts` sets it on every
        # connection (ADR 115), so other generations are unreachable by any
        # query the site makes.
        SyncTable(
            "cell_stats",
            "SELECT * FROM cell_stats WHERE config_hash = :config_hash",
            ("cell_id", "config_hash"),
        ),
        SyncTable(
            "benchmarks",
            "SELECT * FROM benchmarks WHERE config_hash = :config_hash",
            ("id",),
        ),
        SyncTable("predictions", "SELECT * FROM predictions", ("id",)),
        SyncTable("positions", "SELECT * FROM positions", ("id",)),
    )


logger = logging.getLogger(__name__)


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


def _refuse_self_sync(source: Engine, target: Engine) -> None:
    """Raise if the serving URL resolves to the research database.

    `serving_engine()` already refuses to *fall back* to research. It cannot
    tell that an explicitly-set `DATABASE_URL_SERVING` happens to point
    there — and on a workstation that also hosts research, the natural typo
    is `localhost`, which is a valid URL to the wrong database.

    The failure is silent in the worst way. Every row upserts onto itself,
    every tick reports success, and the deployed site simply never changes.
    ADR 153 makes this a per-tick operation, so a wrong URL would look
    healthy 78 times a session.

    **Host and database together.** Either alone gives a false positive:
    research and serving legitimately share a database *name* on different
    hosts, and a single host legitimately carries both under different
    names.

    Loopback spellings are normalised because `localhost` and `127.0.0.1`
    are the same server and a guard that can be defeated by spelling is
    not a guard. Beyond that this stays deliberately literal — resolving
    DNS to compare addresses would put a network call in a boot path to
    catch a case that has never occurred.
    """
    loopback = {"localhost", "127.0.0.1", "::1", None}

    def _key(engine: Engine) -> tuple[str, str]:
        url = engine.url
        host = "localhost" if url.host in loopback else str(url.host)
        return host, str(url.database)

    if _key(source) == _key(target):
        host, database = _key(target)
        raise RuntimeError(
            f"DATABASE_URL_SERVING resolves to the research database "
            f"({host}/{database}). A sync writes the serving subset onto its "
            "own source: every row upserts onto itself, every run reports "
            "success, and the deployed site never changes. Point it at the "
            "serving host (ADR 153)."
        )


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


# Days re-shipped below the target's own watermark. The sync upserts, so an
# overlap costs bandwidth and nothing else, and it absorbs the cases a bare
# watermark misses: a row corrected after the fact, a night that failed
# halfway, a bar restated by the vendor.
SYNC_OVERLAP_DAYS = 7


def _incremental_bounds(
    target: Engine, config_hash: str, overlap_days: int = SYNC_OVERLAP_DAYS
) -> dict[str, date | None]:
    """How far back each large table needs to be re-shipped.

    **Derived from the target, not from a fixed window.** A constant
    "last 7 days" is wrong exactly when it matters -- a Pi that has been
    off for a fortnight would get seven days of rows and a permanent hole,
    with no error. Reading the target's own newest row means the window is
    however far behind it actually is, plus the overlap.

    `None` means "no incremental bound": the table is empty on the target,
    or holds nothing for this config, so the full `cutoff` pass is the only
    correct answer. That is also what makes a first sync, a rebuilt serving
    store and a config change work without a flag.

    A failure to read is `None` too. Being slow is recoverable; guessing a
    watermark and shipping a subset is not.
    """
    queries = {
        "bars_from": "SELECT max(ts)::date FROM bars WHERE interval = '1d'",
        "indicators_from": "SELECT max(ts)::date FROM indicators WHERE interval = '1d'",
        "events_from": ("SELECT max(signal_date) FROM events WHERE config_hash = :config_hash"),
        "reports_from": "SELECT max(fired_at)::date FROM signal_reports",
    }
    out: dict[str, date | None] = {}
    for name, sql in queries.items():
        try:
            with target.connect() as conn:
                got = conn.execute(text(sql), {"config_hash": config_hash}).scalar_one_or_none()
        except SQLAlchemyError:
            logger.warning("could not read the %s watermark; falling back to a full pass", name)
            got = None
        out[name] = (got - timedelta(days=overlap_days)) if got is not None else None
    return out


# The poller's **durable** output, as opposed to its provisional output.
# `_sweep_provisional_poll_rows` deletes only `events` rows whose `run_id`
# begins `poll`, and explicitly preserves `signal_reports`; it never touches
# `poller_sessions`. These two are the record a past date's fired-at
# timestamps come from, and ADR 084 has Phase 6 reading
# `poller_sessions.coverage_pct` to tell "no coverage" from "no signals".
#
# **`runs` is here because `events.run_id` is a foreign key.** A poller run
# row is written on serving by `run_job` when the poller starts there, and
# research needs it before any later job can reference that run. Audited
# 2026-08-28: `poll.py` writes six tables, not the four first recorded --
# `signal_reports` goes through `db_io.append` rather than `upsert`, and
# `runs` through the `run_job` context manager rather than a direct call,
# so a grep for `upsert` finds neither.
_LIVE_DURABLE_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("runs", "started_at >= :since AND job = 'poll'", ("run_id",)),
    ("signal_reports", "fired_at >= :since", ("id",)),
    ("poller_sessions", "session_date >= :since", ("session_date",)),
)


def pull_live_records(
    source: Engine | None = None,
    target: Engine | None = None,
    since: date | None = None,
    lookback_days: int = 7,
) -> dict[str, int]:
    """Copy the poller's durable rows **serving -> research** (ADR 158).

    The reverse of `run_sync`, and deliberately narrow.

    **Why it is needed.** With the poller writing serving directly, its two
    permanent tables are *born* there. Research is where analysis happens,
    so without this pull it quietly stops accumulating them -- and the gap
    is invisible until someone queries data that was never written, which
    is the worst shape a data defect can take.

    **Only the durable two.** `events` rows from the poller are provisional
    and the nightly sweep removes them; pulling those back would resurrect
    exactly what nightly just judged unreliable. `bars_live` and
    `quotes_live` are per-tick scratch that research has no reader for.

    **Upsert, not replace.** Research may already hold rows for a date --
    from before the poller moved, or from a re-run. An insert would raise on
    the key and a delete-then-insert would lose anything the pull did not
    cover.

    Bounded to `lookback_days` because this runs nightly and the whole
    history is neither needed nor cheap; the overlap absorbs a night that
    failed. `since` overrides it for a manual catch-up.
    """
    source = source or serving_engine()
    target = target or db_io.get_engine()
    floor = since or (date.today() - timedelta(days=lookback_days))

    pulled: dict[str, int] = {}
    for name, predicate, key in _LIVE_DURABLE_TABLES:
        frame = pd.read_sql(
            text(f"SELECT * FROM {name} WHERE {predicate}"),  # noqa: S608 - fixed names
            source,
            params={"since": floor},
        )
        pulled[name] = db_io.copy_upsert(target, name, frame, list(key)) if not frame.empty else 0
    return pulled


def run_sync(
    source: Engine | None = None,
    target: Engine | None = None,
    sp: ServingParams | None = None,
    today: date | None = None,
    config_hash: str | None = None,
    incremental: bool = False,
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
    _refuse_self_sync(source, target)
    cutoff = cutoff_date(sp, today)

    with run_job(source, "sync", {"cutoff": str(cutoff)}) as report:
        if config_hash is None:
            with source.connect() as conn:
                config_hash = conn.execute(
                    text("SELECT current_setting('capitalscan.default_config_hash', true)")
                ).scalar_one()

        # **Full by default; `incremental=True` is the nightly path.**
        # `cscan sync` means "copy the serving subset", and that is the
        # command you reach for after a rebuild, a reflash or a config
        # change -- it must not quietly ship a window.
        #
        # Nightly is the case that cannot afford it. A full pass shipped
        # 7,469,519 rows in 114.2 minutes on 2026-08-26 to deliver ~3,875
        # that had changed: a 1,900x amplification and two thirds of the
        # job. `cutoff` stopped bounding anything when
        # `ServingParams.history_years` went from 3 to 30, which was right
        # on its own and turned this into a whole-table copy.
        #
        # Even incremental, an empty table or an unseen `config_hash`
        # produces NULL bounds and falls back to the full `cutoff` pass, so
        # the fast path cannot leave a new serving store half-populated.
        bounds: dict[str, date | None] = (
            _incremental_bounds(target, str(config_hash))
            if incremental
            else {k: None for k in ("bars_from", "indicators_from", "events_from", "reports_from")}
        )
        logger.info(
            "sync mode=%s bounds=%s",
            "incremental" if incremental else "full",
            {k: str(v) for k, v in bounds.items()},
        )

        rows: dict[str, int] = {}

        # **One snapshot for every table (2026-08-25).** Each `read_sql`
        # against the Engine used to open its own connection, so a sync
        # that ran 1h45m read fourteen tables at fourteen different
        # moments. Measured on the Pi right after one: VOO and IBIT had
        # indicators, no bars, and no `in_trade` universe row -- `universe`
        # was copied at ~03:05 before ADR 154 made ETFs eligible, `bars` at
        # ~03:30 with an `EXISTS (... u.in_trade)` filter evaluated against
        # the *source*, and `indicators` at ~04:12 after ADR 154 landed.
        #
        # **That state never existed in research.** The copy manufactured
        # it, and a ticker with indicators but no bars is incoherent rather
        # than merely stale -- no downstream query can tell.
        #
        # REPEATABLE READ, not the default READ COMMITTED, which takes a
        # fresh snapshot per *statement*: sharing the connection alone
        # would fix nothing. The snapshot is established by the first
        # statement in the transaction and every later read sees it.
        # Postgres needs no locks for this -- readers never block writers
        # under MVCC -- so the cost is one long-lived connection.
        #
        # READ ONLY because a sync must never write to research; an
        # accidental write then fails at the database instead of
        # succeeding quietly. The target is written outside this
        # transaction, which is the point of holding it open.
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as snapshot:
            snapshot.execute(text("SET TRANSACTION READ ONLY"))
            for table in _tables(cutoff, str(config_hash)):
                # pandas-stubs types `params` values as non-optional; a NULL
                # bound is exactly how "no incremental floor" is expressed and
                # psycopg binds it fine. The mismatch is the stub, not the call
                # -- same as `_read_corporate_actions`' list binding.
                frame = pd.read_sql(
                    text(table.sql),
                    snapshot,
                    params={"cutoff": cutoff, "config_hash": config_hash, **bounds},  # type: ignore[arg-type]
                )
                # **`COPY` into a staging table, not row dicts.** Profiled
                # during a full sync on 2026-08-26: the Pi was 76% idle (load
                # 1.11 of four cores, SD card 15% utilised) while this process
                # held 894 MB and 53.8% of one core. The constraint was
                # `to_dict("records")` building 7.4M Python dicts and SQLAlchemy
                # re-binding each one -- three full representations of every row
                # to move it between two databases.
                #
                # `_rows_per_batch` chunking is gone with it: `COPY` streams, so
                # there are no bind parameters to stay under `MAX_BIND_PARAMS`,
                # and the whole table lands in one transaction instead of one
                # per 1,000 rows.
                #
                # Writes the *target* from inside the source's read
                # transaction, deliberately: the snapshot must outlive
                # every read, and serving is a different database.
                rows[table.name] = db_io.copy_upsert(target, table.name, frame, list(table.key))

        # **Reset the target's sequences (2026-08-28).** Every row above was
        # copied with its own id, and an INSERT that supplies an explicit id
        # does not advance the sequence -- so serving ends a sync holding
        # rows its sequences have never seen.
        #
        # That was invisible until the poller moved to write serving (ADR
        # 158), because until then nothing ever *inserted* there. On the
        # first live session the Pi's poller inserted a `signal_reports`
        # row, got id 21, and id 21 already existed: serving held 1,829 rows
        # with its sequence still at 21. `events_id_seq` was at 21 too,
        # against a max of 39,167,955 -- that one would have crashed on the
        # first live event rather than the first report.
        #
        # Derived from the catalogue rather than a hardcoded list, which
        # would go stale the moment a table gains a serial. Skipped for an
        # empty table because `setval(seq, 0)` is an error in Postgres.
        #
        # Belongs here rather than in a one-off repair: every sync copies
        # research's ids again, so a sequence fixed by hand goes stale on
        # the next nightly.
        with target.begin() as conn:
            conn.execute(
                text("""
                DO $$
                DECLARE r record; n bigint;
                BEGIN
                  FOR r IN
                    SELECT c.oid::regclass AS tbl, a.attname AS col,
                           pg_get_serial_sequence(c.oid::regclass::text, a.attname) AS seq
                      FROM pg_class c
                      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0
                     WHERE c.relkind = 'r'
                       AND pg_get_serial_sequence(c.oid::regclass::text, a.attname) IS NOT NULL
                  LOOP
                    EXECUTE format('SELECT coalesce(max(%I),0) FROM %s', r.col, r.tbl) INTO n;
                    IF n > 0 THEN PERFORM setval(r.seq, n); END IF;
                  END LOOP;
                END $$;
                """)
            )

        # Assigned *before* the pin, which can raise. Measured 2026-08-21:
        # a pin failure discarded the count and recorded rows_written = 0
        # for a sync that had committed ~100,000 rows.
        report.rows_written = sum(rows.values())

        # The pin is a convenience and must not fail a sync that worked.
        # ADR 115 moved the serving views onto the `serving_config` table,
        # written above; the GUC only helps a human in `psql`. Neon and
        # other managed Postgres refuse `ALTER DATABASE ... SET` to
        # non-superuser roles, and that is not a reason to report failure.
        #
        # Only this error is tolerated. `_pin_config_hash` raises
        # `ValueError` when an identifier does not look like one, and
        # swallowing everything here would hide that guard.
        # SQLAlchemy wraps the driver error, so the psycopg class arrives on
        # `.orig` rather than as the raised type. Catching
        # `InsufficientPrivilege` directly never fires.
        try:
            _pin_config_hash(target, str(config_hash))
        except ProgrammingError as err:
            if not isinstance(err.orig, InsufficientPrivilege):
                raise
            logger.warning(
                "could not pin capitalscan.default_config_hash on the serving "
                "database: the role lacks ALTER DATABASE. The sync itself "
                "succeeded and the serving views read the `serving_config` "
                "table (ADR 115), not this GUC, so nothing is broken. Set it "
                "by hand if you want `psql` sessions to default to %s.",
                config_hash,
            )
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


@dataclass(frozen=True)
class LiveWatermark:
    """How far the live sync has already pushed.

    `events` and `signal_reports` have no `updated_at`, but both `id`
    columns are bigint sequences and the poller never rewrites a key it has
    already fired (`_already_fired`). So a high-water id is an exact
    "everything below this is already on serving" marker, and each tick
    ships only what that tick produced rather than re-uploading the session
    so far.

    `bars_live` is excluded: it is keyed `(ticker, session_date)`, so a tick
    *overwrites* each ticker's row and the whole table has to ship each time
    (~450 rows). A watermark there would send each ticker once and never
    again, freezing the deployed candle at the day's first tick.

    `quotes_live` is keyed `(ticker, ts)` and therefore append-only, so it
    takes a clock watermark rather than an id one -- checked against
    `pg_constraint`, not assumed from its neighbour.
    """

    event_id: int = 0
    report_id: int = 0
    quote_ts: str | None = None


def _live_tables(chash: str, d: date, run_id: str, since: LiveWatermark) -> tuple[SyncTable, ...]:
    """The poller's own footprint, for one session, in foreign-key order.

    Deliberately not a subset of `_tables()`. That list is the *nightly*
    cut and excludes the live session for a reason its own docstring gives:
    a once-a-day copy of a five-minute table is a frozen price wearing a
    live label. This list exists because that reasoning is about the copy
    *frequency*, not about the table -- a per-tick copy puts the serving
    store in the same position the workstation is already in, with ADR
    131's 45-second client poll and ADR 134's session-hours guard applying
    unchanged because both live in the view and API layers.

    `poller_sessions` is the heartbeat and ships first, so a reader that
    sees no signals can still tell a quiet session from a dead poller.
    """
    return (
        SyncTable(
            "poller_sessions",
            "SELECT * FROM poller_sessions WHERE session_date = :d",
            ("session_date",),
        ),
        # `events.run_id` is a foreign key; `run_job` inserts this row on
        # entry with status 'running', so it resolves mid-session. The
        # status is corrected by the nightly's full sync.
        SyncTable("runs", "SELECT * FROM runs WHERE run_id = :run_id", ("run_id",)),
        SyncTable(
            "events",
            "SELECT * FROM events WHERE config_hash = :chash AND signal_date = :d "
            "AND entry_kind = 'touch' AND id > :since_event",
            ("config_hash", "ticker", "signal_date", "signal_type", "entry_kind"),
        ),
        SyncTable(
            "signal_reports",
            "SELECT * FROM signal_reports WHERE fired_at >= :d AND id > :since_report",
            ("id",),
        ),
        SyncTable(
            "bars_live",
            "SELECT * FROM bars_live WHERE session_date = :d",
            ("ticker", "session_date"),
        ),
        SyncTable(
            "quotes_live",
            "SELECT * FROM quotes_live WHERE ts >= :d "
            "AND (CAST(:since_quote AS timestamptz) IS NULL "
            "OR ts > CAST(:since_quote AS timestamptz))",
            ("ticker", "ts"),
        ),
    )


def run_live_sync(
    chash: str,
    d: date,
    run_id: str,
    since: LiveWatermark | None = None,
    source: Engine | None = None,
    target: Engine | None = None,
) -> tuple[SyncReport, LiveWatermark]:
    """Push one poll tick's output to serving (ADR 153).

    **No `run_job` wrapper.** The poll's own run row already accounts for
    the work, and ~78 ticks a session would otherwise write 78 rows to
    `runs` describing a copy rather than a computation.

    **No `_pin_config_hash`.** The GUC does not change intraday and the pin
    is an `ALTER DATABASE` per tick for nothing.

    Idempotent: re-running a tick upserts the same rows to the same keys and
    the watermark advances past them, so a repeat is a no-op and a missed
    tick is repaired by the next one rather than needing a catch-up path.

    Returns the report and the advanced watermark. The caller holds the
    watermark across ticks; it deliberately does not live in this module,
    because a module-level one would leak between sessions in a process
    that polls two days in a row.
    """
    since = since or LiveWatermark()
    source = source or db_io.get_engine()
    target = target or serving_engine()
    _refuse_self_sync(source, target)

    params: dict[str, Any] = {
        "chash": chash,
        "d": d,
        "run_id": run_id,
        "since_event": since.event_id,
        "since_report": since.report_id,
        "since_quote": since.quote_ts,
    }

    rows: dict[str, int] = {}
    high = {"events": since.event_id, "signal_reports": since.report_id}
    quote_ts = since.quote_ts
    for table in _live_tables(chash, d, run_id, since):
        frame = pd.read_sql(text(table.sql), source, params=params)
        written = 0
        per_batch = _rows_per_batch(len(frame.columns))
        for start in range(0, len(frame), per_batch):
            batch = frame.iloc[start : start + per_batch]
            written += db_io.upsert(target, table.name, batch.to_dict("records"), list(table.key))
        rows[table.name] = written
        if table.name in high and not frame.empty:
            high[table.name] = max(high[table.name], int(frame["id"].max()))
        if table.name == "quotes_live" and not frame.empty:
            quote_ts = str(frame["ts"].max())

    return SyncReport(rows), LiveWatermark(high["events"], high["signal_reports"], quote_ts)


def describe(sp: ServingParams | None = None, today: date | None = None) -> dict[str, Any]:
    """What a sync would carry, without doing it. Used by `--dry-run`."""
    return {"cutoff": cutoff_date(sp, today), "tables": [t.name for t in _tables(date.min, "")]}
