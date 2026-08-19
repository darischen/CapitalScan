r"""events records trade-universe membership instead of being filtered by it

Revision ID: f2d16b47c093
Revises: c4a7e91b53d8
Create Date: 2026-08-19 06:30:00.000000

ADR 122. `run_events` skipped any bar where the ticker was outside the
trade universe that day (DESIGN §4.7 step 3), so a train-universe name has
bars, indicators, real band touches, and **no events at all**. Measured:
SMCI has 192 band touches since 2024, zero events, and has never once been
in the trade universe across 66 quarterly snapshots.

The membership stops being a filter on *detection* and becomes a column on
the *detection*. A signal that fired is a fact about the ticker; whether you
would have traded it is a separate fact, and storing the second lets every
consumer decide for itself.

**`NOT NULL DEFAULT true`, which is the whole reason this migration is
fast.** Postgres 11+ stores a non-volatile column default in the catalog
rather than rewriting the table, so this is a metadata change against
13,479,819 rows instead of a full rewrite plus an `UPDATE`. `true` is not a
guess: every row already in the table was written by the gated code path,
so every one of them cleared the trade universe. That is exactly what the
default asserts.

The risk the default carries, stated because it is real: an `INSERT` that
forgets the column silently claims trade membership. `run_events` sets it
explicitly on every row after this, and `test_events_in_trade_filter.py`
fails if any production read of `events` stops filtering on it.

**The index is partial.** Every consumer that filters wants
`in_trade = true`; nothing scans for the false rows except the ticker page,
which always has a ticker predicate and uses `events_ticker_date`. A partial
index is a fraction of the size of a full one and the planner uses it for
exactly the queries that exist.

**Verify:**

    cscan db status
    psql -c "\d events"
    psql -c "SELECT in_trade, count(*) FROM events GROUP BY 1"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from capitalscan.jobs.views import (
    V_SCREEN_DDL,
    V_SCREEN_LIVE_DDL,
)

revision: str = "f2d16b47c093"
down_revision: Union[str, Sequence[str], None] = "c4a7e91b53d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The exact line `upgrade()` adds to both screener views, so `downgrade()`
# removes precisely it. Spelled once rather than inline at both call sites:
# a downgrade that removed *almost* the right text would leave a view that
# still parses and no longer filters, which is the failure mode this whole
# migration exists to prevent.
_PREDICATE = "     AND e.in_trade\n"


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "in_trade",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "events_in_trade",
        "events",
        ["config_hash", "split_key", "signal_type"],
        postgresql_where=sa.text("in_trade"),
    )

    # Both screener views take the predicate. Their two consumers already
    # join `v_universe` and filter there, so this is defence in depth --
    # but `v_universe` answers "is it in the universe *now*" and the column
    # answers "was it in the universe *on that bar*", which is the right
    # question for a historical row.
    #
    # `v_chart` and `v_events` deliberately do not change: they are what
    # the ticker page reads, and showing every firing on a name you do not
    # trade is what ADR 122 is for.
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(V_SCREEN_DDL)
    op.execute(V_SCREEN_LIVE_DDL)


def downgrade() -> None:
    # The views first: they reference the column, so dropping it under them
    # would fail rather than cascade -- which is the safe default and the
    # reason the order matters. `V_SCREEN_DDL_PRE_122` is not needed: the
    # constants below are regenerated from this module, so re-running the
    # ADR 120 forms means re-running them without the predicate.
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(V_SCREEN_DDL.replace(_PREDICATE, ""))
    op.execute(V_SCREEN_LIVE_DDL.replace(_PREDICATE, ""))

    # Then the index: dropping a column would cascade to it, and a cascade
    # that happens to do the right thing is not the same as saying so.
    op.drop_index("events_in_trade", table_name="events")
    op.drop_column("events", "in_trade")
