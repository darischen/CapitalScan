"""universe and audit

Revision ID: 8785b00f9217
Revises: c530b63124cf
Create Date: 2026-07-31 00:39:59.088114

DESIGN.md §2.5. Universe membership (ADR 014's per-criterion evaluation,
not a separate `regime` table per BUILD.md §1.4) plus the audit trail:
`runs`, `scheduled_runs`, `poller_sessions`, `quotes_live`, `signal_reports`.

`signal_reports.prediction_id` is created here as a plain bigint with no
foreign key, because `predictions` does not exist until migration 005.
The FK itself is added there via ALTER TABLE (BUILD.md §1.6).
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8785b00f9217'
down_revision: Union[str, Sequence[str], None] = 'c530b63124cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE universe (
          ticker text NOT NULL REFERENCES tickers(ticker),
          as_of date NOT NULL,
          in_train boolean NOT NULL,
          in_trade boolean NOT NULL,
          mcap_usd numeric,
          mcap_rank int,
          adv_20d_usd numeric,
          crit_mcap boolean,
          crit_above_sma200 boolean,
          crit_sma200_slope boolean,
          crit_rel_return boolean,
          crit_rev_growth boolean,
          PRIMARY KEY (ticker, as_of)
        )
    """)

    op.execute("""
        CREATE TABLE runs (
          run_id text PRIMARY KEY,
          job text NOT NULL,
          git_sha text NOT NULL,
          params jsonb NOT NULL,
          started_at timestamptz NOT NULL,
          finished_at timestamptz,
          status text CHECK (status IN ('running','ok','failed')),
          rows_written bigint,
          notes text
        )
    """)

    op.execute("""
        CREATE TABLE scheduled_runs (
          job text NOT NULL,
          scheduled_for timestamptz NOT NULL,
          actual_start timestamptz,
          delay_seconds int,
          status text,
          run_id text,
          PRIMARY KEY (job, scheduled_for)
        )
    """)

    op.execute("""
        CREATE TABLE poller_sessions (
          session_date date PRIMARY KEY,
          started_at timestamptz,
          ended_at timestamptz,
          ticks_completed int,
          ticks_expected int,
          coverage_pct numeric(6,3),
          notes text
        )
    """)

    op.execute("""
        CREATE TABLE quotes_live (
          ticker text NOT NULL,
          ts timestamptz NOT NULL,
          price numeric(12,4) NOT NULL,
          breached text,
          breach_depth_atr numeric(12,6),
          event_id bigint,
          PRIMARY KEY (ticker, ts)
        )
    """)

    # Immutable record of what the user was told, per ADR 029.
    # Lets the forward log compare the recommendation against the outcome.
    op.execute("""
        CREATE TABLE signal_reports (
          id bigserial PRIMARY KEY,
          event_id bigint,
          ticker text NOT NULL,
          fired_at timestamptz NOT NULL,
          state_json jsonb NOT NULL,
          cell_id text,
          prediction_id bigint,
          call_overlay_json jsonb,
          channels_sent text[],
          model_version text,
          git_sha text
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE signal_reports")
    op.execute("DROP TABLE quotes_live")
    op.execute("DROP TABLE poller_sessions")
    op.execute("DROP TABLE scheduled_runs")
    op.execute("DROP TABLE runs")
    op.execute("DROP TABLE universe")
