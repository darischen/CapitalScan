"""the bull reversal reaches the screener

**ADR 144 has been computing this since 2026-08-21 and nothing displayed
it.** `bull_reversal_state` runs on every poll tick and the block is written
into `signal_reports.state_json` beside `bear_reversal`, but `v_screen_live`
projected only the bear side. A long-side name that closed back inside its
lower band -- EXPE on 2026-09-09, the exact mirror of VOD and BE the same
day -- was indistinguishable on the page from one still falling.

**Four columns, mirroring the bear four**, so the frontend renders one badge
component with a direction rather than two components that drift apart.

**`above_band` is read out as `below_band`.** `bull_reversal_state` reuses
`ReversalState` and documents `above_band` as "the band condition holds on
this signal's own side", which for a long is *below* the lower band. The
dataclass keeps one shape; the view names the side, because a SQL column
called `above_band` that means below is read wrong by whoever comes next.

**`open_gap_atr` keeps its sign**, and therefore flips its reading:
`(price - open) / ATR` always, so negative confirms a bear reversal and
*positive* confirms a bull one. Negating it here would make one column mean
two things depending on a sibling column.

**A separate lateral rather than widening the bear one.** The blocks are
independent -- a wide bar can break both bands, and ADR 144 stores them side
by side for that reason -- so a row can carry a bull reversal and no bear
one. Sharing a lateral would drop the bull side whenever
`state_json ? 'bear_reversal'` missed.

**The columns are appended, never reordered.** `CREATE OR REPLACE VIEW`
requires the existing column list to match position for position and permits
additions only at the end.

Prerequisite, and the reason this went unnoticed for two weeks: until
2026-09-09 `db_io.json_safe` stored every nested dict as a Python repr
string, so both reversal blocks read back as NULL through `->>` and the bear
badge was silently dead too. Fixed and backfilled the same day; this
migration is what makes the repaired data visible.

Revision ID: d4f1b8c07e93
Revises: c5a29e13b708
Create Date: 2026-09-09
"""

# ruff: noqa: E501 -- `pg_get_viewdef` output captured verbatim.

from alembic import op

revision = "d4f1b8c07e93"
down_revision = "c5a29e13b708"
branch_labels = None
depends_on = None


