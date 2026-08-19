"""v_ticker_events: the ticker history reads a grain that is defined for it

Revision ID: a8d3e5c17f92
Revises: c1f7d92a6b45
Create Date: 2026-08-19 21:40:00.000000

Reported 2026-08-19: the ticker page showed a blank entry and exit on old
events whose horizon had long passed.

Measured across in-trade cluster heads at the `touch` grain:

    stoch_overbought  11,780 rows  11,780 with no entry
    stoch_oversold     7,438 rows   7,438 with no entry
    bb_upper_touch     8,876 rows       0 with no entry
    confluence_low     1,377 rows       0 with no entry

19,218 of 41,065 rows -- 47% -- and every one of them a stochastic
signal.

**Not a data fault.** A `touch` entry means *enter at the level you
touched*. A stochastic threshold crossing has no level, so `touch_level`
is NULL on every one of those rows by construction, no entry price can be
assigned, and no exit can follow. The same events carry complete outcomes
at `next_open`, `touch_5m` and `touch_30m`, where entry does not need a
band. AMZN 2024-12-31 `stoch_oversold`, for instance, entered 2025-01-02
at 222.0966 and exited 2025-01-10 at 218.94 on a timeout -- visible at
`next_open` and blank at `touch`.

So the page read a grain undefined for two of the seven signal types and
rendered the undefined half as an em-dash, which is what a reader reads
as missing data.

**This view reads `next_open`** -- `GRID_ENTRY_KIND`, the grain every
Phase 4 statistic used -- **and unions back the `touch` rows with no
`next_open` sibling.** Those are the poller's fires from sessions
`cscan backtest` has not reached: 246 of 41,065 when measured. Reading
`next_open` alone would drop today's fires off the page, which is the row
a reader most wants.

`pending` marks them, so the UI can say "not yet backtested" instead of
printing a blank that looks exactly like the defect this fixes.

**Marker alignment survives.** `v_chart` marks bars at `entry_kind =
'touch' AND is_cluster_head IS NOT FALSE`, and `(config_hash, ticker,
signal_date, signal_type, entry_kind)` is the natural key -- so every
historical touch row has an exact `next_open` sibling and a marker with no
history row under it stays impossible.

**Per ADR 125 the SQL below is a literal, never an import.**

**Verify:**

    cscan db status
    psql -c "SELECT count(*) FILTER (WHERE pending) FROM v_ticker_events"
"""

from alembic import op

revision = "a8d3e5c17f92"
down_revision = "c1f7d92a6b45"
branch_labels = None
depends_on = None

DDL = """CREATE VIEW public.v_ticker_events AS
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
    # Dropped rather than restored: the view did not exist before this
    # revision. The ticker page reading `v_events` directly is what a
    # downgrade returns to, defect included.
    op.execute("DROP VIEW IF EXISTS public.v_ticker_events")
