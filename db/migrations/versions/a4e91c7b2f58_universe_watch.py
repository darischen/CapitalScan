"""ADR 149: the watch universe, a sibling of `in_trade`.

Adds `universe.in_watch` and `universe.watch_reason`.

**Both nullable, with no server default**, following `c3f91a70b8d4`'s
reasoning verbatim: a `DEFAULT false` would populate all 51,837 existing
rows with False, asserting a negative that was never computed. NULL means
"not yet evaluated", which is the truth until `cscan universe` re-runs over
all 66 quarters and writes a real value.

**The CHECK constraint is the point of doing this by hand.** Three
invariants ADR 149 states in prose become impossible to violate:

- a watched row must say why (`in_watch` implies a reason),
- an unwatched row must not (no orphan reason),
- and `in_trade` and `in_watch` are disjoint, so "which population is this
  row in" always has exactly one answer.

Without the constraint the third is a convention that holds until one
writer forgets. `universe` is 51,837 rows, so validation is instant.

Revision ID: a4e91c7b2f58
Revises: c3f91a70b8d4
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a4e91c7b2f58"
down_revision = "c3f91a70b8d4"
branch_labels = None
depends_on = None

_WATCH_REASONS = ("history", "pullback")

_CHECK_NAME = "universe_watch_consistent"
_CHECK = (
    "((in_watch IS NOT TRUE AND watch_reason IS NULL) "
    " OR (in_watch IS TRUE AND watch_reason IN ('history', 'pullback'))) "
    "AND NOT (in_trade AND in_watch IS TRUE)"
)


def upgrade() -> None:
    op.add_column("universe", sa.Column("in_watch", sa.Boolean(), nullable=True))
    op.add_column("universe", sa.Column("watch_reason", sa.Text(), nullable=True))
    op.create_check_constraint(_CHECK_NAME, "universe", _CHECK)


def downgrade() -> None:
    # Constraint first: dropping a column that a CHECK references fails.
    op.drop_constraint(_CHECK_NAME, "universe", type_="check")
    op.drop_column("universe", "watch_reason")
    op.drop_column("universe", "in_watch")
