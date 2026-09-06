"""predictions carry their calibration

ADR 174 ships `p_touch` and requires every published probability to arrive
with the evidence behind it. `predictions` was written in Phase 5 as a
shape to fill later and is missing four things a row now needs.

**`run_id` and `config_hash`.** Invariant 6 says every generated row
carries `run_id` and `git_sha`. This table has had `git_sha` since
migration 005 and has never had `run_id`, which nothing caught because
nothing has ever written a row. It is `text`, matching the other thirteen
tables that carry it and `runs.run_id` itself -- a `bigint` here would have
failed at the first write against a run id like `predict-20260905-a1b2c3`.

`config_hash` matters for the same reason it matters on `events`: a
prediction made under one generation is not comparable to one made under
another, and without the column there is no way to tell them apart after
the fact.

**`ci_low` / `ci_high` / `calib_n_eff` / `calib_bucket`.** Invariant 8's
companions. `Prediction` has carried these fields since Phase 5 and the
table had nowhere to put them -- only `cell_p_hit` and `cell_n_eff`, which
mean the *conditioning cell* under ADR 093 and not the calibration bucket
under ADR 174. Overloading those two would have avoided this migration and
made `cell_id` mean two different things depending on which code path
wrote it, which is exactly the kind of ambiguity invariant 5b exists to
prevent. Separate columns, separate meanings.

**`p_touch_3_raw`.** The uncalibrated model output. Storing only the
calibrated value would make the calibration irreversible: refitting the
reliability table later could not be checked against what the model
actually said. One column buys auditability of the whole ADR 174 claim.

**The unique index is the load-bearing part of this migration.**
`v_screen` and `v_screen_live` both `LEFT JOIN predictions p ON p.ticker =
e.ticker AND p.as_of = e.signal_date`. A LEFT JOIN against a table with two
rows for one `(ticker, as_of)` does not error -- it silently doubles every
screen row for that ticker. Two model versions, or one re-run that inserts
instead of upserting, is enough. The constraint makes that impossible at
the database rather than by convention, and gives the writer an
`ON CONFLICT` target so a re-run replaces rather than accumulates.

The uniqueness is on `(ticker, as_of)` and deliberately excludes
`model_version`: including it would satisfy the letter of the constraint
and still fan the join out, because the join does not mention the version.
One prediction per ticker per day is the invariant the views actually need.

Revision ID: c3f8a1e07b26
Revises: e2c7a94b3d15
Create Date: 2026-09-05
"""

from alembic import op

revision = "c3f8a1e07b26"
down_revision = "e2c7a94b3d15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS so this is safe to re-apply against a database that was
    # patched by hand during Phase 6 development.
    op.execute("""
        ALTER TABLE predictions
          ADD COLUMN IF NOT EXISTS run_id        text,
          ADD COLUMN IF NOT EXISTS config_hash   text,
          ADD COLUMN IF NOT EXISTS calib_bucket  text,
          ADD COLUMN IF NOT EXISTS calib_n_eff   numeric,
          ADD COLUMN IF NOT EXISTS ci_low        numeric,
          ADD COLUMN IF NOT EXISTS ci_high       numeric,
          ADD COLUMN IF NOT EXISTS p_touch_3_raw numeric
    """)

    # Deduplicate before the constraint goes on, keeping the newest row per
    # key. The table is empty today, so this is a no-op here and the reason
    # it exists is the hand-patched case above: CREATE UNIQUE INDEX fails
    # outright on a duplicate and would leave the migration half-applied.
    op.execute("""
        DELETE FROM predictions a
         USING predictions b
         WHERE a.ticker = b.ticker AND a.as_of = b.as_of AND a.id < b.id
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS predictions_ticker_as_of
            ON predictions (ticker, as_of)
    """)

    # The serving path reads by config generation and recency.
    op.execute("""
        CREATE INDEX IF NOT EXISTS predictions_config_as_of
            ON predictions (config_hash, as_of DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS predictions_config_as_of")
    op.execute("DROP INDEX IF EXISTS predictions_ticker_as_of")
    op.execute("""
        ALTER TABLE predictions
          DROP COLUMN IF EXISTS p_touch_3_raw,
          DROP COLUMN IF EXISTS ci_high,
          DROP COLUMN IF EXISTS ci_low,
          DROP COLUMN IF EXISTS calib_n_eff,
          DROP COLUMN IF EXISTS calib_bucket,
          DROP COLUMN IF EXISTS config_hash,
          DROP COLUMN IF EXISTS run_id
    """)
