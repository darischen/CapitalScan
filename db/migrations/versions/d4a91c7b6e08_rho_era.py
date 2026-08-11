"""rho_era: per-era effective-sample-size inputs

Revision ID: d4a91c7b6e08
Revises: c7e1a4f9d302
Create Date: 2026-08-10 00:00:00.000000

ADR 098 part 3. `n_eff = n / (1 + (k_bar - 1) * rho_bar)` needs a `rho_bar`
per era, and this table is where it lives.

Why a table rather than a `StatsParams` field. These are measurements, not
chosen parameters. `jobs.config.config_hash` hashes the whole `Config`, so a
`rho_bar` field would fold a measured quantity into the hash: recomputing it
against more data would move `config_hash` and appear to be a configuration
change when nothing was chosen differently. It would also strand the values
without provenance, which ADR 034 requires on every generated row and whose
omission on the `path` table already cost real investigation time in session
10's reconciliation findings 3 and 7.

Why `config_hash` sits in the primary key. Same reason ADR 096 put it in
`cell_stats`: an 18-config sweep produces 18 event populations with
different co-firing structures, and one snapshot per era would make each
Phase 4 run overwrite the last. Comparing configs is the reason they were
swept.

Column notes:

- `rho_empirical` is NOT NULL because it is the value `n_eff` consumes. An
  era with no measurable co-firing writes no row rather than a null one; a
  null here would read as "computed and found nothing," which is a
  different claim from "not computed."
- `rho_factor_implied`, `rho_gap`, and `mean_beta` are nullable. The
  single-factor diagnostic depends on `market_days.spx_close` covering the
  era, and it is a diagnostic — its absence must not block the value the
  statistics layer actually needs.
- `numeric` without a precision, matching how `cell_stats` stores its own
  statistics. These are correlations in [-1, 1], not prices.

Rollback is a plain DROP. Nothing references this table by foreign key,
`cell_stats` is empty through session 11, and no view reads it, so the
reversal leaves no orphaned objects. Verified against the migration's own
`downgrade()` rather than assumed.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a91c7b6e08"
down_revision: Union[str, Sequence[str], None] = "c7e1a4f9d302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE rho_era (
          era text NOT NULL,
          run_id text NOT NULL,
          config_hash text NOT NULL,
          PRIMARY KEY (era, config_hash),

          rho_empirical numeric NOT NULL,
          rho_factor_implied numeric,
          rho_gap numeric,

          n_pairs int NOT NULL,
          n_cofire_days int NOT NULL,
          mean_beta numeric,

          computed_at timestamptz NOT NULL,
          git_sha text NOT NULL
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE rho_era")
