r"""a live screener view, one definition of the market's date, and v_screen's missing config filter

Revision ID: e5b2a7c81f39
Revises: a3c8e15d40b7
Create Date: 2026-08-19 02:30:00.000000

ADR 119. Three changes, found together while building `/`.

**1. `v_screen` showed events from every config.**

ADR 100 says the view carries a `config_hash` predicate, and it did — on the
`cell_stats` join only, never on `events`. `v_events` filters correctly; this
one did not.

Measured 2026-08-18 on the newest date the screener could show: **46 rows, of
which 17 belonged to a superseded config** and 29 to the live one, mixed with
nothing to tell them apart. Across the whole view, 23 distinct `config_hash`
values.

**2. `v_screen` cannot answer "what fired today".**

It filters `entry_kind = 'next_open'`, and only `cscan backtest` writes that
kind — `run_events` writes `touch` (`compute.py:801`) and so does the poller.
So it trails the last full backtest, a five-hour job. Measured: newest
`next_open` 2026-08-13, while 67 events had fired that day.

`v_screen_live` is the detection-time feed. Three deliberate differences:

- `entry_kind = 'touch'` — what fired, when it fired.
- `is_cluster_head IS NOT FALSE` rather than `is_cluster_head`. The poller
  writes one row per breach and **cannot** cluster: ADR 054's gap window
  needs the whole session, which does not exist at 09:35. Its rows carry
  NULL, so `WHERE is_cluster_head` returns **zero rows intraday** — measured,
  0 of today's 67. NULL means "not yet clustered", which is not "not a head".
- The cell join pins `entry_kind = 'next_open'`, because that is the entry
  the grid measured (`GRID_ENTRY_KIND`). The feed's grain and the
  statistics' grain differ on purpose: a feed is a detection-time question,
  a hit rate is a question about an entry that was actually simulated.

**3. `CURRENT_DATE` is not the market's date.**

The database runs `Etc/UTC`. Measured at 2026-08-19 02:15 UTC, `CURRENT_DATE`
returned **2026-08-19** while the session that had just closed was
**2026-08-18**. Every use of it to mean "today" is wrong from 00:00 UTC until
midnight ET — roughly seven hours a day, 5pm to midnight Pacific, which is
exactly when someone reviews the session that just ended.

`market_date()` is the one definition. `v_positions` adopts it, which fixes
`days_held` and `exit_signal_timeout` over-counting by one session every
evening. `STABLE`, not `IMMUTABLE`: it reads the clock.

**Verify:**

    cscan db status
    psql -c 'SELECT market_date(), CURRENT_DATE'
    psql -c 'SELECT count(*) FROM v_screen_live WHERE signal_date = market_date()'
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

from capitalscan.jobs.views import (
    V_SCREEN_DDL_PRE_119,
)

revision: str = "e5b2a7c81f39"
down_revision: Union[str, Sequence[str], None] = "a3c8e15d40b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Frozen DDL. **Do not import these from `jobs/views.py`.**
# ---------------------------------------------------------------------------
#
# A migration is a statement about one point in history; `jobs/views.py`
# holds the *current* definition. Importing the live constant makes an old
# migration emit tomorrow's SQL, and it breaks the moment a later migration
# changes the object.
#
# It did, on 2026-08-19: ADR 122 added `events.in_trade`, and four earlier
# migrations that imported `V_SCREEN_LIVE_DDL` began emitting
# `AND e.in_trade` against a table without the column. Every from-scratch
# replay failed with `UndefinedColumn`. **Invisible locally** -- a developer
# applies only the new migrations, and only a full replay hits it, which is
# what CI and any new deployment do.
#
# These are literals, captured as the objects stood at this revision.
# `test_migrations_freeze_ddl.py` refuses any new import of a live one.

_MARKET_DATE_DDL_AT_THIS_REVISION = """CREATE OR REPLACE FUNCTION public.market_date() RETURNS date
    LANGUAGE sql
    STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::date $$
"""

_V_POSITIONS_DDL_AT_THIS_REVISION = """CREATE VIEW public.v_positions AS
 SELECT p.id,
    p.user_id,
    p.ticker,
    p.side,
    p.entry_date,
    p.entry_price,
    p.quantity,
    p.source,
    p.status,
    p.exit_date,
    p.exit_price,
    p.exit_reason,
    p.realized_ret,
    p.idempotency_key,
    p.created_at,
    s.close AS current_price,
        CASE
            WHEN (p.status = 'open'::text) THEN ((s.close - p.entry_price) / p.entry_price)
            ELSE p.realized_ret
        END AS unrealized_or_realized_ret,
    ( SELECT count(*) AS count
        FROM public.trading_days td
       WHERE ((td.d > p.entry_date) AND (td.d <= CURRENT_DATE))) AS days_held,
        CASE
            WHEN (NOT c.exit_on_stoch_80) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN ((CASE
                    WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                    ELSE s.k_full
                END) <= c.exit_stoch_threshold_short)
            ELSE ((CASE
                    WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                    ELSE s.k_full
                END) >= c.exit_stoch_threshold)
        END AS exit_signal_stoch,
        CASE
            WHEN (NOT c.exit_on_upper_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_lower)
            ELSE (s.close >= s.bb_upper)
        END AS exit_signal_upper_band,
        CASE
            WHEN (NOT c.exit_on_mid_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_mid)
            ELSE (s.close >= s.bb_mid)
        END AS exit_signal_mid_band,
    (( SELECT count(*) AS count
         FROM public.trading_days td
        WHERE ((td.d > p.entry_date) AND (td.d <= CURRENT_DATE)))
        >= c.max_hold_days) AS exit_signal_timeout,
        CASE
            WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
            ELSE s.k_full
        END AS exit_stoch_k
   FROM ((public.positions p
     LEFT JOIN public.v_ticker_state s ON ((s.ticker = p.ticker)))
     LEFT JOIN public.serving_config c ON (true))
