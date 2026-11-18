"""cell_stats: provenance for the cluster-robust interval (ADR 209)

**Why.** ADR 209 replaces ADR 098's `n_eff = n / (1 + rho_bar (k_bar - 1))`
with a two-way cluster-robust variance over every fire, clustered by
calendar month and by `cluster_id`, inflated by a per-scope factor the
placebo test measures. `n_eff`, `ci_low` and `ci_high` keep their names and
their meaning for a reader, so no view changes. What a reader could not do
without these columns is rebuild the interval: the factor and the two
cluster counts are inputs to it.

- `variance_method`: `'cgm_month_cluster'` on rows ADR 209 writes. Null on
  rows written under ADR 098, so old and new rows stay distinguishable.
- `n_time_blocks`: distinct calendar months in the cell.
- `n_clusters`: distinct `cluster_id` values in the cell.
- `se_inflation`: the placebo factor applied to the standard error.

All four are nullable and carry no default, so the `ADD COLUMN` touches only
the catalog and rewrites no rows. `v_stats` lists its columns explicitly and
does not pick these up.

Revision ID: d7e3b1a94c52
Revises: c5d9e2a7b384
Create Date: 2026-11-18
"""

from __future__ import annotations

from alembic import op

revision = "d7e3b1a94c52"
down_revision = "c5d9e2a7b384"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE cell_stats"
        " ADD COLUMN variance_method text,"
        " ADD COLUMN n_time_blocks integer,"
        " ADD COLUMN n_clusters integer,"
        " ADD COLUMN se_inflation numeric(8,4)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE cell_stats"
        " DROP COLUMN se_inflation,"
        " DROP COLUMN n_clusters,"
        " DROP COLUMN n_time_blocks,"
        " DROP COLUMN variance_method"
    )
