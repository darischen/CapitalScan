"""v_screen_live: live_price from bars_live, and bb_pctb projected

Revision ID: c1f7d92a6b45
Revises: d5c17e9b3a02
Create Date: 2026-08-19 21:05:00.000000

Reported 2026-08-19: ROST showed 238.72 on the screener and the ticker
page hours after the close, while the candle beside it showed 234.63.
Both are "the current price" and they differed by four dollars.

    bars_live    ROST  15:55 ET  close 234.63
    quotes_live  ROST  09:36 ET  price 238.715

`poll.py` writes `quotes_live` **inside its breach loop**, so a ticker
gets a row only on a tick where it fired. ROST fired once, at 09:36 ET,
and never again all session. ADR 128 fixed exactly this for bars and
left quotes alone.

The lateral also had **no date bound** -- `ORDER BY q.ts DESC LIMIT 1`
over the whole table -- so a ticker whose last signal was in July
reported a July price in a column labelled live.

`bars_live` is rewritten every tick for every ticker the poller covers,
so `close` is the current price by construction. The lateral becomes an
equality join on `(ticker, session_date)`, which is that table's primary
key.

**`bb_pctb` is added in the same revision.** `v_screen` projects it and
`v_screen_live` did not, which is the one column that differed between the
two feeds. `screen_signals` now takes a `grain` argument selecting between
them (backlog item 2), and a handler whose column list changes with the
grain would be two contracts wearing one name. `events.bb_pctb` is stored,
so this is a projection and nothing is computed.

**One behaviour change beyond the fix.** `live_price` is now NULL
outside a session and for any ticker the poller does not cover. The old
lateral always found *a* row, however old. Null is the honest answer and
the column renders as an em-dash; `live_price_ts` remains beside it for
the case where a price is present.

`quotes_live` is untouched and keeps its job: the record of what price a
signal fired at. It simply stops being a display source.

**Per ADR 125 the SQL below is a literal, never an import.** Importing
`V_SCREEN_LIVE_DDL` would make this migration emit whatever that constant
says at replay time, which is how four migrations began emitting
`AND e.in_trade` against a table that had no such column yet.

**Verify:**

    cscan db status
    psql -c "SELECT ticker, live_price, live_price_ts FROM v_screen_live LIMIT 5"
"""

from alembic import op

revision = "c1f7d92a6b45"
down_revision = "d5c17e9b3a02"
branch_labels = None
depends_on = None

# `v_screen_live` with `live_price` taken from `bars_live`.
NEW_DDL = """\
CREATE VIEW public.v_screen_live AS
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
    lq.close AS live_price,
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

   FROM public.events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower, i2.bb_mid, i2.bb_upper, i2.ts
           FROM public.indicators i2
          WHERE ((i2.ticker = e.ticker) AND (i2."interval" = '1d'::text)
              AND (i2.ts < e.signal_date))
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON (true)
     LEFT JOIN public.bars b ON ((b.ticker = e.ticker) AND (b.ts = e.signal_date)
         AND (b."interval" = '1d'::text))
     LEFT JOIN public.bars_live lq ON ((lq.ticker = e.ticker)
         AND (lq.session_date = market_date()))
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM public.signal_reports r
          WHERE (r.event_id = e.id)) fr ON (true)
     LEFT JOIN LATERAL ( SELECT
              (r2.state_json -> 'bear_reversal' ->> 'confirmed')::boolean AS confirmed,
              (r2.state_json -> 'bear_reversal' ->> 'above_band')::boolean AS above_band,
              (r2.state_json -> 'bear_reversal' ->> 'open_gap_atr')::numeric AS open_gap_atr,
              r2.fired_at AS rev_ts
           FROM public.signal_reports r2
          WHERE ((r2.event_id = e.id)
              AND (r2.state_json ? 'bear_reversal'))
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON (true)

     JOIN public.tickers t ON (t.ticker = e.ticker)
     LEFT JOIN public.cell_stats c ON ((c.signal_type = e.signal_type)
         AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket)
         AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text)
         AND (c.split_key = 'validate'::text) AND (c.era IS NULL)
         AND (c.horizon_days = 5) AND (c.target_pct = 0.03)
         AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
         AND (c.arm = 'signal'::text))
     LEFT JOIN public.predictions p ON ((p.ticker = e.ticker) AND (p.as_of = e.signal_date))

  WHERE ((e.entry_kind = 'touch'::text)
     AND e.in_trade
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""

# `v_screen_live` as it stood before this revision, kept verbatim so
# `downgrade()` restores it exactly -- including the stale-quote defect,
# because a downgrade that quietly kept the fix would not be a downgrade.
OLD_DDL = """\nCREATE VIEW public.v_screen_live AS
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
    lq.price AS live_price,
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

   FROM public.events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower, i2.bb_mid, i2.bb_upper, i2.ts
           FROM public.indicators i2
          WHERE ((i2.ticker = e.ticker) AND (i2."interval" = '1d'::text)
              AND (i2.ts < e.signal_date))
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON (true)
     LEFT JOIN public.bars b ON ((b.ticker = e.ticker) AND (b.ts = e.signal_date)
         AND (b."interval" = '1d'::text))
     LEFT JOIN LATERAL ( SELECT q.price, q.ts
           FROM public.quotes_live q
          WHERE (q.ticker = e.ticker)
          ORDER BY q.ts DESC
         LIMIT 1) lq ON (true)
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM public.signal_reports r
          WHERE (r.event_id = e.id)) fr ON (true)
     LEFT JOIN LATERAL ( SELECT
              (r2.state_json -> 'bear_reversal' ->> 'confirmed')::boolean AS confirmed,
              (r2.state_json -> 'bear_reversal' ->> 'above_band')::boolean AS above_band,
              (r2.state_json -> 'bear_reversal' ->> 'open_gap_atr')::numeric AS open_gap_atr,
              r2.fired_at AS rev_ts
           FROM public.signal_reports r2
          WHERE ((r2.event_id = e.id)
              AND (r2.state_json ? 'bear_reversal'))
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON (true)

     JOIN public.tickers t ON (t.ticker = e.ticker)
     LEFT JOIN public.cell_stats c ON ((c.signal_type = e.signal_type)
         AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket)
         AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text)
         AND (c.split_key = 'validate'::text) AND (c.era IS NULL)
         AND (c.horizon_days = 5) AND (c.target_pct = 0.03)
         AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
         AND (c.arm = 'signal'::text))
     LEFT JOIN public.predictions p ON ((p.ticker = e.ticker) AND (p.as_of = e.signal_date))

  WHERE ((e.entry_kind = 'touch'::text)
     AND e.in_trade
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(NEW_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(OLD_DDL)