"""

_V_POSITIONS_DDL_MARKET_DATE_AT_THIS_REVISION = """CREATE VIEW public.v_positions AS
 SELECT p.id,
    p.user_id,
    p.ticker,
    p.side,
    p.entry_date,
    p.entry_price,
    p.quantity,
    p.source,
    p.status,
    p.exit_date,
    p.exit_price,
    p.exit_reason,
    p.realized_ret,
    p.idempotency_key,
    p.created_at,
    s.close AS current_price,
        CASE
            WHEN (p.status = 'open'::text) THEN ((s.close - p.entry_price) / p.entry_price)
            ELSE p.realized_ret
        END AS unrealized_or_realized_ret,
    ( SELECT count(*) AS count
        FROM public.trading_days td
       WHERE ((td.d > p.entry_date) AND (td.d <= public.market_date()))) AS days_held,
        CASE
            WHEN (NOT c.exit_on_stoch_80) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN ((CASE
                    WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                    ELSE s.k_full
                END) <= c.exit_stoch_threshold_short)
            ELSE ((CASE
                    WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                    ELSE s.k_full
                END) >= c.exit_stoch_threshold)
        END AS exit_signal_stoch,
        CASE
            WHEN (NOT c.exit_on_upper_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_lower)
            ELSE (s.close >= s.bb_upper)
        END AS exit_signal_upper_band,
        CASE
            WHEN (NOT c.exit_on_mid_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_mid)
            ELSE (s.close >= s.bb_mid)
        END AS exit_signal_mid_band,
    (( SELECT count(*) AS count
         FROM public.trading_days td
        WHERE ((td.d > p.entry_date) AND (td.d <= public.market_date())))
        >= c.max_hold_days) AS exit_signal_timeout,
        CASE
            WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
            ELSE s.k_full
        END AS exit_stoch_k
   FROM ((public.positions p
     LEFT JOIN public.v_ticker_state s ON ((s.ticker = p.ticker)))
     LEFT JOIN public.serving_config c ON (true))
"""


# ---------------------------------------------------------------------------
# Frozen DDL. **Do not import these from `jobs/views.py`.**
# ---------------------------------------------------------------------------
#
# A migration is a statement about one point in history; `jobs/views.py`
# holds the *current* definition. Importing the live constant makes an old
# migration emit tomorrow's SQL, and it breaks the moment a later migration
# adds a column.
#
# It did, on 2026-08-19. ADR 122 added `events.in_trade` in `f2d16b47c093`,
# which runs *after* this revision, and both screener views here started
# emitting `AND e.in_trade` against a table that does not have the column
# yet. Every fresh database failed with
# `UndefinedColumn: column e.in_trade does not exist`.
#
# **It was invisible locally**, because a developer applies only the new
# migrations; only a from-scratch replay hits it, which is exactly what CI
# does and what a new deployment would do.
#
# So these are literals, captured as the views stood at this revision.

_V_SCREEN_AT_THIS_REVISION = """CREATE VIEW public.v_screen AS
 SELECT e.ticker,
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
    p.model_version

   FROM public.events e

     JOIN public.tickers t ON (t.ticker = e.ticker)
     LEFT JOIN public.cell_stats c ON ((c.signal_type = e.signal_type)
         AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket)
         AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text)
         AND (c.split_key = 'validate'::text) AND (c.era IS NULL)
         AND (c.horizon_days = 5) AND (c.target_pct = 0.03)
         AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
         AND (c.arm = 'signal'::text))
     LEFT JOIN public.predictions p ON ((p.ticker = e.ticker) AND (p.as_of = e.signal_date))

  WHERE (e.is_cluster_head AND (e.entry_kind = 'next_open'::text)
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""

_V_SCREEN_LIVE_AT_THIS_REVISION = """CREATE VIEW public.v_screen_live AS
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

     JOIN public.tickers t ON (t.ticker = e.ticker)
     LEFT JOIN public.cell_stats c ON ((c.signal_type = e.signal_type)
         AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket)
         AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text)
         AND (c.split_key = 'validate'::text) AND (c.era IS NULL)
         AND (c.horizon_days = 5) AND (c.target_pct = 0.03)
         AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
         AND (c.arm = 'signal'::text))
     LEFT JOIN public.predictions p ON ((p.ticker = e.ticker) AND (p.as_of = e.signal_date))

  WHERE ((e.entry_kind = 'touch'::text) AND (e.is_cluster_head IS NOT FALSE)
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""


def upgrade() -> None:
    op.execute(_MARKET_DATE_DDL_AT_THIS_REVISION)

    # `v_positions` depends on the function, so the function goes first.
    # Dropped and recreated rather than replaced: `CREATE OR REPLACE VIEW`
    # cannot change a column's type, and although these two columns keep
    # theirs, a drop is unambiguous and this view is rebuilt often enough
    # that the pattern is worth keeping consistent.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(_V_POSITIONS_DDL_MARKET_DATE_AT_THIS_REVISION)

    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(_V_SCREEN_AT_THIS_REVISION)
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(_V_SCREEN_LIVE_AT_THIS_REVISION)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(V_SCREEN_DDL_PRE_119)

    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(_V_POSITIONS_DDL_AT_THIS_REVISION)

    # After the view stops referencing it. A `DROP FUNCTION` with a
    # dependent view fails rather than cascading, which is the safe default
    # and the reason the order matters here.
    op.execute("DROP FUNCTION IF EXISTS public.market_date()")
