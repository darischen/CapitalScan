"""path provenance: run_id and computed_at

Revision ID: a1f4c7d2e903
Revises: 699cb410d219
Create Date: 2026-08-06 00:00:00.000000

ADR 034 requires `run_id` and `git_sha` on every generated row. `path`
shipped (migration 8e2f5d9c0a1a, Task 10.1) with `event_id`, `day_offset`,
`favorable`, `adverse`, `terminal` and nothing else. Every other generated
table already carries provenance — `events.run_id`, `indicators.run_id`,
`bars.run_id`, `cell_stats.run_id`, `benchmarks.run_id` — so this was an
omission in Task 10.1's "minimum shape needed for label derivation", not a
decision to exempt `path`.

It has already cost real time. Reconciliation findings 3 and 7 (Task 10.4)
both needed to know which `bars` snapshot a given path row was computed
against. Neither could read it from `path`. Both were resolved by
cross-referencing `bars.run_id`, inferring the answer, and hardcoding a
ticker list plus a specific `run_id` into `research/path_reconcile.py` as
standing evidence.

Both columns are NULLABLE, and that is the point of this migration rather
than a shortcut in it. `run_path_capture` writes through
`ON CONFLICT (event_id, day_offset) DO UPDATE`, so a row that has been
rewritten by a later capture no longer carries any trace of the run that
first created it. The origin of the 27.6M rows already in the table is
genuinely unrecoverable. Backfilling them with a guessed or synthetic
`run_id` would produce a column that looks like provenance and is not —
precisely the failure ADR 034 exists to prevent. NULL here means "written
before provenance existed," which is true and checkable; anything else
would be a fabrication.

`ADD COLUMN` with no DEFAULT is a catalog-only change in Postgres 11+, so
neither statement rewrites the table. On 27.6M rows that is the difference
between a moment and a long ACCESS EXCLUSIVE lock against a live writer.

Semantics of the two columns, once written (see
`research/path_backfill.py`): both are rewritten by the upsert on every
conflict, so they describe the run that produced the row's *current*
values, not the run that first inserted the row. That is the useful
question — "which bars snapshot is this number from" — and it is the one
findings 3 and 7 could not answer.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f4c7d2e903"
down_revision: Union[str, Sequence[str], None] = "699cb410d219"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE path ADD COLUMN run_id text")
    op.execute("ALTER TABLE path ADD COLUMN computed_at timestamptz")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE path DROP COLUMN computed_at")
    op.execute("ALTER TABLE path DROP COLUMN run_id")
