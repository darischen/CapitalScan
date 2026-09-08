"""market_days breadth columns

ADR 176. `p_touch` is calibrated in every regime and only *ranks* in some,
and the variable that separates them is universe breadth -- the fraction of
tickers whose 20-day average sits above their 200-day. Below 0.68 the
shipped probability scores AUC 0.6255; at or above it scores 0.5154, which
is a coin flip.

**On `market_days`, not a new table.** It is market-level daily data keyed
on `ts`, exactly like `spx_close`, `vix_close` and `vix_pct_252d` which
already live there. A separate table would need its own join everywhere
this is read.

**Two columns, not one.** The level says how extended the universe is; the
60-session change says which way it is going. The measured grid needs both:
`breadth 0.55-0.68 rising` scores 0.6623 while `breadth >= 0.68 rising`
scores 0.5154, and level alone cannot express that.

**`double precision`, not `numeric`.** These are fractions of a count
recomputed from scratch on every run rather than money, and nothing
compares them for equality. The rest of the market columns are `numeric`
because they carry prices.

**Nullable, and the writer leaves early history NULL on purpose.**
`breadth_chg_60d` needs 60 prior sessions and `sma_200` needs 200, so the
first year of any window has no honest value. A zero there would read as
"flat breadth", which is a confident statement about a market nobody
measured.

Revision ID: c1e6b73f9a02
Revises: b4f8c17d29e6
Create Date: 2026-09-07
"""

from alembic import op

revision = "c1e6b73f9a02"
down_revision = "b4f8c17d29e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE market_days
          ADD COLUMN IF NOT EXISTS breadth_ma_above  double precision,
          ADD COLUMN IF NOT EXISTS breadth_chg_60d   double precision
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE market_days
          DROP COLUMN IF EXISTS breadth_chg_60d,
          DROP COLUMN IF EXISTS breadth_ma_above
    """)
