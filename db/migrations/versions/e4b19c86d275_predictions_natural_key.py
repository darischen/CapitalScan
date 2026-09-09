"""predictions carry the natural key, not just a research id

**`predictions.event_id` cannot survive a sync, and this is the fix.**

`sync._drop_surrogate_id` strips the `id` column from any table whose key
is not `id`. `events` keys on
`(config_hash, ticker, signal_date, signal_type, entry_kind)`, so serving
assigns its own `id` from a local sequence. Measured 2026-09-08: research's
2026-09-08 events are ids 73.7M-74.5M, serving's are 72.70M-72.72M for the
same natural keys.

`predictions` carries `event_id` -- a *research* id -- so the screener join
succeeded only where the two sequences happened to coincide. August
rendered; September did not, and no amount of syncing could fix it.

ADR 174 chose `event_id` for a good reason: `(ticker, as_of)` is not
unique, because a name can fire a long and a short on the same day (BNS on
2026-08-25 carries a `bb_upper_touch` short and a `stoch_oversold` long).
That reasoning holds. The answer is not to go back to the pair but to carry
the same five columns `events` itself syncs on.

**`event_id` stays.** It is correct within research, `outcomes` joins on it
through `prediction_id`, and the forward log's 5,986 rows depend on it. This
adds a second addressing scheme for the copy to use, rather than replacing
the one that works at home.

**Backfilled from `events`, not recomputed.** The values are already on the
row the prediction points at, and deriving them any other way would invent
a second definition of what signal a prediction is about.

Revision ID: e4b19c86d275
Revises: d8c40a5b71e9
Create Date: 2026-09-08
"""

from alembic import op

revision = "e4b19c86d275"
down_revision = "d8c40a5b71e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE predictions
          ADD COLUMN IF NOT EXISTS signal_type text,
          ADD COLUMN IF NOT EXISTS entry_kind  text
    """)

    # Backfill from the event each prediction already names. Runs on both
    # databases; on serving the join is by that store's own ids, which is
    # exactly the mismatch this migration exists to route around -- so rows
    # that cannot resolve stay NULL there and are filled by the next sync.
    op.execute("""
        UPDATE predictions p
           SET signal_type = e.signal_type,
               entry_kind  = e.entry_kind
          FROM events e
         WHERE e.id = p.event_id
           AND (p.signal_type IS NULL OR p.entry_kind IS NULL)
    """)

    # The natural key the screener views join on. Not UNIQUE: one ticker-day
    # can carry a long and a short, which is the whole reason ADR 174 moved
    # off `(ticker, as_of)` in the first place.
    op.execute("""
        CREATE INDEX IF NOT EXISTS predictions_natural_key
            ON predictions (config_hash, ticker, as_of, signal_type, entry_kind)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS predictions_natural_key")
    op.execute("""
        ALTER TABLE predictions
          DROP COLUMN IF EXISTS entry_kind,
          DROP COLUMN IF EXISTS signal_type
    """)
