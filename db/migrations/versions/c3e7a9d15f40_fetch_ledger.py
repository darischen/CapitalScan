"""fetch_ledger: remember a slow per-ticker fetch even when it found nothing

**Why.** Two nightly steps paid for the same per-ticker requests every
night because nothing recorded that they had already been made. Measured
2026-09-27 on the workstation copy and in `runs`:

- `actions` sends a ticker with no `corporate_actions` rows down the
  full-history path, one request each at the 0.5/s rate limit. A company
  that has never split or paid a dividend never gets a row, so 286 of 1,463
  tickers paid for their whole history again nightly: ~9.5 of 13.7 min.
- `shares` falls back to Yahoo (`fetch_shares_full`, `fetch_net_assets`)
  for ~280 depositary, ETF and SEC-stale tickers. The cache key carries
  today's date, so it never hits across nights: ~9.5 of ~10.4 min.

**What this records.** One row per `(source, ticker)`: when that fetch last
ran, whether or not it returned anything. Each caller skips a ticker whose
row is younger than its own refetch window and records every attempt that
did not raise. An exception is not recorded, so a failed fetch is retried
the next night.

**One table for both, keyed by `source`.** The two needs are the same
shape; two single-purpose tables would be the drift this project keeps
recording. Not a column on `tickers`: this is ingest bookkeeping, and
`tickers` is reference data the serving store copies.

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
        "CREATE TABLE fetch_ledger ("
        "  source text NOT NULL,"
        "  ticker text NOT NULL REFERENCES tickers(ticker),"
        "  fetched_at timestamp with time zone NOT NULL DEFAULT now(),"
        "  PRIMARY KEY (source, ticker)"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fetch_ledger")
