r"""the screener can see the poller's intraday reversal judgement

Revision ID: a7c519d3e8b4
Revises: f2d16b47c093
Create Date: 2026-08-19 02:20:00.000000

ADR 123. Two different things carry the word "reversal", and until now the
screener could only see one of them.

`bear_close_above_upper` is **close-confirmed** (ADR 108/109): `open >
close AND close >= bb_upper[t-1]`, resolvable only after the session ends.
It lands in `events.signal_types_all` and reaches the screener the next
morning, once `cscan events` has run.

`poll.py::reversal_state` is the **live analogue** (ADR 117): price above
the band but below today's open. It is written to
`signal_reports.state_json->'bear_reversal'` on every quote, and it is
deliberately *not* the same predicate -- one is a statement about a
completed session, the other about where price is right now.

`v_screen_live` joined `signal_reports` for `min(fired_at)` and never
touched `state_json`. So during a session the poller's terminal printed
`no reversal (+0.42 ATR vs open)` while the home page had no way to say
anything at all, and the badge appeared only the following day.

**The newest report, not the first.** `fired_at` keeps `min()` -- when the
signal was first detected -- while the reversal takes the latest row,
because it describes where price is now and the session's earliest quote is
the least useful one. Two laterals, for that reason.

**`state_json ? 'bear_reversal'` is load-bearing.** ADR 117 merged
2026-08-18 11:18 PT and the last poller run wrote at 08:34 PT, so every
existing report lacks the key. Without the guard a missing key casts to
NULL and renders as "not a reversal" rather than as "not recorded", which
is a claim the data does not support.

**Safe to run beside a job.** `DROP VIEW`/`CREATE VIEW` needs
ACCESS EXCLUSIVE on the *view* and only ACCESS SHARE on the tables beneath
it, which does not conflict with the ROW EXCLUSIVE an ingest holds. That is
different from the `ALTER TABLE` case CLAUDE.md warns about.

**Verify:**

    cscan db status
    psql -c "SELECT ticker, rev_confirmed, rev_open_gap_atr
             FROM v_screen_live WHERE rev_ts IS NOT NULL LIMIT 5"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "a7c519d3e8b4"
down_revision: Union[str, Sequence[str], None] = "f2d16b47c093"
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

  WHERE ((e.entry_kind = 'touch'::text) AND (e.is_cluster_head IS NOT FALSE)
     AND e.in_trade
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""

# The lateral this migration adds, spelled once so `downgrade()` removes
# precisely it. A downgrade that removed *almost* the right text would leave
# a view that still parses and silently references a dropped alias.
_REVERSAL_LATERAL = """     LEFT JOIN LATERAL ( SELECT
              (r2.state_json -> 'bear_reversal' ->> 'confirmed')::boolean AS confirmed,
              (r2.state_json -> 'bear_reversal' ->> 'above_band')::boolean AS above_band,
              (r2.state_json -> 'bear_reversal' ->> 'open_gap_atr')::numeric AS open_gap_atr,
              r2.fired_at AS rev_ts
           FROM public.signal_reports r2
          WHERE ((r2.event_id = e.id)
              AND (r2.state_json ? 'bear_reversal'))
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON (true)
"""

_REVERSAL_COLUMNS = """    rev.confirmed AS rev_confirmed,
    rev.above_band AS rev_above_band,
    rev.open_gap_atr AS rev_open_gap_atr,
    rev.rev_ts,
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(_V_SCREEN_LIVE_AT_THIS_REVISION)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(
        _V_SCREEN_LIVE_AT_THIS_REVISION.replace(_REVERSAL_LATERAL, "").replace(
            _REVERSAL_COLUMNS, ""
        )
    )
