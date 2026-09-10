"""one prediction per signal, chosen rather than joined

**`v_screen_live` joined `predictions` on the natural key and the natural
key is not unique.** Two rows can share
`(config_hash, ticker, as_of, signal_type, entry_kind)` while carrying
different `event_id`s, and a plain `LEFT JOIN` then emits the event once per
match. The page silently gains rows.

**Measured before writing this**, on serving, 2026-09-09: the Pi holds 472
predictions its own `cscan predict --serving` wrote, and the sync holds 498
for the same session from research. Backfilling the Pi's missing natural key
inside a transaction took `v_screen_live` from **163 rows to 264** for that
date -- 101 signals rendered twice -- and the transaction was rolled back.

**The two sets are complementary, which is why neither can simply be
dropped.** Serving carries a three-year subset (ADR 137), so a
research-origin prediction's `event_id` points at a row serving does not
have: 0 of 498 resolve. The Pi reads serving's own `events`, so all 472 of
its `event_id`s resolve and none of them carried a natural key. One set is
joinable only by natural key; the other is the only one that knows which
event it is actually about.

**So the view chooses instead of joining.** A lateral with `LIMIT 1` returns
exactly one prediction per event, ordered:

1. `p2.event_id = e.id` first -- an exact identity beats a key that merely
   matches on five columns, and it is the only ordering term that is a fact
   about *this* event rather than a tiebreak.
2. then `created_at DESC` -- the newer scoring of the same signal.
3. then `id DESC`, so the result is total. A lateral whose ordering can tie
   is a view that returns different numbers on different days.

**Why not make the natural key UNIQUE and upsert on it.** That is the real
fix and it is a writer change, not a view change: `jobs/predict.py` upserts
on `event_id` deliberately, because `predictions.id` is a `bigserial` that
`outcomes.prediction_id` references, and an upsert that reassigns primary
keys raises `ForeignKeyViolation` against the forward log. Moving the
conflict target has to be reasoned about against that, and it does not
belong in the same change as the defect it would prevent. -> `BACKLOG.md`

**This is why the natural-key writer is not yet deployed to the Pi.** The Pi
runs `c4a5775`, which predates it and writes `signal_type` as NULL -- so its
rows are invisible to this join and cannot duplicate anything. Deploying the
writer without this view change would have started the double-render the
next time the poller scored.

Revision ID: a7c2e9f4b105
Revises: d4f1b8c07e93
Create Date: 2026-09-09
"""

# ruff: noqa: E501 -- `pg_get_viewdef` output captured verbatim.

from alembic import op

revision = "a7c2e9f4b105"
down_revision = "d4f1b8c07e93"
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
     LEFT JOIN LATERAL ( SELECT p2.q50, p2.p_touch_2, p2.p_touch_3, p2.p_touch_5, p2.p_touch_10,
            p2.p_adverse_3, p2.model_version, p2.ci_low, p2.ci_high, p2.calib_n_eff, p2.calibration_json
           FROM predictions p2
          WHERE p2.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
            AND p2.model_scored
            AND (p2.event_id = e.id
                 OR (p2.ticker = e.ticker AND p2.as_of = e.signal_date
                     AND p2.signal_type = e.signal_type AND p2.entry_kind = e.entry_kind))
          ORDER BY (p2.event_id = e.id) DESC, p2.created_at DESC, p2.id DESC
         LIMIT 1) p ON true
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


def upgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW public.v_screen_live AS " + V_SCREEN_LIVE_NEW)


def downgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW public.v_screen_live AS " + V_SCREEN_LIVE_OLD)
