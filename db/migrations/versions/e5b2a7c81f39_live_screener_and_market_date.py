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
    MARKET_DATE_DDL,
    V_POSITIONS_DDL,
    V_POSITIONS_DDL_MARKET_DATE,
    V_SCREEN_DDL,
    V_SCREEN_DDL_PRE_119,
    V_SCREEN_LIVE_DDL,
)

revision: str = "e5b2a7c81f39"
down_revision: Union[str, Sequence[str], None] = "a3c8e15d40b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(MARKET_DATE_DDL)

    # `v_positions` depends on the function, so the function goes first.
    # Dropped and recreated rather than replaced: `CREATE OR REPLACE VIEW`
    # cannot change a column's type, and although these two columns keep
    # theirs, a drop is unambiguous and this view is rebuilt often enough
    # that the pattern is worth keeping consistent.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(V_POSITIONS_DDL_MARKET_DATE)

    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(V_SCREEN_DDL)
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute(V_SCREEN_LIVE_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_screen_live")
    op.execute("DROP VIEW IF EXISTS public.v_screen")
    op.execute(V_SCREEN_DDL_PRE_119)

    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(V_POSITIONS_DDL)

    # After the view stops referencing it. A `DROP FUNCTION` with a
    # dependent view fails rather than cascading, which is the safe default
    # and the reason the order matters here.
    op.execute("DROP FUNCTION IF EXISTS public.market_date()")
