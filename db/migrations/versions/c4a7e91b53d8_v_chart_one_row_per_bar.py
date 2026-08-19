r"""v_chart returns one row per bar, which is what it was documented to do

Revision ID: c4a7e91b53d8
Revises: b8f31c204e7a
Create Date: 2026-08-19 05:20:00.000000

ADR 120. DESIGN §8.2 introduces this view as "bars + indicators + event
markers, **one row per bar**". Measured 2026-08-19 on TSM's last 400 days:
**963 rows for 275 trading days.**

**1. No `config_hash` predicate.** The events join matched every sweep
config, and there are 22 of them on `next_open`. A bar that produced an
event joined once per config, so a chart would stack 22 candles and 22
markers on one date. `v_events` has always filtered config and ADR 119
fixed `v_screen`; this was the third and last serving view missing it.

**2. `entry_kind = 'next_open'`.** Only `cscan backtest` writes that kind,
so markers stopped at 2026-08-13 while events had fired through 2026-08-18.
A chart whose newest marker is five sessions old is not showing what fired.
`touch` is the detection-time row, written by the poller and by
`cscan events`.

**3. `AND e.is_cluster_head`.** The poller cannot cluster -- ADR 054's gap
window needs the whole session, which does not exist at 09:35 -- so its
rows carry NULL and a bare truth test drops all of them. `IS NOT FALSE`
keeps the not-yet-clustered rows and hides only the known non-heads.

**4. A bar can carry two events, and no predicate fixes that.** Measured:
**116 dates** hold both a long and a short head under the live config.
ADBE 2016-06-22 is `bb_lower_touch` (long) and `stoch_overbought` (short)
on the same bar; both fired. So the marker columns become arrays and the
bar stays one row. A series library indexes by time and silently keeps the
last of a duplicate key, which is the failure where the chart looks right
and shows the wrong marker.

`exit_date`, `exit_reason` and `net_ret` are dropped. They are per-event
outcomes, and carrying per-event columns on a bar grain is the shape
mismatch that produced the duplication in the first place. `v_events` has
them keyed by event, and the ticker page reads it for the history table.

**Nothing consumed the view yet.** Grep across `capitalscan/`, `web/` and
`db/` finds only this session's ticker page, so the column change breaks no
caller.

**Verify:**

    cscan db status
    psql -c "SELECT count(*), count(DISTINCT ts) FROM v_chart
             WHERE ticker = 'TSM' AND ts >= now() - interval '400 days'"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

from capitalscan.jobs.views import V_CHART_DDL_PRE_120

revision: str = "c4a7e91b53d8"
down_revision: Union[str, Sequence[str], None] = "b8f31c204e7a"
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

_V_CHART_DDL_AT_THIS_REVISION = """CREATE VIEW public.v_chart AS
 SELECT b.ticker,
    b.ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.sma_200,
    i.bb_width_pct,
    i.dd_52w,
    ev.event_ids,
    ev.signal_types,
    ev.sides,
    ev.signal_strength
   FROM public.bars b
     LEFT JOIN public.indicators i ON ((i.ticker = b.ticker) AND (i.ts = b.ts)
         AND (i."interval" = b."interval"))
     LEFT JOIN LATERAL ( SELECT array_agg(e.id ORDER BY e.id) AS event_ids,
            array_agg(e.signal_type ORDER BY e.id) AS signal_types,
            array_agg(e.side ORDER BY e.id) AS sides,
            max(e.signal_strength) AS signal_strength
           FROM public.events e
          WHERE ((e.ticker = b.ticker) AND (e.signal_date = (b.ts)::date)
              AND (e.entry_kind = 'touch'::text)
              AND (e.is_cluster_head IS NOT FALSE)
              AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
         ) ev ON true
  WHERE (b."interval" = '1d'::text)
"""


def upgrade() -> None:
    # Dropped and recreated rather than replaced: `CREATE OR REPLACE VIEW`
    # cannot drop a column or change one's type, and this does both.
    op.execute("DROP VIEW IF EXISTS public.v_chart")
    op.execute(_V_CHART_DDL_AT_THIS_REVISION)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_chart")
    op.execute(V_CHART_DDL_PRE_120)
