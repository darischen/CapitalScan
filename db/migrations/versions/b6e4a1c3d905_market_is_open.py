"""market_is_open(): a live price outside the session is a lie with a timestamp

Revision ID: b6e4a1c3d905
Revises: a4b8f2e619c3
Create Date: 2026-08-19 22:45:00.000000

Reported 2026-08-19: TSLA showed 349.58 as "live" at 15:25 PT against an
official close of 351.12.

    bars_live  TSLA  last tick 15:55:46 ET  349.58
    bars       TSLA  official close         351.12
    now                          18:25 ET

The poller's last tick landed five minutes before the bell and TSLA moved
$1.54 after it. `bars_live` was correct; the page was presenting a session
snapshot two and a half hours after the session ended, with no way for a
reader to tell.

**The date half was already handled and the clock half was not.**
`market_date()` is the ET calendar date, so a weekend or holiday leaves
`session_date = market_date()` unmatched and the price disappears by
itself. A weekday evening matches, which is the window that was wrong --
every evening, on every ticker, since `live_price` moved to `bars_live`
in `c1f7d92a6b45` three hours ago. The defect it replaced was worse
(quotes from July), which is why this surfaced second.

`market_is_open()` is ET 09:30 to 16:00, the same bounds
`poll.py::MARKET_OPEN` and `MARKET_CLOSE` use to decide whether to poll at
all. `test_market_hours.py` asserts the two agree; they cannot be imported
from one another, one being Python and one SQL, so the guarantee is a
test rather than a shared constant.

`v_screen_live.live_price` is NULL outside the session. `live_price_ts` is
kept, so a reader who wants to know when the last tick landed still can --
what goes away is the number pretending to be current.

Half-days close at 13:00 ET and are not modelled: roughly nine afternoons
a year show a stale price for three hours. Modelling them needs a holiday
calendar carrying session lengths, which nothing here has.

**Per ADR 125 the SQL below is a literal, never an import.**

**Verify:**

    psql -c "SELECT market_is_open(), (now() AT TIME ZONE 'America/New_York')::time"
    psql -c "SELECT count(*) FILTER (WHERE live_price IS NOT NULL) FROM v_screen_live"
"""

from alembic import op

revision = "b6e4a1c3d905"
down_revision = "a4b8f2e619c3"
branch_labels = None
depends_on = None

FUNCTION_DDL = """CREATE OR REPLACE FUNCTION public.market_is_open() RETURNS boolean
    LANGUAGE sql
    STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::time
                 >= TIME '09:30'
             AND (now() AT TIME ZONE 'America/New_York')::time
                 <  TIME '16:00' $$
"""

# `v_screen_live` with `live_price` guarded by the session clock.
NEW_DDL = """\nCREATE VIEW public.v_screen_live AS
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
        CASE WHEN market_is_open() THEN lq.close ELSE NULL::numeric END AS live_price,
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

# `v_screen_live` as `c1f7d92a6b45` left it: correct source, no clock.
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


def upgrade() -> None:
    op.execute(FUNCTION_DDL)
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(NEW_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(OLD_DDL)
    # Dropped after the view, which depends on it. `market_date()` is not
    # touched: it predates this revision.
    op.execute("DROP FUNCTION IF EXISTS public.market_is_open()")
