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
    INDICATORS_DAILY_LATEST_INDEX,
    V_POSITIONS_DDL,
    V_TICKER_STATE_DDL,
    V_TICKER_STATE_DDL_PRE_116,
)

revision: str = "a3c8e15d40b7"
down_revision: Union[str, Sequence[str], None] = "d7f4b91c26ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(INDICATORS_DAILY_LATEST_INDEX)
    # `v_positions` selects from `v_ticker_state`, so the dependent view has
    # to go first. Dropped and recreated rather than `CASCADE`d: a CASCADE
    # would also take anything else that came to depend on it since, and
    # would do so silently.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute("DROP VIEW IF EXISTS public.v_ticker_state")
    op.execute(V_TICKER_STATE_DDL)
    op.execute(V_POSITIONS_DDL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute("DROP VIEW IF EXISTS public.v_ticker_state")
    op.execute(V_TICKER_STATE_DDL_PRE_116)
    op.execute(V_POSITIONS_DDL)
    op.execute("DROP INDEX IF EXISTS public.indicators_daily_latest")
