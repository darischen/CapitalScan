"""events trough_ret_1d/2d/3d/5d/10d columns

ADR 175. The exact mirror of `peak_ret_{h}d` (migration `c7e1a4f9d302`):

    m_h = min over t in [1, h] of the entry-anchored return

`path.adverse` already holds the per-day series and is **side-adjusted in
position convention** — a long uses `(low - entry) / entry` and a short
uses `(entry - high) / entry`, so negative means "against the position" on
both sides. No sign fix is needed at read time, exactly as with
`favorable`.

**Why not use `events.mae`, which already exists.** `mae` is the minimum
adverse excursion *until the trade exits*, and the exit is decided by
`ExitParams`. A model head fitted on it would carry `target_pct` and
`stop_atr_k` inside its own target, so every sweep would silently redefine
what the head is predicting. `peak_ret_5d` has no such coupling and neither
does this. `mae` stays on the table and stays correct for reporting what
happened to a trade under the policy that ran.

**`numeric(12,6)`, matching `peak_ret_*`.** These are returns, not prices,
and the two families are compared against each other constantly; differing
precision between them would produce differences that are artefacts of
storage.

**NULL until the forward window closes.** The writer applies the same
completeness gate as the peak family: a trough taken over a still-filling
window is a smaller-magnitude number wearing a complete label, which is the
staleness class ADR 094 exists to prevent.

Revision ID: f2a71d6e8c34
Revises: e7b4c92f1a08
Create Date: 2026-09-05
"""

from alembic import op

revision = "f2a71d6e8c34"
down_revision = "e7b4c92f1a08"
branch_labels = None
depends_on = None

# Mirrors `StatsParams.fwd_ret_horizons`. Written out rather than imported:
# a migration must describe the schema at the moment it ran, and importing
# a sweepable constant would make an old migration change meaning when the
# constant does.
HORIZONS = (1, 2, 3, 5, 10)


def upgrade() -> None:
    for h in HORIZONS:
        op.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS trough_ret_{h}d numeric(12,6)")


def downgrade() -> None:
    for h in HORIZONS:
        op.execute(f"ALTER TABLE events DROP COLUMN IF EXISTS trough_ret_{h}d")
