"""the fitted model travels in the database

**ADR 185.** The Pi scores poller fires from a saved artifact (ADR 181),
which means the artifact has to reach the Pi. `scp` from the research
machine was the obvious route and is the wrong one: it makes the autonomous
path depend on ssh keys between two boxes, and it breaks differently after
the `wivie` cutover, when the machine doing the pushing changes.

The Pi already holds a serving connection and reads everything else it
needs through it. One row rides that connection.

**One row, replaced.** `config_hash` is the primary key rather than an
append-only log with a timestamp: two artifacts for one generation is not a
state anything wants to resolve at read time, and a scorer picking "the
newest" would silently prefer a half-written row over a good one. The
weekly refit replaces its generation's row and that is the whole lifecycle.

**`bytea`, not a path.** The bytes are the `.npz` exactly as
`numpy.savez_compressed` wrote them, so the reader is `np.load` on a
buffer and nothing has to agree about filesystem layout across three
machines. 3.6 MB measured, well inside what a row should hold.

**`git_sha` and `fitted_at` are carried for the guard**, not for
provenance alone. `artifact.load` refuses a mismatch, and a reader that
cannot say which code produced a model cannot refuse anything.

Revision ID: c5a29e13b708
Revises: b2e8f04a6c31
Create Date: 2026-09-09
"""

from alembic import op

revision = "c5a29e13b708"
down_revision = "b2e8f04a6c31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_artifact (
            config_hash   text        PRIMARY KEY,
            git_sha       text        NOT NULL,
            model_version text        NOT NULL,
            fitted_at     timestamptz NOT NULL,
            n_train       integer     NOT NULL,
            n_calibrate   integer     NOT NULL,
            payload       bytea       NOT NULL,
            written_at    timestamptz NOT NULL DEFAULT now()
        )
    """)

    # The payload is already compressed by `savez_compressed`, so TOAST
    # would spend CPU re-compressing incompressible bytes for nothing.
    op.execute("ALTER TABLE model_artifact ALTER COLUMN payload SET STORAGE EXTERNAL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_artifact")
