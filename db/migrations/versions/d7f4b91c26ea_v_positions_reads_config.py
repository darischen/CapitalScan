"""v_positions reads its exit thresholds from config, not from literals

Revision ID: d7f4b91c26ea
Revises: b2e57f3a91c4
Create Date: 2026-08-18 01:20:00.000000

ADR 095's deferred Phase 5 rebuild, resolved by ADR 115. Session 15 task
15.4.

**What was wrong.** `v_positions` compared `k_full` against a bare `80`
and `CURRENT_DATE - entry_date` against a bare `5`. The exact text is in
`jobs/views.py::V_POSITIONS_DDL_PRE_115`, quoted there once and not here -
`jobs/threshold_lint.py` matches on the *pattern* rather than the spelling,
so a second verbatim copy in this docstring would be a second finding to
allowlist for no gain.

`80` is `ExitParams.exit_stoch_threshold` and `5` is
`ExitParams.max_hold_days`. Sweep either and the backtest moves while the
position page does not, with every number on both sides looking reasonable.
That is the silent-bias class ADR 092 and invariant 9 exist to prevent.

Three more defects in the same expression, all named by ADR 095 and one
found while fixing them:

- `exit_signal_stoch` ignored `p.side`, so an open short read its exit off
  the long threshold - wrong in the direction that *suppresses* the exit.
- `exit_signal_mid_band` was published unconditionally though
  `ExitParams.exit_on_mid_band` defaults False (ADR 046).
- `days_held` counted **calendar** days while `max_hold_days` counts bars.
  A Thursday entry reads 4 calendar days and 2 sessions on the following
  Monday, so the timeout flag fired early over every weekend. Not in ADR
  095; found by writing the parity fixture.

**Why a settings row rather than a generated DDL.** ADR 095 preferred
generating the view's DDL from `ExitParams` here in the migration. ADR 115
reverses that, and the reason is `jobs/threshold_lint.py`, which did not
exist when ADR 095 was written: a generated DDL still bakes `80` into the
database, `pg_dump` still writes `(80)::numeric` into `db/schema.sql`, and
the `KNOWN_EXCEPTIONS` entry exempting it would have to stay forever. A
settings row leaves no literal in any checked-in SQL, so the exemption is
deleted instead of renewed - which is what ADR 095's own note asked for
("its continued presence past that point is itself a signal the rebuild
missed a spot").

**Why this file imports application code.** `capitalscan.jobs.views` holds
the DDL and the row payload so that a *test* can compare the deployed view
against the live `ExitParams` and fail when they drift. A migration is
applied once; without that test the row would go stale silently the first
time a threshold moved. The import is narrow - `views.py` imports only
`capitalscan.core.config`, which imports only `dataclasses`.

**Drop and create, not replace.** `CREATE OR REPLACE VIEW` can append
columns but cannot change a column's type, and `days_held` moves from
`integer` (a date subtraction) to `bigint` (a count).

**Verify:**

    cscan db status
    psql -c '\\d v_positions'
    psql -c 'SELECT * FROM serving_config'
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from capitalscan.jobs.views import (
    SERVING_CONFIG_DDL,
    SERVING_CONFIG_UPSERT,
    V_POSITIONS_DDL,
    V_POSITIONS_DDL_PRE_115,
    serving_config_values,
)

revision: str = "d7f4b91c26ea"
down_revision: Union[str, Sequence[str], None] = "b2e57f3a91c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. The settings table, seeded from `ExitParams()` defaults. Seeded
    #    rather than left empty: the view LEFT JOINs it, so an empty table
    #    would render every exit flag NULL until someone ran a CLI command
    #    nobody had been told about yet.
    op.execute(SERVING_CONFIG_DDL)

    # `config_hash` is left empty here on purpose. Computing it needs
    # `jobs.config.config_hash`, and a migration that pins a hash pins the
    # config as of the day it was written - which is exactly the staleness
    # this whole change exists to remove. `cscan db sync-config` fills it.
    values = serving_config_values()
    op.get_bind().execute(sa.text(SERVING_CONFIG_UPSERT), values)

    # 2. The view.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(V_POSITIONS_DDL)


def downgrade() -> None:
    # Restores the literals. A downgrade that quietly kept the fix would
    # not be a downgrade, and the pre-115 DDL is checked in verbatim for
    # exactly this reason.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(V_POSITIONS_DDL_PRE_115)
    op.execute("DROP TABLE IF EXISTS public.serving_config")
