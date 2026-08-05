"""events giveback column

Revision ID: 699cb410d219
Revises: 8e2f5d9c0a1a
Create Date: 2026-08-04 19:00:00.000000

Session 10, task 10.5 follow-up. Adds the `giveback` label column to
`events` for real, closing a gap where `research/path_labels.py` and
`research/path_reconcile.py` already computed/compared `giveback` but no
migration had ever added the column — confirmed live (`\\d events`) to be
missing, which crashed `cscan path reconcile` with `UndefinedColumn`
before this fix (LABEL_COLUMNS temporarily dropped `giveback` to unblock
the gate; see path_reconcile.py's comment at that removal for the
full story).

Schema design:
- `events.giveback numeric(12,6)`, nullable — same type and nullability as
  the other Task 9/10 per-event label columns it sits alongside (`mfe`,
  `mae`, `capture_ratio`: see `01c32499e1b2_events.py`), since giveback is
  the same shape of quantity (a return fraction) computed the same way
  they are.
- Nullable, not backfilled by this migration: `derive_labels_from_path`
  already writes `None`/NaN giveback for an unresolved position (Session
  10's own null semantics), and every existing event row predates this
  column entirely, so NULL is the only honest value until a giveback
  backfill/write path actually populates it. Writing that population step
  is separate follow-up work, not this migration.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "699cb410d219"
down_revision: Union[str, Sequence[str], None] = "8e2f5d9c0a1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE events ADD COLUMN giveback numeric(12,6)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE events DROP COLUMN giveback")
