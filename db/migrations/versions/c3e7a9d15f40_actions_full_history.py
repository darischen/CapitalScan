"""actions_full_history: remember a full-history fetch even when it found nothing

**Why.** `run_actions` picks a path per ticker by whether it has rows in
`corporate_actions`: rows mean the batched incremental fetch, no rows mean
a full-history `yf.Ticker(t).actions`, one request each at the 0.5/s rate
limit. A company that has never split or paid a dividend never gets a row,
so it paid for its whole history again **every night**. Measured
2026-09-27: 286 of the 1,463 tickers, ~9.5 minutes of a 13.7-minute step.

**What this records.** One row per ticker whose full history was fetched,
with when. `run_actions` treats a ticker fetched within
`ACTIONS_FULL_REFETCH_DAYS` as known and sends it down the batched path,
which still catches any new split or dividend in the lookback window. After
that window the full fetch runs once more, so an empty result caused by a
transient download failure heals within a month instead of hiding an old
split forever (`jobs/compute.py` reads splits).

**Its own table, not a column on `tickers`.** It is ingest bookkeeping, and
`tickers` is reference data the serving store copies; a fetch timestamp
there would ship to the Pi for no reader.

Revision ID: c3e7a9d15f40
Revises: b6d1e8f30a27
Create Date: 2026-09-27
"""

from alembic import op

revision = "c3e7a9d15f40"
down_revision = "b6d1e8f30a27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE actions_full_history ("
        "  ticker text PRIMARY KEY REFERENCES tickers(ticker),"
        "  fetched_at timestamp with time zone NOT NULL DEFAULT now()"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS actions_full_history")
