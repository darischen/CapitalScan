"""events

Revision ID: 01c32499e1b2
Revises: 8785b00f9217
Create Date: 2026-07-31 00:40:50.909063

DESIGN.md §5.7. The widest table in the schema: one row per
(config, ticker, signal_date, signal_type, entry_kind). Cluster columns
(ADR 056) and the UNIQUE constraint are the parts most likely to be
gotten wrong, per BUILD.md §1.5.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "01c32499e1b2"
down_revision: Union[str, Sequence[str], None] = "8785b00f9217"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE events (
          id bigserial PRIMARY KEY,
          run_id text NOT NULL REFERENCES runs(run_id),
          config_hash text NOT NULL,

          -- identity
          ticker text NOT NULL,
          signal_date date NOT NULL,
          signal_type text NOT NULL,
          signal_types_all text[],
          signal_strength int,
          side text NOT NULL CHECK (side IN ('long','short')),

          -- clustering (§5.3)
          cluster_id bigint,
          seq_in_cluster int,
          is_cluster_head boolean,
          days_since_head int,

          -- state at signal, all from t-1
          touch_level numeric(12,4),
          bb_pctb numeric(12,6),
          bb_width_pct numeric(12,6),
          k_full numeric(12,6), d_full numeric(12,6), k_fast numeric(12,6),
          k_cross_up boolean, k_cross_down boolean,
          atr_14 numeric(12,6),
          rv_pct_252d numeric(12,6),
          dd_52w numeric(12,6),
          sma200_slope_60 numeric(12,6),
          above_sma200 boolean,
          vol_z_20d numeric(12,6),
          days_to_earnings int,
          vix_close numeric(12,4),
          spx_ret_1d numeric(12,6),

          -- context tags
          dd_bucket text, bw_regime text, era text,
          cofire_count int, mcap_usd numeric, sector text,

          -- entry (one row per entry_kind)
          entry_kind text NOT NULL,
          entry_date date, entry_price numeric(12,4), entry_gapped boolean,

          -- exit
          exit_date date, exit_price numeric(12,4), exit_reason text,
          holding_days int, ambiguous boolean NOT NULL DEFAULT false,

          -- outcome
          gross_ret numeric(12,6), net_ret numeric(12,6),
          mfe numeric(12,6), mae numeric(12,6),
          time_to_mfe int, capture_ratio numeric(12,6),

          -- reachability, full 5-bar window
          touched_2pct boolean,  day_touched_2pct int,
          touched_3pct boolean,  day_touched_3pct int,
          touched_5pct boolean,  day_touched_5pct int,
          touched_10pct boolean, day_touched_10pct int,

          -- unconditional forward returns, for baseline comparison
          fwd_ret_1d numeric(12,6), fwd_ret_2d numeric(12,6),
          fwd_ret_3d numeric(12,6), fwd_ret_5d numeric(12,6),
          fwd_ret_10d numeric(12,6),

          -- flags
          earnings_in_window boolean,
          is_terminal boolean,
          split_key text NOT NULL CHECK (split_key IN ('train','validate','holdout')),

          UNIQUE (config_hash, ticker, signal_date, signal_type, entry_kind)
        )
    """)
    op.execute("CREATE INDEX events_lookup ON events (signal_type, split_key, dd_bucket)")
    op.execute("CREATE INDEX events_ticker_date ON events (ticker, signal_date)")
    op.execute("CREATE INDEX events_cluster ON events (cluster_id, seq_in_cluster)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE events")
