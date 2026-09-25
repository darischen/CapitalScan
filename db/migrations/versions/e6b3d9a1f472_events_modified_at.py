"""events.modified_at: a watermark that moves with the write, not the date

**Why.** `cscan sync --incremental` bounded `events` by the serving store's
own `max(signal_date)` minus `SYNC_OVERLAP_DAYS`. That sees forward and never
backward: any job that rewrites an event older than the overlap -- the
cosmetic backtest, `--phase finalize`'s `cofire_count`, `peak_labels`,
`path_backfill` -- changed research and never reached serving. Measured
2026-09-25: for August signals, research held 6,327 `peak_ret_10d` labels
and serving 4,025, because `peak_labels` writes a label about fourteen
calendar days after the signal and the overlap is seven.

**A trigger, not a column each writer sets.** Seven code paths write
`events` (`copy_upsert` from compute and backtest, three `UPDATE`s in
`db_io`, `finalize_cofire`, `peak_labels`, `path_backfill`, the poller).
A column that depends on every one of them remembering is the same bug
waiting for the eighth. The trigger sees all of them.

**Stamped only on a real change.** `NEW IS DISTINCT FROM OLD` with
`modified_at` and `run_id` normalised first. Every backtest re-touches its
rows and rewrites `run_id` (Ruling C4); stamping those would re-ship the
whole generation after each `weekly` for nothing. Values are deterministic
(invariant/test 3), so an unchanged re-run stamps nothing.

**NULL until touched.** No backfill: the existing `signal_date` watermark
already covers everything a NULL row could be missing, except rewrites
before this migration -- and those need the one full `cscan sync` the
backlog entry already prescribes.

**Partial index on `modified_at IS NOT NULL`.** The sync's predicate is
`modified_at >= :since`, which implies NOT NULL, so the planner can use it
and the index holds only rows written since this migration. The sync reads
it through its own `UNION ALL` arm: an `OR` against the `signal_date` arm
planned as a sequential scan of the whole table.

Applies to serving too (ADR 053: same migrations on both). There the
column carries no meaning -- the trigger restamps whatever the sync ships.

Revision ID: e6b3d9a1f472
Revises: a7c2e9f4b105
Create Date: 2026-09-25
"""

from alembic import op

revision = "e6b3d9a1f472"
down_revision = "a7c2e9f4b105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE events ADD COLUMN modified_at timestamp with time zone")
    op.execute(
        """
        CREATE FUNCTION public.events_stamp_modified_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            probe public.events;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                NEW.modified_at := now();
                RETURN NEW;
            END IF;
            probe := NEW;
            probe.modified_at := OLD.modified_at;
            probe.run_id := OLD.run_id;
            IF probe IS DISTINCT FROM OLD THEN
                NEW.modified_at := now();
            ELSE
                NEW.modified_at := OLD.modified_at;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER events_stamp_modified_at BEFORE INSERT OR UPDATE ON events "
        "FOR EACH ROW EXECUTE FUNCTION public.events_stamp_modified_at()"
    )
    op.execute(
        "CREATE INDEX events_modified_at ON events (config_hash, modified_at) "
        "WHERE modified_at IS NOT NULL"
    )
    # Without statistics the planner assumes a range predicate keeps a third
    # of the table and seq-scans all 20 GB for the sync's second arm. One
    # column, sampled, about a second: measured on the workstation copy.
    op.execute("ANALYZE events (modified_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS events_modified_at")
    op.execute("DROP TRIGGER IF EXISTS events_stamp_modified_at ON events")
    op.execute("DROP FUNCTION IF EXISTS public.events_stamp_modified_at()")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS modified_at")
