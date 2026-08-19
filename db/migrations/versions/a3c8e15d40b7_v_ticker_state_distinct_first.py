r"""v_ticker_state reads one row per ticker instead of sorting three million

Revision ID: a3c8e15d40b7
Revises: d7f4b91c26ea
Create Date: 2026-08-18 03:50:00.000000

ADR 116. A performance change with no change in output.

**What was slow.** The view joined `bars`, `tickers`, `market_days`, and a
LATERAL over `universe` across every daily indicator row, then applied
`DISTINCT ON (ticker)` to the result. 2,912,426 rows in, 612 out.

Measured 2026-08-18, `max_parallel_workers_per_gather = 0`:

| Query | Before | After |
|---|---|---|
| `SELECT count(*) FROM v_ticker_state` | 23.8 s | 27 ms |
| `SELECT * FROM v_positions WHERE id = 44` | 24.5 s | 23.5 ms |
| `SELECT * FROM v_ticker_state WHERE ticker = 'TSM'` | 17 ms | 1.4 ms |

The single-ticker read was already fast, because the planner pushes a
*constant* predicate down through the `DISTINCT ON`. It cannot push a
*correlated* one, which is why `v_positions` - joining on `p.ticker` - paid
the full 24.5 s to return one row.

**The rewrite is a loose index scan.** Drive off `tickers` (712 rows) and
take one indicator row per ticker with `ORDER BY ts DESC LIMIT 1`, against
a new partial index on `(ticker, ts DESC) WHERE interval = '1d'`.

**Two things that did not work, both measured before this one.**

A lateral over the unchanged view - `LEFT JOIN LATERAL (SELECT * FROM
v_ticker_state WHERE ticker = p.ticker)` - produced an identical plan at
22.7 s. A correlated subquery with no volatile function and no LIMIT gets
pulled up into a plain join, so the lateral was flattened away. The `LIMIT`
in the shipped version is what prevents that, as well as what makes the scan
stop early.

Moving the joins *inside* a `DISTINCT ON` was correct and only 1.7x faster
(13.7 s): it reintroduced the 2.9M-row join to `bars` and an external merge
sort of 74 MB.

**Semantics are preserved, and were checked rather than argued.** With the
original view rebuilt alongside under a second name, `EXCEPT` in both
directions returned zero rows over the full 612-row result.

The lateral filters on `EXISTS (bars)` rather than selecting from
`indicators` alone. The old view inner-joined `bars` and *then* took the
latest surviving row, so a ticker whose newest indicator row had no matching
bar fell through to the next one down; without the EXISTS the rewrite would
drop that ticker instead. Measured across all 2,912,426 daily indicator
rows, zero lack a bar and zero lack a ticker - the filter is kept anyway, so
the behaviour holds structurally rather than because that measurement stays
true.

**Not CONCURRENTLY.** `CREATE INDEX CONCURRENTLY` cannot run inside a
transaction block and Alembic wraps each migration in one. A plain build
took 4.7 s and holds a `SHARE` lock on `indicators` for that time, which
blocks writers - so do not run this while `cscan indicators` or a nightly is
running, the same rule every migration here already carries.

**Verify:**

    cscan db status
    psql -c '\d v_ticker_state'
    psql -c 'SET max_parallel_workers_per_gather=0; \timing on
             SELECT count(*) FROM v_ticker_state;'
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

from capitalscan.jobs.views import (
    V_TICKER_STATE_DDL_PRE_116,
)

revision: str = "a3c8e15d40b7"
down_revision: Union[str, Sequence[str], None] = "d7f4b91c26ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDX_AT_THIS_REVISION = """CREATE INDEX IF NOT EXISTS indicators_daily_latest
    ON public.indicators (ticker, ts DESC)
    WHERE ("interval" = '1d'::text)
"""

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

_V_TICKER_STATE_DDL_AT_THIS_REVISION = """CREATE VIEW public.v_ticker_state AS
 SELECT i.ticker,
    t.name,
    t.sector,
    i.ts AS as_of,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.bb_pctb,
    i.bb_width,
    i.bb_width_pct,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.k_cross_up,
    i.k_cross_down,
    i.sma_200,
    i.sma200_slope_60,
    i.atr_14,
    i.rv_20d,
    i.rv_pct_252d,
    i.vol_z_20d,
    i.dd_52w,
    i.days_to_earnings,
    m.vix_close,
    m.vix_pct_252d,
    m.spx_ret_1d,
    u.in_trade,
    u.mcap_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    (b.close > i.sma_200) AS above_sma200
   FROM ((((public.tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM public.indicators ind
          WHERE ((ind.ticker = t.ticker) AND (ind."interval" = '1d'::text)
              AND (EXISTS ( SELECT 1
                   FROM public.bars bb
                  WHERE ((bb.ticker = ind.ticker) AND (bb.ts = ind.ts)
                      AND (bb."interval" = ind."interval")))))
          ORDER BY ind.ts DESC
         LIMIT 1) latest)
     JOIN public.indicators i ON (((i.ticker = t.ticker) AND (i.ts = latest.ts)
         AND (i."interval" = '1d'::text))))
     JOIN public.bars b ON (((b.ticker = i.ticker) AND (b.ts = i.ts)
         AND (b."interval" = i."interval"))))
     LEFT JOIN public.market_days m ON ((m.ts = (i.ts)::date)))
     LEFT JOIN LATERAL ( SELECT u2.in_trade,
            u2.mcap_usd,
            u2.crit_mcap,
            u2.crit_above_sma200,
            u2.crit_sma200_slope,
            u2.crit_rel_return,
            u2.crit_rev_growth
           FROM public.universe u2
          WHERE ((u2.ticker = i.ticker) AND (u2.as_of <= (i.ts)::date))
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON (true)
"""


def upgrade() -> None:
    op.execute(_IDX_AT_THIS_REVISION)
    # `v_positions` selects from `v_ticker_state`, so the dependent view has
    # to go first. Dropped and recreated rather than `CASCADE`d: a CASCADE
    # would also take anything else that came to depend on it since, and
    # would do so silently.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute("DROP VIEW IF EXISTS public.v_ticker_state")
    op.execute(_V_TICKER_STATE_DDL_AT_THIS_REVISION)
    op.execute(_V_POSITIONS_DDL_AT_THIS_REVISION)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute("DROP VIEW IF EXISTS public.v_ticker_state")
    op.execute(V_TICKER_STATE_DDL_PRE_116)
    op.execute(_V_POSITIONS_DDL_AT_THIS_REVISION)
    op.execute("DROP INDEX IF EXISTS public.indicators_daily_latest")
