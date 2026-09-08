"""v_ticker_events carries in_watch

ADR 149 gave the universe two memberships. `v_ticker_events` exposed only
`in_trade`, so the ticker page could not tell "in the watch universe, so no
statistic is published" from "outside the universe entirely, so nothing is
coming". It rendered both as **outside universe**, which is wrong for any
name failing a single criterion -- AAPL, VMC, HUM and ELPC among them.

The rows were never mis-backtested: `research/backtest.py` reads `in_trade`
*and* `in_watch` from `universe` and writes both, and 192,545 watch-only
touch events carry entry prices. Only the label was wrong, and only because
the view dropped the column that distinguishes the two.

**Appended at the end of both branches.** `CREATE OR REPLACE VIEW` matches
columns positionally, so a new column may only go last -- the same
constraint migration `d5a02b18c937` learned with
`cannot change name of view column`.

Revision ID: d8c40a5b71e9
Revises: c1e6b73f9a02
Create Date: 2026-09-08
"""

# ruff: noqa: E501 -- `pg_get_viewdef` output captured verbatim.
from alembic import op

revision = "d8c40a5b71e9"
down_revision = "c1e6b73f9a02"
branch_labels = None
depends_on = None

NEW = """SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    false AS pending,
    e.in_watch
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND e.entry_kind = 'next_open'::text
UNION ALL
 SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    e.in_trade AS pending,
    e.in_watch
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND e.entry_kind = 'touch'::text AND NOT (EXISTS ( SELECT 1
           FROM events n
          WHERE n.config_hash = e.config_hash AND n.ticker = e.ticker AND n.signal_date = e.signal_date AND n.signal_type = e.signal_type AND n.entry_kind = 'next_open'::text))"""

OLD = """SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    false AS pending
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND e.entry_kind = 'next_open'::text
UNION ALL
 SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    e.in_trade AS pending
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND e.entry_kind = 'touch'::text AND NOT (EXISTS ( SELECT 1
           FROM events n
          WHERE n.config_hash = e.config_hash AND n.ticker = e.ticker AND n.signal_date = e.signal_date AND n.signal_type = e.signal_type AND n.entry_kind = 'next_open'::text))"""


def upgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW v_ticker_events AS " + NEW)


def downgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW v_ticker_events AS " + OLD)
