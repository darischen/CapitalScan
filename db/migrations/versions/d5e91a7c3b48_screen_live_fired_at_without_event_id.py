"""v_screen_live must not resolve fired_at through event_id

Revision ID: d5e91a7c3b48
Revises: c93f4a1e77b2
Create Date: 2026-08-28 15:10:00.000000

**Every intraday detection vanished from the site after each nightly, and
only the 06:45 opening burst survived.** Reported from the frontend on
2026-08-28, the first full session the Pi polled under ADR 158.

Two designs, each correct alone, that contradict each other:

- `_sweep_provisional_poll_rows` (ADR 150) sets `signal_reports.event_id =
  NULL` and deletes the provisional event. Its stated reasoning is that the
  report is self-contained -- `ticker`, `fired_at` and `state_json` are all
  NOT NULL -- so nulling the link "preserves the observation".
- `v_screen_live` resolved `fired_at` **only** through `r.event_id = e.id`.

The premise is true of the table and false of the view that reads it. After
the 2026-08-28 nightly, of 175 reports for the day:

    resolves -> site shows it     90    06:45 .. 06:45
    event_id NULL (swept)         61    06:45 .. 12:52
    dangling                      24    07:04 .. 12:37

Every row the site could still render had fired at the open. Each of the 85
it could not was an intraday fire. The blanks and the uniform 06:45 are the
same defect seen from two sides.

**Matched on `(ticker, signal_date)`, which is what the sweep guarantees.**
Both columns are NOT NULL on `signal_reports` and neither is touched by the
sweep, so the link survives what `event_id` does not.

`fired_at` is `timestamptz` and `signal_date` is the ET trading date, so the
comparison converts explicitly (`AT TIME ZONE 'America/New_York'`). Reading
it as a UTC date would shift every fire after 17:00 ET onto the next day.
Not `CURRENT_DATE` anywhere, per ADR 119.

**One accepted imprecision.** `signal_reports` has no `signal_type` column
and `state_json` carries no signal type -- only `ticker` -- so a ticker that
fires two different signal types on one day gives both events the same
earliest `fired_at`. Exact in practice today (all 157 linked events had
exactly one report), and strictly better than the NULL it replaces. Storing
`signal_type` on the report is the real fix and is in `BACKLOG.md`.

`min()` is kept rather than `max()`: the column means "when did this first
fire today", which is what the screener sorts on (`s.fired_at DESC`).

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

revision = "d5e91a7c3b48"
down_revision = "c93f4a1e77b2"
branch_labels = None
depends_on = None


NEW_DEF = """SELECT e.ticker,
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
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.ticker = e.ticker
            AND (r.fired_at AT TIME ZONE 'America/New_York')::date = e.signal_date) fr ON true
     LEFT JOIN LATERAL ( SELECT ((r2.state_json -> 'bear_reversal'::text) ->> 'confirmed'::text)::boolean AS confirmed,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'above_band'::text)::boolean AS above_band,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM signal_reports r2
          WHERE r2.ticker = e.ticker
            AND (r2.fired_at AT TIME ZONE 'America/New_York')::date = e.signal_date
            AND r2.state_json ? 'bear_reversal'::text
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON true
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

OLD_DEF = """SELECT e.ticker,
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
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.event_id = e.id) fr ON true
     LEFT JOIN LATERAL ( SELECT ((r2.state_json -> 'bear_reversal'::text) ->> 'confirmed'::text)::boolean AS confirmed,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'above_band'::text)::boolean AS above_band,
            ((r2.state_json -> 'bear_reversal'::text) ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM signal_reports r2
          WHERE r2.event_id = e.id AND r2.state_json ? 'bear_reversal'::text
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
