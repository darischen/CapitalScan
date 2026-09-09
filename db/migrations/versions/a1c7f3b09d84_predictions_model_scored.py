"""predictions record whether the model was ever fitted on their signal type

**Found live 2026-09-08: 48% of shipped predictions were extrapolation.**

4,207 of 8,699 predictions were for `stoch_oversold`/`stoch_overbought`.
The training frame contains **zero** rows of either. The screener rendered a
calibrated probability, a confidence interval and an `n_eff` for signals the
model had never been fitted on, and nothing in the output told them apart
from the half that were legitimate.

The cause is structural, not a typo. `build_training_frame` drops rows with
NULL labels, which removes stochastic-only signals because they carry no fill
price and so no forward return. `build_serving_frame` drops `LABEL_COLS`
outright -- ADR 174's guard against ever scoring the holdout -- so it has
nothing left to filter on and keeps every row. The two builders disagreed
*because* of a correct safety measure in one of them.

**Measured on the forward log** (1,966 resolved out-of-population outcomes
against 4,020 in):

    in population   Brier 0.2256  skill 0.079  predicted 51.7%  actual 57.1%
    OUT             Brier 0.2445  skill 0.021  predicted 52.3%  actual 49.0%

The model hands both groups ~52%, while their real rates differ by eight
points. In population it errs low; outside it errs **high** by 3.3pp, and
skill against the base rate falls to about a quarter. Overconfidence is the
expensive direction, and it was the one outside the fit.

**A flag rather than a delete.** Those 1,966 resolved outcomes are a third of
the whole forward log and the only direct measurement of what the model does
off-distribution -- exactly the evidence ADR 179's rolling refit needs. The
rows stay; they stop being served.

`false` is the safe default: a row written before the writer knew to set this
is a row nobody checked, and it should be excluded until something proves
otherwise.

Revision ID: a1c7f3b09d84
Revises: f7d3a02e5c18
Create Date: 2026-09-08
"""

# ruff: noqa: E501 -- `pg_get_viewdef` output captured verbatim.

from alembic import op

revision = "a1c7f3b09d84"
down_revision = "f7d3a02e5c18"
branch_labels = None
depends_on = None

#: The signal types `build_training_frame` actually produced, measured
#: 2026-09-08 against the live generation. Written out here because a
#: migration must describe the world at the moment it ran and cannot import
#: code that will keep moving. **The running system never reads this list**
#: -- `run_predict` passes `predictor.trained_signal_types` off the fit
#: itself, so the two cannot drift.
TRAINED_TYPES = (
    "bb_lower_touch",
    "bb_upper_touch",
    "bear_close_above_upper",
    "confluence_high",
    "confluence_low",
)


# The two screener views, with the join narrowed to rows the model was
# fitted on. Copied literally rather than imported from the migration that
# defined them: a migration describes one moment, and importing a sibling
# would make this file's behaviour change when that one is edited.
V_SCREEN_SCORED = """SELECT e.ticker,
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
    c.cell_id,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed,
    c.suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version,
    p.p_touch_2,
    p.p_touch_10,
    p.ci_low AS pred_ci_low,
    p.ci_high AS pred_ci_high,
    p.calib_n_eff AS pred_n_eff,
    p.calibration_json
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
       AND p.model_scored
  WHERE e.is_cluster_head AND e.entry_kind = 'next_open'::text AND e.in_trade AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

V_SCREEN_LIVE_SCORED = """SELECT e.ticker,
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
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
       AND p.model_scored
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

V_SCREEN_PRIOR = """SELECT e.ticker,
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
    c.cell_id,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed,
    c.suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version,
    p.p_touch_2,
    p.p_touch_10,
    p.ci_low AS pred_ci_low,
    p.ci_high AS pred_ci_high,
    p.calib_n_eff AS pred_n_eff,
    p.calibration_json
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
  WHERE e.is_cluster_head AND e.entry_kind = 'next_open'::text AND e.in_trade AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

V_SCREEN_LIVE_PRIOR = """SELECT e.ticker,
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
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""


def upgrade() -> None:
    op.execute("""
        ALTER TABLE predictions
          ADD COLUMN IF NOT EXISTS model_scored boolean NOT NULL DEFAULT false
    """)

    types = ", ".join(f"'{t}'" for t in TRAINED_TYPES)
    op.execute(f"""
        UPDATE predictions
           SET model_scored = (signal_type IN ({types}))
         WHERE signal_type IS NOT NULL
    """)

    # The screener filters on this on every read, and it is selective
    # (roughly half the table), so it earns an index.
    op.execute("""
        CREATE INDEX IF NOT EXISTS predictions_model_scored
            ON predictions (config_hash, as_of) WHERE model_scored
    """)

    # The screener stops reading unscored rows. `v_forward` is left alone
    # on purpose -- it is the scoring log, and what the model does off its
    # training population is exactly what that log exists to measure.
    op.execute("CREATE OR REPLACE VIEW v_screen AS " + V_SCREEN_SCORED)
    op.execute("CREATE OR REPLACE VIEW v_screen_live AS " + V_SCREEN_LIVE_SCORED)


def downgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW v_screen AS " + V_SCREEN_PRIOR)
    op.execute("CREATE OR REPLACE VIEW v_screen_live AS " + V_SCREEN_LIVE_PRIOR)
    op.execute("DROP INDEX IF EXISTS predictions_model_scored")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS model_scored")
