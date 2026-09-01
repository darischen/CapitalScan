"""v_screen_live prefers signal_type when the report carries one

Revision ID: b7f3c5d21a94
Revises: a4c8d19f6e02
Create Date: 2026-09-01 14:30:00.000000

**Closes the imprecision `d5e91a7c3b48` accepted and named.**

That migration had to stop resolving `fired_at` through
`r.event_id = e.id`, because ADR 150's nightly sweep nulls that column by
design -- so the precise link is destroyed every night on exactly the rows
the site most wants to show. It retreated to the three columns the sweep
guarantees survive (`ticker`, `fired_at`, `state_json`) and matched on
`(ticker, signal_date)`, which was the finest grain then available.

Its own docstring records the cost: "a ticker that fires two different
signal types on one day gives both events the same earliest `fired_at`",
and names the fix as storing `signal_type` on the report.

`a4c8d19f6e02` added that column on 2026-08-29 and the poller has written
it since. Measured on research 2026-09-01, after the first session that
produced rows:

    NULL              1831
    stoch_oversold      84
    confluence_low      21
    stoch_overbought    19
    bb_lower_touch      14
    bb_upper_touch       3
    confluence_high      3

**So the predicate has to tolerate both populations at once**, which is why
it is `r.signal_type IS NULL OR r.signal_type = e.signal_type` rather than
a plain equality. A plain equality would match nothing for the 1,831
legacy rows and silently return every pre-2026-08-29 event to the NULL
`fired_at` that `d5e91a7c3b48` existed to fix -- a regression wearing the
shape of a tightening.

**The legacy rows are deliberately not backfilled.** `state_json` carries
indicator state but no signal type, and the events that would have supplied
it are the ones ADR 150 deleted. A guessed value would be fabrication where
NULL is true (invariant 4), and the fallback above is what makes NULL
harmless.

**Behaviour on today's data is unchanged**, which is the point: the
imprecision it removes has never actually fired. It is corrected before it
can, rather than after.

`CREATE OR REPLACE VIEW` keeps the column list and order identical, so no
dependent object is dropped. Applies to both databases -- research has the
view too.
"""

# ruff: noqa: E501 -- NEW_DEF and OLD_DEF are `pg_get_viewdef` output captured
# verbatim, and one join clause is 387 characters. Re-wrapping a view
# definition by hand to satisfy a line limit risks changing what it selects,
# which is a worse trade than a long line in a file that is executed once.
# Scoped to this file, not added to pyproject: db/migrations is exactly where
# CLAUDE.md records an E501 reaching main unnoticed, and a global ignore would
# make that permanent.

from alembic import op

revision = "b7f3c5d21a94"
down_revision = "a4c8d19f6e02"
branch_labels = None
depends_on = None


NEW_DEF = """ SELECT e.ticker,
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
    e.watch_reason
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
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

OLD_DEF = """ SELECT e.ticker,
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
    e.watch_reason
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
          WHERE r.ticker = e.ticker AND (r.fired_at AT TIME ZONE 'America/New_York'::text)::date = e.signal_date) fr ON true
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
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""


def upgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW v_screen_live AS " + NEW_DEF)


def downgrade() -> None:
    op.execute("CREATE OR REPLACE VIEW v_screen_live AS " + OLD_DEF)
