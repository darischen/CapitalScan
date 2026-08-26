"""events.watch_reason, and the site can see the watch universe

Revision ID: a4f8c21d7e63
Revises: c8d3a1f70b25
Create Date: 2026-08-26 11:05:00.000000

**This deliberately overrides `c8d3a1f70b25`. User decision, 2026-08-26.**

That migration built `v_watchlist` as a *separate* surface and said why:

    Widening `v_screen` to `in_trade OR in_watch` would pull watched names
    into the surface that joins `cell_stats`, which is precisely what ADR
    149 promises not to do.

This revision does widen it, with the watch rows marked. The reasoning was
put to the user with that objection stated, and the call was to show CCJ on
the surface people actually read rather than on a second one they have to
know exists.

**One half of the objection is answered rather than overridden.** It also
said a `p_hit` column would be "a NULL that always means 'not applicable',
indistinguishable from one that means 'suppressed'". This sets
`suppress_reason = 'watch_universe'`, so the two are distinguishable and
the frontend can say which it is. What is genuinely overridden is the
population-purity argument: watched names now appear in a view that joins
`cell_stats`, with every statistic suppressed and labelled.

**`v_watchlist` is left in place.** Nothing reads it today, and dropping a
view in the same change that supersedes its purpose makes rollback two
decisions instead of one. It should be retired in its own revision once
this has run a week.

Reported 2026-08-26: CCJ fired `confluence_high` at 06:45:40, the poller
logged it, and the home page never showed it.

    CCJ  in_trade=f  in_watch=t
    universe 2026-06-30: crit_above_sma200 = f  (mcap, slope, rel_return pass)

Nothing was broken. CCJ is below its 200-day SMA, which ADR 149's
`pullback` route admits to **watch** rather than trade, and every surface
that reads events filters `in_trade`. **27 of that day's 164 fires were in
the same position** -- `pullback` 23, `history` 4 -- and `in_watch`
appeared nowhere in the web layer and in no view definition. The whole
population was write-only as far as the site was concerned, which defeats
the point of a universe that exists to be *detected* on while staying out
of training.

**`watch_reason` is stamped on the event, not joined at read time.**
`universe.watch_reason` is the only place the reason lives today, and
membership moves quarterly. A read-time join would answer "why is CCJ
watched *now*", so a March `history` fire would silently relabel itself
`pullback` the day CCJ dropped below its average -- no error, no migration,
no code change, the column just quietly means something else. That is the
defect ADR 122 fixed by stamping `in_trade`, and it is why the backfill
below joins `universe` at each event's own `as_of` rather than the latest.

**Statistics are suppressed on watch rows.** ADR 149: "no statistic reads
`in_watch`." `cell_stats` is computed over the trade universe, so printing
`p_hit` beside a watch row attributes one population's number to a name
outside it. `v_screen_live` already carries a `suppressed` /
`suppress_reason` pair for exactly this, so the new state rides the
mechanism that exists rather than adding a second one -- and the frontend
needs no change to honour it.

**The membership filter lives in `v_screen_live`, not only in the
application.** `web/lib/screen.ts`'s `FEED_DOMAIN` filters `in_trade` for
the calendar and the default-date query, and the view filters it again for
the rows. Both move in this change; changing one would leave a calendar
that offers dates the table cannot fill.

Backfill size: **448,566** rows where `in_watch`.

**Run this before a nightly, never after.** It is DDL on `events`, which
`run_events` writes, and the `run_events` / `poll.py` change that stamps
the column ships in the same commit -- so migrating first means the
night's rows carry the reason from birth rather than needing a second
backfill.
"""

from alembic import op

