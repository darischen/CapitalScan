"""v_ticker_events: pending means waiting, not excluded

Revision ID: a4b8f2e619c3
Revises: a8d3e5c17f92
Create Date: 2026-08-19 22:20:00.000000

Corrects `a8d3e5c17f92`, applied twenty minutes earlier. That revision
defined `pending` as "this `touch` row has no `next_open` sibling", which
is true of two very different populations:

    in_trade = true,  no sibling      246  waiting on cscan backtest
    in_trade = false, no sibling  179,286  never measured, by design

The second group is ADR 122's: events recorded so they stay visible on
the ticker page, and excluded from every statistical read. Labelling them
"not yet backtested" promises a number that is never coming, which is a
different lie from the blank it replaced but still a lie.

`pending` is now `in_trade AND no sibling`, and `in_trade` is projected
beside it so the page can say which of the three things a row without an
outcome is: measured, waiting, or outside the universe.

The view also reads `events` directly rather than `v_events`, because
`v_events` does not project `in_trade` and has no DDL constant in
`jobs/views.py` -- it exists only in an older migration, so changing it
here would put two definitions of it in the chain.

**Found by verification, not by a test.** The counts after applying
`a8d3e5c17f92` were 47,310 pending against an estimate of 246, and the
two orders of magnitude were the whole signal.

**Verify:**

    psql -c "SELECT count(*) FILTER (WHERE pending) FROM v_ticker_events"
    -- expect ~246 across cluster heads, not ~47,000

**Per ADR 125 the SQL below is a literal, never an import.**
"""

from alembic import op

revision = "a4b8f2e619c3"
down_revision = "a8d3e5c17f92"
branch_labels = None
depends_on = None

DDL = """CREATE VIEW public.v_ticker_events AS
 SELECT
    e.ticker,
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
   FROM public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker))
  WHERE ((e.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
     AND (e.entry_kind = 'next_open'::text))
UNION ALL
 SELECT
    e.ticker,
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
   FROM public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker))
  WHERE ((e.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
     AND (e.entry_kind = 'touch'::text)
     AND (NOT (EXISTS ( SELECT 1
           FROM public.events n
          WHERE ((n.config_hash = e.config_hash) AND (n.ticker = e.ticker)
              AND (n.signal_date = e.signal_date) AND (n.signal_type = e.signal_type)
              AND (n.entry_kind = 'next_open'::text))))))
"""

# `v_ticker_events` as `a8d3e5c17f92` defined it, kept verbatim so
# `downgrade()` restores it exactly -- mislabelled `pending` included.
OLD_DDL = """\nCREATE VIEW public.v_ticker_events AS
 SELECT
    e.id,
    e.ticker,
    e.sector,
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
    false AS pending
   FROM public.v_events e
  WHERE (e.entry_kind = 'next_open'::text)
UNION ALL
 SELECT
    e.id,
    e.ticker,
    e.sector,
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
    true AS pending
   FROM public.v_events e
  WHERE ((e.entry_kind = 'touch'::text)
     AND (NOT (EXISTS ( SELECT 1
           FROM public.v_events n
          WHERE ((n.ticker = e.ticker) AND (n.signal_date = e.signal_date)
              AND (n.signal_type = e.signal_type)
              AND (n.entry_kind = 'next_open'::text))))))
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_ticker_events")
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_ticker_events")
    op.execute(OLD_DDL)
