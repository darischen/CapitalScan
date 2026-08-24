"""ADR 149: record watch membership on the detection.

Adds `events.in_watch`, mirroring `events.in_trade`.

**Why a column and not a join.** ADR 122 decided membership is recorded on
the detection rather than filtered at write time, and `in_trade` already
follows that. Without the mirror, a watch event is indistinguishable from
the 1,807 out-of-trade rows `cscan events` writes under ADR 122 — both have
`in_trade = false` — so the only way to tell them apart would be a join
back to `universe` on `(ticker, as_of <= signal_date)`. That join is also
what the restated `entry_price` invariant would need on every check, which
makes an invariant expensive enough that it stops being checked.

**Nullable, no server default**, following `c3f91a70b8d4` and
`a4e91c7b2f58`: `false` on all 863,489 existing rows would assert every one
was evaluated for watch membership and found outside it. NULL is the truth
until the backtest rewrites them.

**No CHECK pairing `in_trade` and `in_watch` here**, deliberately, unlike
the `universe` migration. On `universe` the two are computed together from
one criteria dict in one function, so a violation is a bug in one place. On
`events` they are copied from whichever universe row applied at
`signal_date`, and rows written before this migration carry NULL — a CHECK
would reject the backfill of exactly the rows it is meant to guard. The
disjointness is enforced at the source instead.

Revision ID: b7c2f0d5a913
Revises: a4e91c7b2f58
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c2f0d5a913"
down_revision = "a4e91c7b2f58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `ADD COLUMN ... NULL` with no default does not rewrite the table, so
    # this is a catalogue change on 863,489 rows rather than a scan.
    op.add_column("events", sa.Column("in_watch", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "in_watch")
