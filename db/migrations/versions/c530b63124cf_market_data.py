"""market data

Revision ID: c530b63124cf
Revises: f8a373722e8d
Create Date: 2026-07-31 00:39:11.652865

DESIGN.md §2.5. Raw bars, their rejects, market-wide daily context
(SPX/VIX), and the wide indicators table. The CHECK constraints on
`bars` are the first line of §2.3's validation, enforced by Postgres
itself rather than only in application code.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c530b63124cf'
down_revision: Union[str, Sequence[str], None] = 'f8a373722e8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE bars (
          ticker text NOT NULL REFERENCES tickers(ticker),
          ts timestamptz NOT NULL,
          interval text NOT NULL DEFAULT '1d',
          open numeric(12,4) NOT NULL,
          high numeric(12,4) NOT NULL,
          low numeric(12,4) NOT NULL,
          close numeric(12,4) NOT NULL,
          adj_close numeric(12,4) NOT NULL,
          volume bigint,
          adj_factor numeric(12,6) NOT NULL DEFAULT 1.0,
          is_terminal boolean NOT NULL DEFAULT false,
          source text NOT NULL,
          ingested_at timestamptz NOT NULL DEFAULT now(),
          run_id text,
          PRIMARY KEY (ticker, ts, interval),
          CHECK (high >= low),
          CHECK (close BETWEEN low AND high),
          CHECK (open  BETWEEN low AND high)
        )
    """)
    op.execute("CREATE INDEX bars_ts_idx ON bars (ts)")

    op.execute("""
        CREATE TABLE bar_rejects (
          id bigserial PRIMARY KEY,
          ticker text, ts timestamptz,
          rule text NOT NULL,
          severity text NOT NULL CHECK (severity IN ('flag','reject')),
          payload jsonb,
          run_id text,
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE market_days (
          ts date PRIMARY KEY,
          spx_close numeric(12,4),
          spx_ret_1d numeric(12,6),
          vix_close numeric(12,4),
          vix_pct_252d numeric(12,6)
        )
    """)

    op.execute("""
        CREATE TABLE indicators (
          ticker text NOT NULL,
          ts timestamptz NOT NULL,
          interval text NOT NULL DEFAULT '1d',
          bb_mid numeric(12,6), bb_upper numeric(12,6), bb_lower numeric(12,6),
          bb_pctb numeric(12,6), bb_width numeric(12,6), bb_width_pct numeric(12,6),
          k_fast numeric(12,6), d_fast numeric(12,6),
          k_full numeric(12,6), d_full numeric(12,6),
          k_cross_up boolean, k_cross_down boolean,
          sma_200 numeric(12,6), sma200_slope_60 numeric(12,6),
          atr_14 numeric(12,6),
          rv_20d numeric(12,6), rv_pct_252d numeric(12,6),
          vol_z_20d numeric(12,6),
          dd_52w numeric(12,6),
          days_to_earnings int,
          computed_at timestamptz NOT NULL DEFAULT now(),
          run_id text,
          PRIMARY KEY (ticker, ts, interval)
        )
    """)
    op.execute("CREATE INDEX indicators_ts_idx ON indicators (ts)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE indicators")
    op.execute("DROP TABLE market_days")
    op.execute("DROP TABLE bar_rejects")
    op.execute("DROP TABLE bars")
