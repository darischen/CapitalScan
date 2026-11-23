"""v_listing: what the ticker page can say about a name with bars and no indicators

**Why.** The ticker page gates on `v_ticker_state`, which is built on
`indicators`, and `cscan indicators` writes nothing for a ticker until it
holds `MIN_BARS_FOR_INDICATORS` (280) daily bars. A new listing therefore
opened on "No indicator history" for over a year. On 2026-11-23 that was 35
active tickers on serving with current bars, among them ANT (listed
2026-11-19), SPCX and Q. Search could not reach them either: it reads
`v_universe`, and a quarter is evaluated only once it has closed.

**One row per ticker with daily bars.** The identity columns come from
`tickers`; the rest are scalar subqueries, so a read that does not select a
column does not pay for it (the planner drops unreferenced subquery outputs
when it inlines the view). Search reads only `evaluated` and the identity
columns; the ticker page reads one row.

- `first_bar`, `last_bar`, `sessions`, `last_close`: from `bars`, daily
  only. `tickers.first_bar` is not used, because it is coverage bookkeeping
  written at ingest and can lag what serving holds.
- `evaluated`: the live generation holds a `universe` row for it, the same
  test `v_universe` makes.
- `has_indicators`: any daily indicator row. The ticker page's normal path
  is taken when this is true.

Applies to both databases (ADR 053). ADR 076: the web reads views.
"""

from __future__ import annotations

from alembic import op

revision = "e2b6f4a8c913"
down_revision = "d7e3b1a94c52"
branch_labels = None
depends_on = None


V_LISTING = """
CREATE VIEW v_listing AS
SELECT t.ticker,
       t.name,
       t.sector,
       t.industry,
       t.exchange,
       t.is_active,
       t.delisted_on,
       (SELECT min(b.ts)::date FROM bars b
         WHERE b.ticker = t.ticker AND b.interval = '1d') AS first_bar,
       (SELECT max(b.ts)::date FROM bars b
         WHERE b.ticker = t.ticker AND b.interval = '1d') AS last_bar,
       (SELECT count(*) FROM bars b
         WHERE b.ticker = t.ticker AND b.interval = '1d') AS sessions,
       (SELECT b.close FROM bars b
         WHERE b.ticker = t.ticker AND b.interval = '1d'
         ORDER BY b.ts DESC LIMIT 1) AS last_close,
       EXISTS (SELECT 1 FROM universe u
                WHERE u.ticker = t.ticker
                  AND u.config_hash = current_setting('capitalscan.default_config_hash', true)
              ) AS evaluated,
       EXISTS (SELECT 1 FROM indicators i
                WHERE i.ticker = t.ticker AND i.interval = '1d') AS has_indicators
  FROM tickers t
 WHERE EXISTS (SELECT 1 FROM bars b WHERE b.ticker = t.ticker AND b.interval = '1d')
"""


def upgrade() -> None:
    op.execute(V_LISTING)
    # The serving web role reads views by grant, like every other view.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'capscan_ro') THEN "
        "GRANT SELECT ON v_listing TO capscan_ro; END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_listing")
