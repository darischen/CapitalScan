"""cell_stats primary key becomes (cell_id, config_hash)

Revision ID: b2e5d81a4c76
Revises: a1f4c7d2e903
Create Date: 2026-08-06 00:00:00.000000

ADR 096. `cell_stats` was created with `cell_id text PRIMARY KEY`
(migration 7b31a50af774), matching DESIGN §6.9 as written. That key holds
exactly one statistics snapshot at a time, which was the design intent
while `/api/stats` was assumed to serve one current config.

Session 9's exit sweep wrote 18 distinct `config_hash` values into
`events`. Under the single-column key, each Phase 4 run overwrites the
previous one, so comparing those 18 configs statistically would mean
running Phase 4 eighteen times and exporting to files between runs.
Comparing configs is the reason they were swept.

`config_hash` already exists as a NOT NULL column on this table; this
migration only promotes it into the key. `cell_id` itself is unchanged and
still derived from its component columns (invariant 5b, ADR 088) — the two
are separate columns in a composite key, not a concatenated string, so an
existing `cell_id` value means what it always meant.

Safe now, expensive later. Verified 2026-08-06: `cell_stats` holds 0 rows
and Phase 4's `cell_key()` does not exist yet, so this costs one catalog
change. After Phase 4 runs, the same change costs this migration plus a
full Phase 4 re-run.

Asymmetric rollback, stated rather than discovered. `downgrade()` restores
the single-column key, which succeeds only while `cell_id` is still unique
on its own. Once Phase 4 has written two configs' statistics, the
downgrade will fail on a duplicate-key error. That is correct behavior —
silently collapsing to one config would discard rows — but it means this
migration is effectively one-way after the first multi-config Phase 4 run.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e5d81a4c76"
down_revision: Union[str, Sequence[str], None] = "a1f4c7d2e903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE cell_stats DROP CONSTRAINT cell_stats_pkey")
    op.execute(
        "ALTER TABLE cell_stats ADD CONSTRAINT cell_stats_pkey PRIMARY KEY (cell_id, config_hash)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE cell_stats DROP CONSTRAINT cell_stats_pkey")
    op.execute("ALTER TABLE cell_stats ADD CONSTRAINT cell_stats_pkey PRIMARY KEY (cell_id)")