V_SCREEN_LIVE_NEW = """SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.side,
    e.touch_level,
    e.entry_price,
    e.k_fast,
    e.k_full,
    e.d_full,
    e.k_cross_up,
    e.bb_pctb,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.cofire_count,
    t.sector,
    ind.bb_lower,
    ind.bb_mid,
    ind.bb_upper,
    ind.ts AS band_ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
        CASE
            WHEN market_is_open() THEN lq.close
            ELSE NULL::numeric
        END AS live_price,
    lq.ts AS live_price_ts,
    fr.fired_at,
    rev.confirmed AS rev_confirmed,
    rev.above_band AS rev_above_band,
    rev.open_gap_atr AS rev_open_gap_atr,
    rev.rev_ts,
    c.cell_id,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed OR NOT e.in_trade AS suppressed,
        CASE
            WHEN NOT e.in_trade THEN 'watch_universe'::text
            ELSE c.suppress_reason
        END AS suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version,
    e.in_watch,
    e.watch_reason,
    p.p_touch_2,
    p.p_touch_10,
    p.ci_low AS pred_ci_low,
    p.ci_high AS pred_ci_high,
    p.calib_n_eff AS pred_n_eff,
    p.calibration_json,
    bull.confirmed AS bull_rev_confirmed,
    bull.below_band AS bull_rev_below_band,
    bull.open_gap_atr AS bull_rev_open_gap_atr,
    bull.rev_ts AS bull_rev_ts
   FROM events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower,
            i2.bb_mid,
            i2.bb_upper,
            i2.ts
           FROM indicators i2
          WHERE i2.ticker = e.ticker AND i2."interval" = '1d'::text AND i2.ts < e.signal_date
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON true
     LEFT JOIN bars b ON b.ticker = e.ticker AND b.ts = e.signal_date AND b."interval" = '1d'::text
     LEFT JOIN bars_live lq ON lq.ticker = e.ticker AND lq.session_date = market_date()
     LEFT JOIN LATERAL ( SELECT max(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.ticker = e.ticker AND (r.fired_at AT TIME ZONE 'America/New_York'::text)::date = e.signal_date AND (r.signal_type IS NULL OR r.signal_type = e.signal_type)) fr ON true
     LEFT JOIN LATERAL ( SELECT ((r2.state_json -> 'bear_reversal'::text) ->> 'confirmed'::text)::boolean AS confirmed,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'above_band'::text)::boolean AS above_band,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM signal_reports r2
          WHERE r2.ticker = e.ticker AND (r2.fired_at AT TIME ZONE 'America/New_York'::text)::date = e.signal_date AND r2.state_json ? 'bear_reversal'::text
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON true
     LEFT JOIN LATERAL ( SELECT ((r3.state_json -> 'bull_reversal'::text) ->> 'confirmed'::text)::boolean AS confirmed,
            ((r3.state_json -> 'bull_reversal'::text) ->> 'above_band'::text)::boolean AS below_band,
            ((r3.state_json -> 'bull_reversal'::text) ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r3.fired_at AS rev_ts
           FROM signal_reports r3
          WHERE r3.ticker = e.ticker AND (r3.fired_at AT TIME ZONE 'America/New_York'::text)::date = e.signal_date AND r3.state_json ? 'bull_reversal'::text
          ORDER BY r3.fired_at DESC
         LIMIT 1) bull ON true
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND p.model_scored
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""


V_SCREEN_LIVE_OLD = """SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.side,
    e.touch_level,
    e.entry_price,
    e.k_fast,
    e.k_full,
    e.d_full,
    e.k_cross_up,
    e.bb_pctb,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.cofire_count,
    t.sector,
    ind.bb_lower,
    ind.bb_mid,
    ind.bb_upper,
    ind.ts AS band_ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
        CASE
            WHEN market_is_open() THEN lq.close
            ELSE NULL::numeric
        END AS live_price,
    lq.ts AS live_price_ts,
    fr.fired_at,
    rev.confirmed AS rev_confirmed,
    rev.above_band AS rev_above_band,
    rev.open_gap_atr AS rev_open_gap_atr,
    rev.rev_ts,
    c.cell_id,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed OR NOT e.in_trade THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed OR NOT e.in_trade AS suppressed,
        CASE
            WHEN NOT e.in_trade THEN 'watch_universe'::text
            ELSE c.suppress_reason
        END AS suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version,
    e.in_watch,
    e.watch_reason,
    p.p_touch_2,
    p.p_touch_10,
    p.ci_low AS pred_ci_low,
    p.ci_high AS pred_ci_high,
    p.calib_n_eff AS pred_n_eff,
    p.calibration_json
   FROM events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower,
            i2.bb_mid,
            i2.bb_upper,
            i2.ts
           FROM indicators i2
          WHERE i2.ticker = e.ticker AND i2."interval" = '1d'::text AND i2.ts < e.signal_date
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON true
     LEFT JOIN bars b ON b.ticker = e.ticker AND b.ts = e.signal_date AND b."interval" = '1d'::text
     LEFT JOIN bars_live lq ON lq.ticker = e.ticker AND lq.session_date = market_date()
     LEFT JOIN LATERAL ( SELECT max(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.ticker = e.ticker AND (r.fired_at AT TIME ZONE 'America/New_York'::text)::date = e.signal_date AND (r.signal_type IS NULL OR r.signal_type = e.signal_type)) fr ON true
     LEFT JOIN LATERAL ( SELECT ((r2.state_json -> 'bear_reversal'::text) ->> 'confirmed'::text)::boolean AS confirmed,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'above_band'::text)::boolean AS above_band,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM signal_reports r2
          WHERE r2.ticker = e.ticker AND (r2.fired_at AT TIME ZONE 'America/New_York'::text)::date = e.signal_date AND r2.state_json ? 'bear_reversal'::text
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON true
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND p.model_scored
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""


def upgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW public.v_screen_live AS " + V_SCREEN_LIVE_NEW)


def downgrade() -> None:
    # `CREATE OR REPLACE` cannot drop columns, so the rollback is a real drop
    # and recreate. Nothing else depends on this view -- the web app reads it
    # directly -- so there is no cascade to rebuild.
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("CREATE VIEW public.v_screen_live AS " + V_SCREEN_LIVE_OLD)
