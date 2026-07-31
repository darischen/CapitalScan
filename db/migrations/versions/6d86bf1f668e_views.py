"""views

Revision ID: 6d86bf1f668e
Revises: 7b31a50af774
Create Date: 2026-07-31 00:42:07.175562

DESIGN.md §8.2. Eight views are the shared query contract (ADR 076):
the TypeScript API routes and the Python MCP handlers both SELECT from
these, never from the base tables, so a shape change happens in one
place. Copied verbatim from DESIGN.md per BUILD.md §1.7.

Per ADR 088: `events` carries no `cell_id` column (one event maps to
many cells, since horizon/target are report parameters, not event
properties). `cell_key()` is the canonical, immutable function the
stats job and every view use to compute a cell identity from
components. `v_screen` joins `cell_stats` on those components with
`split_key = 'validate'` and `era IS NULL` hardcoded — never
`e.split_key`, which for a live event is `'holdout'` and would leak
holdout statistics into the UI continuously if inherited.

v_positions is created after v_ticker_state because it depends on it.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6d86bf1f668e'
down_revision: Union[str, Sequence[str], None] = '7b31a50af774'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        -- Canonical cell identifier. The stats job and every view use this
        -- one function (ADR 088).
        CREATE FUNCTION cell_key(
          p_signal_type text, p_side text, p_dd_bucket text,
          p_strength int, p_entry_kind text, p_split text,
          p_era text, p_horizon int, p_target numeric
        ) RETURNS text LANGUAGE sql IMMUTABLE AS $$
          SELECT concat_ws('|',
            p_signal_type, p_side,
            coalesce(p_dd_bucket, 'all'),
            coalesce(p_strength::text, 'all'),
            p_entry_kind, p_split,
            coalesce(p_era, 'pooled'),
            'h' || p_horizon,
            't' || to_char(p_target, 'FM990.999')
          );
        $$
    """)

    op.execute("""
        -- 1. Screener. Joins on components with display parameters pinned.
        CREATE VIEW v_screen AS
        SELECT e.ticker, e.signal_date, e.signal_type, e.signal_types_all,
               e.signal_strength, e.touch_level, e.bb_pctb, e.k_full, e.k_fast,
               e.k_cross_up, e.dd_52w, e.dd_bucket, e.above_sma200,
               e.seq_in_cluster, e.cofire_count, e.sector,
               c.cell_id,
               CASE WHEN c.suppressed THEN NULL ELSE c.p_hit END              AS p_hit,
               CASE WHEN c.suppressed THEN NULL ELSE c.baseline_empirical END AS baseline,
               CASE WHEN c.suppressed THEN NULL ELSE c.edge END               AS edge,
               CASE WHEN c.suppressed THEN NULL ELSE c.ci_low END             AS ci_low,
               CASE WHEN c.suppressed THEN NULL ELSE c.ci_high END            AS ci_high,
               c.n_events, c.n_eff, c.q_value, c.suppressed, c.suppress_reason,
               p.q50, p.p_touch_3, p.p_touch_5, p.p_adverse_3, p.model_version
        FROM events e
        LEFT JOIN cell_stats c
               ON c.signal_type     = e.signal_type
              AND c.side            = e.side
              AND c.dd_bucket       = e.dd_bucket
              AND c.signal_strength = e.signal_strength
              AND c.entry_kind      = e.entry_kind
              AND c.split_key       = 'validate'      -- NEVER e.split_key
              AND c.era             IS NULL           -- pooled row
              AND c.horizon_days    = 5
              AND c.target_pct      = 0.03
        LEFT JOIN predictions p
               ON p.ticker = e.ticker AND p.as_of = e.signal_date
        WHERE e.is_cluster_head
          AND e.entry_kind = 'next_open'
    """)

    op.execute("""
        -- 2. Current state rail for /ticker/[sym]. One row per ticker.
        CREATE VIEW v_ticker_state AS
        SELECT DISTINCT ON (i.ticker)
               i.ticker, t.name, t.sector, i.ts AS as_of,
               b.close, b.volume,
               i.bb_lower, i.bb_mid, i.bb_upper, i.bb_pctb,
               i.bb_width, i.bb_width_pct,
               i.k_full, i.d_full, i.k_fast, i.k_cross_up, i.k_cross_down,
               i.sma_200, i.sma200_slope_60, i.atr_14,
               i.rv_20d, i.rv_pct_252d, i.vol_z_20d,
               i.dd_52w, i.days_to_earnings,
               m.vix_close, m.vix_pct_252d, m.spx_ret_1d,
               u.in_trade, u.mcap_usd,
               u.crit_mcap, u.crit_above_sma200, u.crit_sma200_slope,
               u.crit_rel_return, u.crit_rev_growth,
               (b.close > i.sma_200) AS above_sma200
        FROM indicators i
        JOIN bars b    ON b.ticker = i.ticker AND b.ts = i.ts AND b.interval = i.interval
        JOIN tickers t ON t.ticker = i.ticker
        LEFT JOIN market_days m ON m.ts = i.ts::date
        LEFT JOIN LATERAL (
            SELECT * FROM universe u2
            WHERE u2.ticker = i.ticker AND u2.as_of <= i.ts::date
            ORDER BY u2.as_of DESC LIMIT 1
        ) u ON true
        WHERE i.interval = '1d'
        ORDER BY i.ticker, i.ts DESC
    """)

    op.execute("""
        -- 3. Chart series: bars + indicators + event markers, one row per bar.
        CREATE VIEW v_chart AS
        SELECT b.ticker, b.ts, b.open, b.high, b.low, b.close, b.volume,
               i.bb_lower, i.bb_mid, i.bb_upper,
               i.k_full, i.d_full, i.k_fast,
               i.sma_200, i.bb_width_pct, i.dd_52w,
               e.id AS event_id, e.signal_type, e.signal_strength,
               e.exit_date, e.exit_reason, e.net_ret
        FROM bars b
        LEFT JOIN indicators i
               ON i.ticker = b.ticker AND i.ts = b.ts AND i.interval = b.interval
        LEFT JOIN events e
               ON e.ticker = b.ticker AND e.signal_date = b.ts::date
              AND e.is_cluster_head AND e.entry_kind = 'next_open'
        WHERE b.interval = '1d'
    """)

    op.execute("""
        -- 4. Cell statistics with suppression applied IN SQL.
        CREATE VIEW v_stats AS
        SELECT cell_id, run_id, config_hash,
               signal_type, dd_bucket, signal_strength, side,
               entry_kind, split_key, era, horizon_days, target_pct,
               n_events, n_eff, n_tickers, mean_cofire,
               CASE WHEN suppressed THEN NULL ELSE p_hit END              AS p_hit,
               CASE WHEN suppressed THEN NULL ELSE baseline_empirical END AS baseline,
               CASE WHEN suppressed THEN NULL ELSE edge END               AS edge,
               CASE WHEN suppressed THEN NULL ELSE ci_low END             AS ci_low,
               CASE WHEN suppressed THEN NULL ELSE ci_high END            AS ci_high,
               q_value, p_value_randomization,
               mean_ret, median_ret, ret_p25, ret_p75,
               mean_mfe, mean_mae, median_time_to_mfe, capture_ratio,
               p_touch_2pct, p_touch_3pct, p_touch_5pct, p_touch_10pct,
               median_day_touch_5pct,
               exit_mix, earnings_frac,
               suppressed, suppress_reason
        FROM cell_stats
    """)

    op.execute("""
        -- 5. Event history with outcomes. Default config only.
        CREATE VIEW v_events AS
        SELECT e.id, e.ticker, t.sector, e.signal_date, e.signal_type,
               e.signal_types_all, e.signal_strength,
               e.cluster_id, e.seq_in_cluster, e.is_cluster_head,
               e.bb_pctb, e.k_full, e.k_fast, e.dd_52w, e.dd_bucket,
               e.above_sma200, e.vix_close, e.days_to_earnings,
               e.entry_kind, e.entry_date, e.entry_price, e.entry_gapped,
               e.exit_date, e.exit_price, e.exit_reason, e.holding_days,
               e.ambiguous, e.gross_ret, e.net_ret,
               e.mfe, e.mae, e.time_to_mfe, e.capture_ratio,
               e.touched_2pct, e.touched_3pct, e.touched_5pct, e.touched_10pct,
               e.day_touched_5pct, e.earnings_in_window, e.era, e.split_key
        FROM events e
        JOIN tickers t ON t.ticker = e.ticker
        WHERE e.config_hash = current_setting('capitalscan.default_config_hash', true)
    """)

    op.execute("""
        -- 6. Forward log: model vs lookup vs reality.
        CREATE VIEW v_forward AS
        SELECT p.id, p.ticker, p.as_of, p.model_version, p.event_id,
               p.q05, p.q25, p.q50, p.q75, p.q95,
               p.p_touch_2, p.p_touch_3, p.p_touch_5, p.p_touch_10,
               p.p_adverse_3, p.p_adverse_5,
               p.cell_id, p.cell_p_hit, p.cell_n_eff,
               o.realized_ret_5d, o.realized_mfe, o.realized_mae,
               o.touched_2, o.touched_3, o.touched_5, o.touched_10,
               o.pinball_loss, o.brier_3pct, o.resolved_at,
               (o.prediction_id IS NOT NULL) AS resolved,
               CASE WHEN o.prediction_id IS NULL THEN NULL
                    ELSE abs(p.p_touch_3 - o.touched_3::int::numeric) END AS abs_err_3pct
        FROM predictions p
        LEFT JOIN outcomes o ON o.prediction_id = p.id
    """)

    op.execute("""
        -- 7. Universe membership with per-criterion pass/fail.
        CREATE VIEW v_universe AS
        SELECT DISTINCT ON (u.ticker)
               u.ticker, t.name, t.sector, t.industry,
               u.as_of, u.in_train, u.in_trade,
               u.mcap_usd, u.mcap_rank, u.adv_20d_usd,
               u.crit_mcap, u.crit_above_sma200, u.crit_sma200_slope,
               u.crit_rel_return, u.crit_rev_growth,
               t.is_active, t.delisted_on
        FROM universe u
        JOIN tickers t ON t.ticker = u.ticker
        ORDER BY u.ticker, u.as_of DESC
    """)

    op.execute("""
        -- 8. Positions with live exit signals computed, not stored.
        CREATE VIEW v_positions AS
        SELECT p.*,
               s.close AS current_price,
               CASE WHEN p.status = 'open'
                    THEN (s.close - p.entry_price) / p.entry_price
                    ELSE p.realized_ret END              AS unrealized_or_realized_ret,
               (CURRENT_DATE - p.entry_date)             AS days_held,
               (s.k_full >= 80)                          AS exit_signal_stoch,
               (s.close >= s.bb_upper)                   AS exit_signal_upper_band,
               (s.close >= s.bb_mid)                     AS exit_signal_mid_band,
               ((CURRENT_DATE - p.entry_date) >= 5)      AS exit_signal_timeout
        FROM positions p
        LEFT JOIN v_ticker_state s ON s.ticker = p.ticker
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW v_positions")
    op.execute("DROP VIEW v_universe")
    op.execute("DROP VIEW v_forward")
    op.execute("DROP VIEW v_events")
    op.execute("DROP VIEW v_stats")
    op.execute("DROP VIEW v_chart")
    op.execute("DROP VIEW v_ticker_state")
    op.execute("DROP VIEW v_screen")
    op.execute("DROP FUNCTION cell_key(text, text, text, int, text, text, text, int, numeric)")
