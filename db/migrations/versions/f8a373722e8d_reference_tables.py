"""reference tables

Revision ID: f8a373722e8d
Revises:
Create Date: 2026-07-31 00:38:26.552684

DESIGN.md §2.5. Reference data: trading calendar, ticker identity,
corporate actions, point-in-time shares outstanding, and earnings dates.
Nothing here depends on another table in this migration, so creation
order only matters for the two REFERENCES to `tickers`.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f8a373722e8d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE trading_days (
          d date PRIMARY KEY,
          is_early_close boolean NOT NULL DEFAULT false
        )
    """)

    op.execute("""
        CREATE TABLE tickers (
          ticker text PRIMARY KEY,
          cik text,
          name text,
          sector text,
          industry text,
          exchange text,
          first_bar date,
          last_bar date,
          is_active boolean NOT NULL DEFAULT true,
          delisted_on date
        )
    """)

    op.execute("""
        CREATE TABLE corporate_actions (
          ticker text NOT NULL REFERENCES tickers(ticker),
          ex_date date NOT NULL,
          action_type text NOT NULL CHECK (action_type IN ('split','dividend')),
          ratio numeric,
          amount numeric,
          PRIMARY KEY (ticker, ex_date, action_type)
        )
    """)

    op.execute("""
        CREATE TABLE shares_outstanding (
          ticker text NOT NULL REFERENCES tickers(ticker),
          filed_on date NOT NULL,
          period_end date,
          shares bigint NOT NULL,
          source text NOT NULL,
          PRIMARY KEY (ticker, filed_on)
        )
    """)

    op.execute("""
        CREATE TABLE earnings (
          ticker text NOT NULL,
          report_date date NOT NULL,
          session text CHECK (session IN ('bmo','amc','unknown')),
          source text NOT NULL,
          confidence text,
          PRIMARY KEY (ticker, report_date)
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE earnings")
    op.execute("DROP TABLE shares_outstanding")
    op.execute("DROP TABLE corporate_actions")
    op.execute("DROP TABLE tickers")
    op.execute("DROP TABLE trading_days")
