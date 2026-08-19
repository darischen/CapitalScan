r"""events records trade-universe membership instead of being filtered by it

Revision ID: f2d16b47c093
Revises: c4a7e91b53d8
Create Date: 2026-08-19 06:30:00.000000

ADR 122. `run_events` skipped any bar where the ticker was outside the
trade universe that day (DESIGN §4.7 step 3), so a train-universe name has
bars, indicators, real band touches, and **no events at all**. Measured:
SMCI has 192 band touches since 2024, zero events, and has never once been
in the trade universe across 66 quarterly snapshots.

The membership stops being a filter on *detection* and becomes a column on
the *detection*. A signal that fired is a fact about the ticker; whether you
would have traded it is a separate fact, and storing the second lets every
consumer decide for itself.

**`NOT NULL DEFAULT true`, which is the whole reason this migration is
fast.** Postgres 11+ stores a non-volatile column default in the catalog
rather than rewriting the table, so this is a metadata change against
13,479,819 rows instead of a full rewrite plus an `UPDATE`. `true` is not a
guess: every row already in the table was written by the gated code path,
so every one of them cleared the trade universe. That is exactly what the
default asserts.

The risk the default carries, stated because it is real: an `INSERT` that
forgets the column silently claims trade membership. `run_events` sets it
explicitly on every row after this, and `test_events_in_trade_filter.py`
fails if any production read of `events` stops filtering on it.

**The index is partial.** Every consumer that filters wants
`in_trade = true`; nothing scans for the false rows except the ticker page,
which always has a ticker predicate and uses `events_ticker_date`. A partial
index is a fraction of the size of a full one and the planner uses it for
exactly the queries that exist.

**Verify:**

    cscan db status
    psql -c "\d events"
    psql -c "SELECT in_trade, count(*) FROM events GROUP BY 1"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2d16b47c093"
down_revision: Union[str, Sequence[str], None] = "c4a7e91b53d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Frozen DDL. **Do not import these from `jobs/views.py`.**
# ---------------------------------------------------------------------------
#
# A migration is a statement about one point in history; `jobs/views.py`
# holds the *current* definition. Importing the live constant makes an old
# migration emit tomorrow's SQL, and it breaks the moment a later migration
# changes the view.
#
# It did, on 2026-08-19: ADR 122 added `events.in_trade`, and every earlier
# migration that had imported `V_SCREEN_LIVE_DDL` started emitting
# `AND e.in_trade` against a table without the column. Every fresh database
# failed with `UndefinedColumn`. **Invisible locally** -- a developer
# applies only the new migrations, and only a from-scratch replay hits it,
# which is what CI and any new deployment do.
#
# These are literals, captured as the view stood at this revision.

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
     AND e.in_trade
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
     AND e.in_trade
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""

# The exact line `upgrade()` adds to both screener views, so `downgrade()`
# removes precisely it. Spelled once rather than inline at both call sites:
# a downgrade that removed *almost* the right text would leave a view that
# still parses and no longer filters, which is the failure mode this whole
# migration exists to prevent.
_PREDICATE = "     AND e.in_trade\n"


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "in_trade",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "events_in_trade",
        "events",
        ["config_hash", "split_key", "signal_type"],
        postgresql_where=sa.text("in_trade"),
    )

    # Both screener views take the predicate. Their two consumers already
    # join `v_universe` and filter there, so this is defence in depth --
    # but `v_universe` answers "is it in the universe *now*" and the column
    # answers "was it in the universe *on that bar*", which is the right
    # question for a historical row.
    #
    # `v_chart` and `v_events` deliberately do not change: they are what
    # the ticker page reads, and showing every firing on a name you do not
    # trade is what ADR 122 is for.
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(_V_SCREEN_AT_THIS_REVISION)
    op.execute(_V_SCREEN_LIVE_AT_THIS_REVISION)


def downgrade() -> None:
    # The views first: they reference the column, so dropping it under them
    # would fail rather than cascade -- which is the safe default and the
    # reason the order matters. `V_SCREEN_DDL_PRE_122` is not needed: the
    # constants below are regenerated from this module, so re-running the
    # ADR 120 forms means re-running them without the predicate.
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(_V_SCREEN_AT_THIS_REVISION.replace(_PREDICATE, ""))
    op.execute(_V_SCREEN_LIVE_AT_THIS_REVISION.replace(_PREDICATE, ""))

    # Then the index: dropping a column would cascade to it, and a cascade
    # that happens to do the right thing is not the same as saying so.
    op.drop_index("events_in_trade", table_name="events")
    op.drop_column("events", "in_trade")
