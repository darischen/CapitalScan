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
    V_POSITIONS_DDL_PRE_115,
)

revision: str = "d7f4b91c26ea"
down_revision: Union[str, Sequence[str], None] = "b2e57f3a91c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The row this migration seeds, as `ExitParams` stood at this revision.
#
# It called `serving_config_values()` — the live builder — so a field added
# to `ExitParams` later would change what this migration inserts, and a
# field *removed* would make it insert a key the frozen upsert above does
# not name. `cscan db sync-config` is what keeps the row current; this is
# only the initial value, and it should be the initial value it always was.
_SEED_AT_THIS_REVISION = {
    "config_hash": "",
    "exit_on_mid_band": False,
    "exit_on_stoch_80": True,
    "exit_on_upper_band": True,
    "exit_stoch_source": "k_full",
    "exit_stoch_threshold": 80.0,
    "exit_stoch_threshold_short": 20.0,
    "max_hold_days": 5,
}

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

_SERVING_CONFIG_DDL_AT_THIS_REVISION = """CREATE TABLE IF NOT EXISTS public.serving_config (
    only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row),
    config_hash text NOT NULL,
    exit_stoch_source text NOT NULL,
    exit_stoch_threshold numeric(12,4) NOT NULL,
    exit_stoch_threshold_short numeric(12,4) NOT NULL,
    exit_on_stoch_80 boolean NOT NULL,
    exit_on_upper_band boolean NOT NULL,
    exit_on_mid_band boolean NOT NULL,
    max_hold_days integer NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT serving_config_stoch_source_check
        CHECK (exit_stoch_source = ANY (ARRAY['k_full'::text, 'k_fast'::text]))
)
"""

_SERVING_CONFIG_UPSERT_AT_THIS_REVISION = """INSERT INTO public.serving_config (
    only_row, config_hash, exit_stoch_source,
    exit_stoch_threshold, exit_stoch_threshold_short,
    exit_on_stoch_80, exit_on_upper_band, exit_on_mid_band,
    max_hold_days, updated_at
) VALUES (
    true, :config_hash, :exit_stoch_source,
    :exit_stoch_threshold, :exit_stoch_threshold_short,
    :exit_on_stoch_80, :exit_on_upper_band, :exit_on_mid_band,
    :max_hold_days, now()
)
ON CONFLICT (only_row) DO UPDATE SET
    config_hash = EXCLUDED.config_hash,
    exit_stoch_source = EXCLUDED.exit_stoch_source,
    exit_stoch_threshold = EXCLUDED.exit_stoch_threshold,
    exit_stoch_threshold_short = EXCLUDED.exit_stoch_threshold_short,
    exit_on_stoch_80 = EXCLUDED.exit_on_stoch_80,
    exit_on_upper_band = EXCLUDED.exit_on_upper_band,
    exit_on_mid_band = EXCLUDED.exit_on_mid_band,
    max_hold_days = EXCLUDED.max_hold_days,
    updated_at = now()
"""

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


def upgrade() -> None:
    # 1. The settings table, seeded from `ExitParams()` defaults. Seeded
    #    rather than left empty: the view LEFT JOINs it, so an empty table
    #    would render every exit flag NULL until someone ran a CLI command
    #    nobody had been told about yet.
    op.execute(_SERVING_CONFIG_DDL_AT_THIS_REVISION)

    # `config_hash` is left empty here on purpose. Computing it needs
    # `jobs.config.config_hash`, and a migration that pins a hash pins the
    # config as of the day it was written - which is exactly the staleness
    # this whole change exists to remove. `cscan db sync-config` fills it.
    values = dict(_SEED_AT_THIS_REVISION)
    op.get_bind().execute(sa.text(_SERVING_CONFIG_UPSERT_AT_THIS_REVISION), values)

    # 2. The view.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(_V_POSITIONS_DDL_AT_THIS_REVISION)


def downgrade() -> None:
    # Restores the literals. A downgrade that quietly kept the fix would
    # not be a downgrade, and the pre-115 DDL is checked in verbatim for
    # exactly this reason.
    op.execute("DROP VIEW IF EXISTS public.v_positions")
    op.execute(V_POSITIONS_DDL_PRE_115)
    op.execute("DROP TABLE IF EXISTS public.serving_config")