revision = "a4f8c21d7e63"
down_revision = "c8d3a1f70b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. The column. Nullable with no default, so this is a catalogue-only
    #    change in Postgres 11+ -- no table rewrite, and ACCESS EXCLUSIVE is
    #    held for a catalogue update rather than a 1.4M-row scan.
    op.execute("ALTER TABLE events ADD COLUMN watch_reason text")

    # 2. Backfill, point-in-time. The lateral takes the newest `universe`
    #    snapshot **at or before the event's own signal_date**, which is what
    #    makes the value a fact about that fire rather than about today.
    #    Restricted to `in_watch`: a trade-universe event has no watch reason
    #    and NULL is the honest answer, not ''.
    op.execute(
        """
        UPDATE events e
           SET watch_reason = u.watch_reason
          FROM LATERAL (
                 SELECT u2.watch_reason
                   FROM universe u2
                  WHERE u2.ticker = e.ticker
                    AND u2.as_of <= e.signal_date
                  ORDER BY u2.as_of DESC
                  LIMIT 1
               ) u
         WHERE e.in_watch
        """
    )

    # 3. The views. CREATE OR REPLACE rather than DROP/CREATE keeps the
    #    grants (`capscan_ro` holds SELECT on both, ADR 027), and is legal
    #    because every existing column keeps its name, type and position
    #    while the new ones are appended.
    op.execute(
        """CREATE OR REPLACE VIEW v_ticker_state AS
SELECT i.ticker, t.name, t.sector, i.ts AS as_of, b.close, b.volume,
    i.bb_lower, i.bb_mid, i.bb_upper, i.bb_pctb, i.bb_width, i.bb_width_pct,
    i.k_full, i.d_full, i.k_fast, i.k_cross_up, i.k_cross_down,
    i.sma_200, i.sma200_slope_60, i.atr_14, i.rv_20d, i.rv_pct_252d,
    i.vol_z_20d, i.dd_52w, i.days_to_earnings,
    m.vix_close, m.vix_pct_252d, m.spx_ret_1d,
    u.in_trade, u.mcap_usd, u.crit_mcap, u.crit_above_sma200,
    u.crit_sma200_slope, u.crit_rel_return, u.crit_rev_growth,
    b.close > i.sma_200 AS above_sma200,
    u.in_watch, u.watch_reason
   FROM tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM indicators ind
          WHERE ind.ticker = t.ticker AND ind."interval" = '1d'::text AND (EXISTS ( SELECT 1
                   FROM bars bb
                  WHERE bb.ticker = ind.ticker AND bb.ts = ind.ts AND
                      bb."interval" = ind."interval"))
          ORDER BY ind.ts DESC
         LIMIT 1) latest
     JOIN indicators i ON i.ticker = t.ticker AND i.ts = latest.ts AND i."interval" = '1d'::text
     JOIN bars b ON b.ticker = i.ticker AND b.ts = i.ts AND b."interval" = i."interval"
     LEFT JOIN market_days m ON m.ts = i.ts::date
     LEFT JOIN LATERAL ( SELECT u2.in_trade, u2.mcap_usd, u2.crit_mcap,
            u2.crit_above_sma200, u2.crit_sma200_slope, u2.crit_rel_return,
            u2.crit_rev_growth, u2.in_watch, u2.watch_reason
           FROM universe u2
          WHERE u2.ticker = i.ticker AND u2.as_of <= i.ts::date
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON true"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW v_screen_live AS
SELECT e.ticker,
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
    (c.suppressed OR NOT e.in_trade) AS suppressed,
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
     LEFT JOIN bars b ON b.ticker = e.ticker AND b.ts = e.signal_date AND
         b."interval" = '1d'::text
     LEFT JOIN bars_live lq ON lq.ticker = e.ticker AND lq.session_date = market_date()
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.event_id = e.id) fr ON true
     LEFT JOIN LATERAL (
         SELECT ((r2.state_json -> 'bear_reversal'::text)
             ->> 'confirmed'::text)::boolean AS confirmed,
            ((r2.state_json -> 'bear_reversal'::text)
                ->> 'above_band'::text)::boolean AS above_band,
            ((r2.state_json -> 'bear_reversal'::text)
                ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM signal_reports r2
          WHERE r2.event_id = e.id AND r2.state_json ? 'bear_reversal'::text
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON true
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND
         c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND
         c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND
         c.horizon_days = 5 AND c.target_pct = 0.03 AND
         c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND
         c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.entry_kind = 'touch'::text AND (e.in_trade OR e.in_watch) AND
      e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""
    )


def downgrade() -> None:
    # Views first: both project `events.watch_reason`, so dropping the
    # column while they still reference it fails on the dependency.
    op.execute(
        """CREATE OR REPLACE VIEW v_screen_live AS
SELECT e.ticker,
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
    p.model_version
   FROM events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower,
            i2.bb_mid,
            i2.bb_upper,
            i2.ts
           FROM indicators i2
          WHERE i2.ticker = e.ticker AND i2."interval" = '1d'::text AND i2.ts < e.signal_date
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON true
     LEFT JOIN bars b ON b.ticker = e.ticker AND b.ts = e.signal_date AND
         b."interval" = '1d'::text
     LEFT JOIN bars_live lq ON lq.ticker = e.ticker AND lq.session_date = market_date()
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.event_id = e.id) fr ON true
     LEFT JOIN LATERAL (
         SELECT ((r2.state_json -> 'bear_reversal'::text)
             ->> 'confirmed'::text)::boolean AS confirmed,
            ((r2.state_json -> 'bear_reversal'::text)
                ->> 'above_band'::text)::boolean AS above_band,
            ((r2.state_json -> 'bear_reversal'::text)
                ->> 'open_gap_atr'::text)::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM signal_reports r2
          WHERE r2.event_id = e.id AND r2.state_json ? 'bear_reversal'::text
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON true
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND
         c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND
         c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND
         c.horizon_days = 5 AND c.target_pct = 0.03 AND
         c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND
         c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.entry_kind = 'touch'::text AND e.in_trade AND
      e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW v_ticker_state AS
SELECT i.ticker, t.name, t.sector, i.ts AS as_of, b.close, b.volume,
    i.bb_lower, i.bb_mid, i.bb_upper, i.bb_pctb, i.bb_width, i.bb_width_pct,
    i.k_full, i.d_full, i.k_fast, i.k_cross_up, i.k_cross_down,
    i.sma_200, i.sma200_slope_60, i.atr_14, i.rv_20d, i.rv_pct_252d,
    i.vol_z_20d, i.dd_52w, i.days_to_earnings,
    m.vix_close, m.vix_pct_252d, m.spx_ret_1d,
    u.in_trade, u.mcap_usd, u.crit_mcap, u.crit_above_sma200,
    u.crit_sma200_slope, u.crit_rel_return, u.crit_rev_growth,
    b.close > i.sma_200 AS above_sma200
   FROM tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM indicators ind
          WHERE ind.ticker = t.ticker AND ind."interval" = '1d'::text AND (EXISTS ( SELECT 1
                   FROM bars bb
                  WHERE bb.ticker = ind.ticker AND bb.ts = ind.ts AND
                      bb."interval" = ind."interval"))
          ORDER BY ind.ts DESC
         LIMIT 1) latest
     JOIN indicators i ON i.ticker = t.ticker AND i.ts = latest.ts AND i."interval" = '1d'::text
     JOIN bars b ON b.ticker = i.ticker AND b.ts = i.ts AND b."interval" = i."interval"
     LEFT JOIN market_days m ON m.ts = i.ts::date
     LEFT JOIN LATERAL ( SELECT u2.in_trade, u2.mcap_usd, u2.crit_mcap,
            u2.crit_above_sma200, u2.crit_sma200_slope, u2.crit_rel_return,
            u2.crit_rev_growth
           FROM universe u2
          WHERE u2.ticker = i.ticker AND u2.as_of <= i.ts::date
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON true"""
    )
    op.execute("ALTER TABLE events DROP COLUMN watch_reason")
