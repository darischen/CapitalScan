"""Every table in one sync is read from the same snapshot of the source.

**The incident, 2026-08-25.** A sync that ran 1h45m copied its fourteen
tables in foreign-key order while the research database changed underneath,
and each `pd.read_sql` opened its own connection — so each table captured a
different moment. Measured on the Pi immediately afterwards:

    QQQ    5,282 bars   5,282 indicators   30 in_trade universe rows
    SPY    5,510 bars   5,510 indicators    1
    VOO        0 bars   4,013 indicators    0
    IBIT       0 bars     656 indicators    0

VOO and IBIT had indicators, no bars, and no `in_trade` universe row. The
sequence explains it: `universe` was copied at ~03:05 (before ADR 154 made
ETFs eligible), `bars` at ~03:30 with a filter of `EXISTS (... u.in_trade)`
evaluated against the **source**, and `indicators` at ~04:12 after ADR 154
had landed. Three tables, three different databases in effect.

**That state never existed in research.** It was manufactured by the copy.
A ticker with indicators but no bars is not a stale row, it is an
incoherent one, and no downstream query can tell.

`REPEATABLE READ` on a single connection fixes it at the source: the first
statement establishes the snapshot and every later read in that transaction
sees the same one, whatever else commits meanwhile. Postgres needs no
locks for this — readers do not block writers under MVCC — so the cost is
one long-lived connection, not contention.

These tests pin the mechanism in the source. Proving the isolation
behaviourally needs two concurrent sessions against a live database, which
the integration tier may not do here (CLAUDE.md).
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import sync as sync_job

SRC = inspect.getsource(sync_job.run_sync)


class TestOneSnapshot:
    def test_the_reads_share_a_single_connection(self):
        """`pd.read_sql(..., source, ...)` hands pandas an Engine, and
        pandas then opens a connection per call — one snapshot per table,
        which is the defect."""
        assert "read_sql" in SRC
        assert "source.connect()" in SRC or "snapshot" in SRC, (
            "each read_sql against the Engine gets its own transaction; "
            "the reads must share one connection to share one snapshot"
        )

    def test_the_isolation_level_is_repeatable_read(self):
        """READ COMMITTED — the default — takes a new snapshot per
        *statement*, so sharing a connection alone changes nothing."""
        assert "REPEATABLE READ" in SRC.upper()

    def test_the_snapshot_opens_before_the_table_loop(self):
        """A snapshot established inside the loop is the same bug with
        extra words."""
        connect_at = SRC.index("REPEATABLE READ")
        loop_at = SRC.index("for table in _tables(")
        assert connect_at < loop_at

    def test_reads_are_still_bounded_by_the_same_parameters(self):
        """The snapshot must not quietly widen what is copied."""
        assert "cutoff" in SRC and "config_hash" in SRC


class TestItStaysAReader:
    def test_the_source_transaction_is_read_only(self):
        """A sync must never write to research. Declaring it read-only
        makes an accidental write fail at the database rather than
        succeed quietly."""
        assert "READ ONLY" in SRC.upper()

    def test_the_target_is_written_outside_that_transaction(self):
        """`copy_upsert` writes to serving. Holding the source snapshot
        open across those writes is intended; writing to the source
        connection is not."""
        # Matched on the argument rather than the call spelling: the call
        # became multi-line on 2026-09-01 when its frame argument gained a
        # `_drop_surrogate_id` wrapper (ADR 163).
        assert "db_io.copy_upsert(" in SRC
        assert "target," in SRC
