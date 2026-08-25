"""ADR 149: surface the watch universe.

Two changes, and the second is deliberately a *new* view rather than a
predicate change to `v_screen`.

**`v_universe` gains `in_watch` and `watch_reason`.** It already projects
`in_trade` and the five `crit_*` booleans, so this is the row's membership
becoming complete rather than a new concept.

**`v_watchlist` is new.** `v_screen` filters `e.in_trade`, and that filter is
what keeps the statistical population pure -- it is the view behind the
screener's cell statistics, and `test_events_in_trade_filter.py` exists
because a read of `events` that loses its population predicate looks
completely normal. Widening `v_screen` to `in_trade OR in_watch` would pull
watched names into the surface that joins `cell_stats`, which is precisely
what ADR 149 promises not to do.

A separate view keeps the two populations separate at the serving layer for
the same reason the columns keep them separate in the table.

**`v_watchlist` deliberately carries no `cell_stats` join.** Watched names
have no statistics by construction -- every statistical read hardcodes
`in_trade` -- so a `p_hit` column would be NULL on every row, and a NULL
that always means "not applicable" is indistinguishable from one that means
"suppressed". `watch_reason` is what this surface has to say instead.

Revision ID: c8d3a1f70b25
Revises: b7c2f0d5a913
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op

revision = "c8d3a1f70b25"
down_revision = "b7c2f0d5a913"
branch_labels = None
depends_on = None

_V_UNIVERSE = """
CREATE OR REPLACE VIEW v_universe AS
SELECT DISTINCT ON (u.ticker)
    u.ticker, t.name, t.sector, t.industry, u.as_of,
    u.in_train, u.in_trade,
    u.mcap_usd, u.mcap_rank, u.adv_20d_usd,
    u.crit_mcap, u.crit_above_sma200, u.crit_sma200_slope,
    u.crit_rel_return, u.crit_rev_growth,
    t.is_active, t.delisted_on,
    -- Appended, not inserted after `in_trade` where they belong logically:
    -- `CREATE OR REPLACE VIEW` may only add columns at the end, and
    -- reordering raises "cannot change name of view column". Dropping and
    -- recreating would avoid that and is not worth it -- nothing depends on
    -- this view (checked against pg_depend), but a DROP makes that a
    -- standing requirement rather than a fact about today.
    u.in_watch, u.watch_reason
  FROM universe u
  JOIN tickers t ON t.ticker = u.ticker
 ORDER BY u.ticker, u.as_of DESC
"""

_V_UNIVERSE_PRIOR = """
CREATE OR REPLACE VIEW v_universe AS
SELECT DISTINCT ON (u.ticker)
    u.ticker, t.name, t.sector, t.industry, u.as_of,
    u.in_train, u.in_trade,
    u.mcap_usd, u.mcap_rank, u.adv_20d_usd,
    u.crit_mcap, u.crit_above_sma200, u.crit_sma200_slope,
    u.crit_rel_return, u.crit_rev_growth,
    t.is_active, t.delisted_on
  FROM universe u
  JOIN tickers t ON t.ticker = u.ticker
 ORDER BY u.ticker, u.as_of DESC
"""

# Mirrors `v_screen`'s shape where the two overlap -- same cluster-head and
# `next_open` grain, same `config_hash` GUC -- so a reader moving between
# them is not also learning a second set of conventions. The differences are
# the population predicate and the absence of statistics, and both are the
# point.
_V_WATCHLIST = """
CREATE VIEW v_watchlist AS
SELECT e.ticker,
       e.signal_date,
       e.signal_type,
       e.signal_types_all,
       e.signal_strength,
       e.touch_level,
       e.bb_pctb,
       e.k_full,
       e.k_fast,
       e.k_cross_up,
       e.dd_52w,
       e.dd_bucket,
       e.above_sma200,
       e.seq_in_cluster,
       e.cofire_count,
       t.sector,
       u.watch_reason,
       u.mcap_usd,
       u.crit_rel_return,
       u.crit_above_sma200,
       u.crit_sma200_slope
  FROM events e
  JOIN tickers t ON t.ticker = e.ticker
  -- The universe row that decided membership: latest evaluation on or
  -- before the signal, matching `core.universe.in_watch`'s reading. A join
  -- on `u.as_of = e.signal_date` would find nothing, since evaluations are
  -- quarterly and signals are daily.
  JOIN LATERAL (
      SELECT u2.watch_reason, u2.mcap_usd, u2.crit_rel_return,
             u2.crit_above_sma200, u2.crit_sma200_slope
        FROM universe u2
       WHERE u2.ticker = e.ticker AND u2.as_of <= e.signal_date
       ORDER BY u2.as_of DESC
       LIMIT 1
  ) u ON TRUE
 WHERE e.is_cluster_head
   AND e.entry_kind = 'next_open'
   AND e.in_watch
   AND e.config_hash = current_setting('capitalscan.default_config_hash', true)
"""


def upgrade() -> None:
    op.execute(_V_UNIVERSE)
    op.execute(_V_WATCHLIST)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_watchlist")
    # Dropped rather than replaced: `CREATE OR REPLACE` cannot remove a
    # column, so restoring the prior shape needs a real DROP.
    op.execute("DROP VIEW IF EXISTS v_universe")
    op.execute(_V_UNIVERSE_PRIOR)
