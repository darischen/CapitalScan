r"""the screener views take sector from tickers, not from the dead events column

Revision ID: b8f31c204e7a
Revises: e5b2a7c81f39
Create Date: 2026-08-19 04:05:00.000000

`v_screen` and `v_screen_live` projected `e.sector`. It is **NULL on all
13,479,819 rows** — measured across the whole table, not sampled — while
`tickers.sector` carries 502 populated. The column exists on `events` and
nothing has ever written it.

`v_events` already got this right: it joins `tickers` and selects
`t.sector`. The two screener views did not, so a consumer reading sector off
them received null and had no way to tell that apart from a ticker whose
sector is genuinely unknown.

**A join, not a backfill.** Copying sector onto 13.5M existing rows is a
large write for data a join already has, and it would go stale the next time
a ticker is reclassified. `events.sector` stays in the table — dropping a
column is a separate decision with its own blast radius, and nothing reads
it now.

**Inner join, matching `v_events`.** Zero events lack a matching ticker, so
it drops nothing; an outer join would mask a referential break rather than
report it.

The `FROM` clauses are also flattened from nested parentheses to plain
joins. Adding a third join to the nested form meant recounting brackets
across two views for no gain, and `pg_dump` re-parenthesises on round trip
regardless — `db/schema.sql` is regenerated from the database, so the
checked-in form is whatever Postgres emits either way.

**Verify:**

    cscan db status
    psql -c "SELECT count(sector) FROM v_screen_live"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

from capitalscan.jobs.views import (
    V_SCREEN_DDL,
    V_SCREEN_LIVE_DDL,
)

revision: str = "b8f31c204e7a"
down_revision: Union[str, Sequence[str], None] = "e5b2a7c81f39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The two views exactly as ADR 119 left them, captured with
# `pg_get_viewdef` from the deployed database rather than retyped, so
# `downgrade()` restores what was actually there -- dead column included,
# because a downgrade that quietly kept the fix would not be a downgrade.
#
# **Both are restored.** An earlier draft dropped `v_screen_live` and left
# it dropped, which would have put the chain in a state e5b2a7c81f39 never
# produced: that migration creates the view, so downgrading past this one
# has to hand it back.
_V_SCREEN_PRE = """
CREATE VIEW public.v_screen AS
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
    e.sector,
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
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type
         AND c.side = e.side
         AND c.dd_bucket = e.dd_bucket
         AND c.signal_strength IS NULL
         AND c.entry_kind = 'next_open'::text
         AND c.split_key = 'validate'::text
         AND c.era IS NULL
         AND c.horizon_days = 5
         AND c.target_pct = 0.03
         AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
         AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.is_cluster_head
      AND e.entry_kind = 'next_open'::text
      AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
"""

_V_SCREEN_LIVE_PRE = """
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
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.cofire_count,
    e.sector,
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
     LEFT JOIN LATERAL ( SELECT q.price,
            q.ts
           FROM quotes_live q
          WHERE q.ticker = e.ticker
          ORDER BY q.ts DESC
         LIMIT 1) lq ON true
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM signal_reports r
          WHERE r.event_id = e.id) fr ON true
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type
         AND c.side = e.side
         AND c.dd_bucket = e.dd_bucket
         AND c.signal_strength IS NULL
         AND c.entry_kind = 'next_open'::text
         AND c.split_key = 'validate'::text
         AND c.era IS NULL
         AND c.horizon_days = 5
         AND c.target_pct = 0.03
         AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
         AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.ticker = e.ticker AND p.as_of = e.signal_date
  WHERE e.entry_kind = 'touch'::text
      AND e.is_cluster_head IS NOT FALSE
      AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
"""


def upgrade() -> None:
    # `v_screen_live` first: neither depends on the other, but dropping in
    # the reverse of creation order keeps the two blocks readable.
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(V_SCREEN_DDL)
    op.execute(V_SCREEN_LIVE_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(_V_SCREEN_PRE)
    op.execute(_V_SCREEN_LIVE_PRE)
