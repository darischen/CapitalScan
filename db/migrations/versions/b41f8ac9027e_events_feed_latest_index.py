r"""an index for "the newest date that fired", which the rebuild made slow

Revision ID: b41f8ac9027e
Revises: e93a0c7d541b
Create Date: 2026-08-19 03:25:00.000000

ADR 122's rebuild took `events` from 13.5M rows to 14.6M and the live
config's `touch` slice from 157,168 to 1,313,053. The screener's first
query on every page load is "what is the newest date with rows", and it
went from 265 ms to **1.9 s** -- there is no index that answers a max over
`(config_hash, entry_kind)`, so the planner reads all 1.3M matching rows.

`count(*)` over `v_screen_live` is worse at **24.5 s**, for the same reason
plus four `LEFT JOIN LATERAL` subqueries the planner cannot drop.

This index makes the max a backward index-only scan: leading equality
columns, then `signal_date DESC`, partial on `in_trade` because every
consumer that asks this question wants the trade universe.

It also serves the previous/next date arrows and the calendar's month
query, which ask the same shape.

**Plain `CREATE INDEX`, not `CONCURRENTLY`.** Alembic wraps a migration in
a transaction and `CONCURRENTLY` cannot run inside one. The SHARE lock
blocks writes for the duration, which is why this belongs outside the
poller window -- CLAUDE.md's rule about not migrating against a live writer
applies.

**Verify:**

    psql -c "EXPLAIN ANALYZE SELECT max(signal_date) FROM events
             WHERE entry_kind = 'touch' AND in_trade
               AND config_hash = current_setting('capitalscan.default_config_hash', true)"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b41f8ac9027e"
down_revision: Union[str, Sequence[str], None] = "e93a0c7d541b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "events_feed_latest",
        "events",
        ["config_hash", "entry_kind", sa.text("signal_date DESC")],
        postgresql_where=sa.text("in_trade"),
    )


def downgrade() -> None:
    op.drop_index("events_feed_latest", table_name="events")
