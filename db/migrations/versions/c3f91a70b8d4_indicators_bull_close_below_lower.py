"""ADR 144: indicators.bull_close_below_lower

The long-side mirror of `bear_close_above_upper` (ADR 108/109):

    close > open  AND  close <= bb_lower[t]

Nullable boolean, matching its sibling exactly. **Null is not False here.**
The flag is undefined through `bollinger`'s 272-bar warmup, and invariant 4
says an absent value is dropped and recorded rather than filled -- a False
through warmup would read as a measured negative, which is a different claim
from "no band yet". `core.signals._close_flag` maps both null and absent to
"did not fire" at read time, so the column carries the honest value and the
reader carries the interpretation.

No backfill, and that is deliberate. Adding the column leaves it NULL for
every existing row, which is the correct state until `cscan indicators`
recomputes: the flag has never been evaluated for those bars, and writing
False would assert it had been. The recompute is a separate, explicit step.

Revision ID: c3f91a70b8d4
Revises: d2f6b48e1a07
Create Date: 2026-08-21

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f91a70b8d4"
down_revision = "d2f6b48e1a07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the column, nullable, with no default.

    No `server_default`: a default would populate every existing row, which
    is exactly the false negative described above. `ADD COLUMN` with no
    default is also metadata-only in Postgres 11+, so this does not rewrite
    a table that holds millions of rows.
    """
    op.add_column(
        "indicators",
        sa.Column("bull_close_below_lower", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    """Drop it.

    Safe in a way its sibling's downgrade is not: nothing reads this column
    unless `SignalParams.enabled_signal_types` lists
    `bull_close_below_lower`, and at the time this migration was written it
    did not. If it has been enabled since, dropping this column silently
    stops the type firing rather than raising -- `_close_flag` treats an
    absent field as "did not fire" -- so check `enabled_signal_types` before
    running this.
    """
    op.drop_column("indicators", "bull_close_below_lower")
