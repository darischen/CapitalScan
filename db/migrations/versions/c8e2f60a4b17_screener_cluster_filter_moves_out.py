r"""the screener decides what to do with cluster repeats; the view stops deciding

Revision ID: c8e2f60a4b17
Revises: a7c519d3e8b4
Create Date: 2026-08-19 02:50:00.000000

ADR 124. `v_screen_live` filtered `is_cluster_head IS NOT FALSE`, which was
right intraday and wrong the next morning.

The poller **cannot** cluster -- ADR 054's gap window needs the whole
session, which does not exist at 09:35 -- so its rows carry NULL and every
one of them passed the predicate. Overnight `cscan events` clusters, the
repeats become `false`, and they vanish.

**Rows disappeared from a date between the session and the next morning.**
Measured on Thursday 2026-08-06: 19 confluence fires, of which 4 are heads
and 15 are repeats. A reader watching live saw 19 and came back to 4, with
nothing on the page accounting for the other 15. That is the report that
started this: "i remember having way more".

The predicate moves to the caller. `is_cluster_head` is still projected, so
nothing loses information; the screener defaults to heads and offers every
fire behind a toggle, the same shape the ticker page's event history
already uses.

**`v_screen` is untouched.** It keeps `is_cluster_head` in its WHERE clause
because it is the statistics grain -- ADR 054 exists so a name hugging a
band for three weeks is not counted as fifteen independent observations,
and that reasoning is about measurement, not about display.

**Safe beside a running job.** `DROP`/`CREATE VIEW` needs ACCESS EXCLUSIVE
on the view and ACCESS SHARE on the tables beneath it, which does not
conflict with an ingest's ROW EXCLUSIVE.

**Verify:**

    cscan db status
    psql -c "SELECT is_cluster_head, count(*) FROM v_screen_live
             WHERE signal_date = '2026-08-06' GROUP BY 1"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

from capitalscan.jobs.views import V_SCREEN_LIVE_DDL

revision: str = "c8e2f60a4b17"
down_revision: Union[str, Sequence[str], None] = "a7c519d3e8b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The exact WHERE clause each way, so `downgrade()` restores precisely what
# was there. Spelled out rather than patched with a substring replace: this
# predicate is the whole subject of the migration.
_WITHOUT = """  WHERE ((e.entry_kind = 'touch'::text)
     AND e.in_trade"""

_WITH = """  WHERE ((e.entry_kind = 'touch'::text) AND (e.is_cluster_head IS NOT FALSE)
     AND e.in_trade"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(V_SCREEN_LIVE_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(V_SCREEN_LIVE_DDL.replace(_WITHOUT, _WITH))
