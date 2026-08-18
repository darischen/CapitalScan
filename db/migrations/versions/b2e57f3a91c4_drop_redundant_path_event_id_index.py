"""drop the redundant path_event_id index

Revision ID: b2e57f3a91c4
Revises: c5b81f2e64a7
Create Date: 2026-08-18 00:00:00.000000

`path` carries two indexes covering the same column:

    path_pkey      UNIQUE btree (event_id, day_offset)   3,865 MB
    path_event_id         btree (event_id)               1,206 MB

A B-tree on `(event_id, day_offset)` already serves every lookup, range
scan, and join on `event_id` alone, because `event_id` is its **leading**
column. A standalone index on the same leading column adds no access path
that the primary key does not already provide.

**Measured, not assumed.** `pg_stat_user_indexes` on 2026-08-17:

    path_pkey        161,784,082 scans
    path_event_id             68 scans

Six orders of magnitude apart on a table where every read goes through
`event_id`. The planner was already choosing the composite index virtually
every time, which is what the theory predicts.

**What it costs to keep.** 1,206 MB of a 25 GB database, and a write
penalty on the hottest table in the system: every `path` insert maintains
both indexes, and `path backfill` writes tens of millions of rows in a
single run.

**Why now.** The same 2026-08-17 review that scoped `path backfill` to one
`config_hash`. Both changes target the same problem — `path` was doing work
nothing read.

**Risk.** Low, and reversible. If some query really did depend on the
narrower index, the planner falls back to `path_pkey` and pays at most a
slightly wider tuple comparison; it cannot fail to find a path. `downgrade`
recreates the index exactly.

**Not CONCURRENTLY.** `DROP INDEX CONCURRENTLY` cannot run inside a
transaction block, and Alembic wraps each migration in one. A plain drop
takes a brief `ACCESS EXCLUSIVE` lock on `path`, which for a drop is
catalog work plus a file unlink rather than a table rewrite — fast even at
1.2 GB. Do not run this while `path backfill` or `path capture` is writing;
the lock would block them.
"""

from __future__ import annotations

from alembic import op

revision = "b2e57f3a91c4"
down_revision = "c5b81f2e64a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS so a database that never had the index (a fresh one built
    # from a later schema.sql) migrates cleanly rather than erroring on a
    # drop of something absent.
    op.execute("DROP INDEX IF EXISTS path_event_id")


def downgrade() -> None:
    # Recreated exactly as `db/schema.sql` declared it. Rebuilding 1.2 GB
    # of index on a populated table takes minutes and holds a lock for the
    # duration — the asymmetry is inherent to index DDL, not a defect here.
    op.execute("CREATE INDEX IF NOT EXISTS path_event_id ON public.path USING btree (event_id)")
