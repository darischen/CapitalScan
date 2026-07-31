"""statistics predictions positions

Revision ID: 7b31a50af774
Revises: 01c32499e1b2
Create Date: 2026-07-31 00:41:26.601869

DESIGN.md §6.9, §6.10, §7.8, §8.5: cell_stats, benchmarks, predictions,
outcomes, positions, order_intents. Also adds the foreign key from
signal_reports.prediction_id to predictions.id now that predictions
exists (BUILD.md §1.6 — this constraint could not be added in
migration 003).
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7b31a50af774'
down_revision: Union[str, Sequence[str], None] = '01c32499e1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE cell_stats (
          cell_id text PRIMARY KEY,
          run_id text NOT NULL, config_hash text NOT NULL,

          signal_type text, dd_bucket text, signal_strength int,
          side text, entry_kind text, split_key text, era text,
          horizon_days int, target_pct numeric,

          n_events int, n_eff int, n_tickers int, mean_cofire numeric,

          p_hit numeric,
          baseline_empirical numeric, baseline_parametric numeric,
          edge numeric, ci_low numeric, ci_high numeric,
          p_value_randomization numeric, p_value_parametric numeric, q_value numeric,

          mean_ret numeric, median_ret numeric, ret_p25 numeric, ret_p75 numeric,
          mean_mfe numeric, mean_mae numeric, median_time_to_mfe numeric,
          capture_ratio numeric,

          p_touch_2pct numeric, p_touch_3pct numeric,
          p_touch_5pct numeric, p_touch_10pct numeric,
          median_day_touch_5pct numeric,

          exit_mix jsonb,
          earnings_frac numeric,
          suppressed boolean, suppress_reason text,

          computed_at timestamptz, git_sha text
        )
    """)

    op.execute("""
        CREATE TABLE benchmarks (
          id bigserial PRIMARY KEY,
          run_id text NOT NULL, config_hash text NOT NULL,
          arm text NOT NULL,
          replication int,
          split_key text, era text,

          total_ret numeric, annualized_ret numeric,
          sharpe numeric, max_drawdown numeric,
          frac_deployed numeric, capital_efficiency numeric,
          win_rate numeric, n_trades int,

          terminal_value numeric, irr numeric,
          avg_cost_basis numeric, cash_drag numeric,
          capital_undeployed numeric,

          n_round_trips int, avg_days_in_cash numeric,

          pre_tax_ret numeric, post_tax_ret numeric, wash_sale_flagged boolean,

          computed_at timestamptz, git_sha text
        )
    """)
    op.execute("CREATE INDEX benchmarks_lookup ON benchmarks (run_id, arm, split_key)")

    op.execute("""
        CREATE TABLE predictions (
          id bigserial PRIMARY KEY,
          ticker text, as_of date, model_version text, event_id bigint,
          q05 numeric, q25 numeric, q50 numeric, q75 numeric, q95 numeric,
          p_touch_2 numeric, p_touch_3 numeric, p_touch_5 numeric, p_touch_10 numeric,
          p_adverse_3 numeric, p_adverse_5 numeric,
          cell_id text, cell_p_hit numeric, cell_n_eff int,
          features_json jsonb NOT NULL,
          git_sha text, created_at timestamptz
        )
    """)

    op.execute("""
        CREATE TABLE outcomes (
          prediction_id bigint PRIMARY KEY REFERENCES predictions(id),
          realized_ret_5d numeric, realized_mfe numeric, realized_mae numeric,
          touched_2 boolean, touched_3 boolean, touched_5 boolean, touched_10 boolean,
          pinball_loss numeric, brier_3pct numeric,
          resolved_at timestamptz
        )
    """)

    op.execute("""
        CREATE TABLE positions (
          id bigserial PRIMARY KEY,
          user_id text,
          ticker text NOT NULL,
          side text NOT NULL,
          entry_date date NOT NULL,
          entry_price numeric(12,4) NOT NULL,
          quantity numeric,
          source text NOT NULL DEFAULT 'user_declared',
          status text NOT NULL DEFAULT 'open',
          exit_date date, exit_price numeric(12,4), exit_reason text,
          realized_ret numeric(12,6),
          idempotency_key text UNIQUE,
          created_at timestamptz DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE order_intents (
          id bigserial PRIMARY KEY,
          event_id bigint, ticker text, side text,
          quantity_basis text, limit_level numeric(12,4),
          stop_level numeric(12,4), time_in_force text,
          idempotency_key text UNIQUE NOT NULL,
          emitted_at timestamptz DEFAULT now(),
          run_id text, git_sha text
        )
    """)

    op.execute("""
        ALTER TABLE signal_reports
          ADD CONSTRAINT signal_reports_prediction_id_fkey
          FOREIGN KEY (prediction_id) REFERENCES predictions(id)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE signal_reports DROP CONSTRAINT signal_reports_prediction_id_fkey")
    op.execute("DROP TABLE order_intents")
    op.execute("DROP TABLE positions")
    op.execute("DROP TABLE outcomes")
    op.execute("DROP TABLE predictions")
    op.execute("DROP TABLE benchmarks")
    op.execute("DROP TABLE cell_stats")
