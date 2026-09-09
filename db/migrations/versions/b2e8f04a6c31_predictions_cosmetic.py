"""predictions record whether they are cosmetic

**ADR 183.** `in_watch` events now get a probability so a reader clicking
any ticker sees one. The model was never fitted on them, so every such
number is extrapolation and must say so.

**Why `model_scored` does not cover this.** ADR 180's flag answers "was
this signal *type* in the training frame". A cosmetic row can be a fitted
type -- `confluence_low`, say -- sitting on a population the model never
saw. The two failures are independent and a reader needs to know which one
applies, so they get separate columns rather than one overloaded flag.

**Why `in_watch` cannot simply be trained on instead.** `peak_labels`
writes labels for `in_trade` rows only (`peak_labels.py:120`), so measured
2026-09-09 the `in_watch` population carries **443 labelled rows against
`in_trade`'s 160,473**, and 347 of those fall in `train`. There is no
population there to fit on. Removing that filter and rebuilding labels is a
real option and a different decision -- it moves the training population,
which is a model change requiring measurement, not a display change.

`false` is the safe default, matching `model_scored`: a row written before
the writer knew about this column is a row nobody checked.

Revision ID: b2e8f04a6c31
Revises: a1c7f3b09d84
Create Date: 2026-09-09
"""

from alembic import op

revision = "b2e8f04a6c31"
down_revision = "a1c7f3b09d84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE predictions
          ADD COLUMN IF NOT EXISTS cosmetic boolean NOT NULL DEFAULT false
    """)

    # Backfill from the event each prediction points at. Every prediction
    # written before this migration came from a trade-only serving frame,
    # so they are all non-cosmetic -- but deriving it rather than trusting
    # that means a row whose event has since left the universe is still
    # described by what was true when it was scored.
    op.execute("""
        UPDATE predictions p
           SET cosmetic = NOT e.in_trade
          FROM events e
         WHERE e.id = p.event_id
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS cosmetic")
