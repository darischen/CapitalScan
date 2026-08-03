"""path table and forward window tracking

Revision ID: 8e2f5d9c0a1a
Revises: 6d86bf1f668e
Create Date: 2026-08-03 01:15:00.000000

Session 10, task 10.1. Adds the forward path store and forward window
completeness tracking to the event table.

Per session10.md §0:
- Path table stores per-day forward outcomes across the full evaluation
  window (ten trading days, max(StatsParams.fwd_ret_horizons)).
- One row per event per forward day, with direction-neutral extremes
  (favorable and adverse) and terminal mark from close.
- Events near the end of price history have partial windows and must be
  identifiable via the fwd_window_days column.

Schema design:
- `path` table: one row per (event_id, day_offset), foreignkey to events(id)
  with cascade delete, composite PK on (event_id, day_offset).
- `events.fwd_window_days`: nullable int, 1-10 when window exists (partial or
  complete), NULL when no forward data available.
- Favorable and adverse extremes use intraday extremes; terminal uses close.
- Day offsets count trading days from price history calendar, never
  calendar days.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e2f5d9c0a1a"
down_revision: Union[str, Sequence[str], None] = "6d86bf1f668e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add columns to events table to track forward window completeness.
    op.execute("ALTER TABLE events ADD COLUMN fwd_window_days int")

    # Create the path table.
    op.execute("""
        CREATE TABLE path (
          event_id bigint NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          day_offset int NOT NULL,
          favorable numeric(12,6) NOT NULL,
          adverse numeric(12,6) NOT NULL,
          terminal numeric(12,6) NOT NULL,
          PRIMARY KEY (event_id, day_offset)
        )
    """)
    op.execute("CREATE INDEX path_event_id ON path (event_id)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE path")
    op.execute("ALTER TABLE events DROP COLUMN fwd_window_days")
