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

from capitalscan.jobs.views import V_SCREEN_LIVE_DDL

revision: str = "a7c519d3e8b4"
down_revision: Union[str, Sequence[str], None] = "f2d16b47c093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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
    op.execute(V_SCREEN_LIVE_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(V_SCREEN_LIVE_DDL.replace(_REVERSAL_LATERAL, "").replace(_REVERSAL_COLUMNS, ""))
