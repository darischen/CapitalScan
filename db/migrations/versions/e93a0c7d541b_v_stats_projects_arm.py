r"""v_stats projects `arm`, which it has needed since ADR 105

Revision ID: e93a0c7d541b
Revises: c8e2f60a4b17
Create Date: 2026-08-19 03:20:00.000000

ADR 126. `v_stats` predates `cell_stats.arm` and never gained it. ADR 105
added the column to separate a *measured* arm from a *recommended* one;
this view was written before that and DESIGN 8.2's DDL still omits it.

So the serving surface for statistics could not express the one predicate
that keeps the control and benchmark arms out of a rate. Nothing caught it
because every path that mattered carried its own copy of the filter:
`v_screen` and `v_screen_live` join `cell_stats` directly and pin
`arm = 'signal'` themselves.

**Found by running the page.** `web/lib/screen.ts` filters `arm = 'signal'`
on `v_stats`, so every `?stats=1` request returned
`column "arm" does not exist` and rendered the error state. The toggle had
been broken since Session 17 shipped, because the statistics panel was only
ever exercised against a fixture -- `states.test.tsx` renders the component
with a `CellStats` object and never runs the query behind it.

`/research` needs the same predicate for its cell grid, which is how this
surfaced now rather than later.

**Verify:**

    cscan db status
    psql -c "SELECT arm, count(*) FROM v_stats GROUP BY 1"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "e93a0c7d541b"
down_revision: Union[str, Sequence[str], None] = "c8e2f60a4b17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen per ADR 125: a migration carries its SQL, never imports the live
# constant.
_V_STATS_AT_THIS_REVISION = """CREATE VIEW public.v_stats AS
SELECT cell_id,
    run_id,
    config_hash,
    signal_type,
    dd_bucket,
    signal_strength,
    side,
    entry_kind,
    arm,
    split_key,
    era,
    horizon_days,
    target_pct,
    n_events,
    n_eff,
    n_tickers,
    mean_cofire,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE p_hit
        END AS p_hit,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE baseline_empirical
        END AS baseline,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE edge
        END AS edge,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_low
        END AS ci_low,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_high
        END AS ci_high,
    q_value,
    p_value_randomization,
    mean_ret,
    median_ret,
    ret_p25,
    ret_p75,
    mean_mfe,
    mean_mae,
    median_time_to_mfe,
    capture_ratio,
    p_touch_2pct,
    p_touch_3pct,
    p_touch_5pct,
    p_touch_10pct,
    median_day_touch_5pct,
    exit_mix,
    earnings_frac,
    suppressed,
    suppress_reason
   FROM cell_stats
"""

_V_STATS_BEFORE_THIS_REVISION = """CREATE VIEW public.v_stats AS
SELECT cell_id,
    run_id,
    config_hash,
    signal_type,
    dd_bucket,
    signal_strength,
    side,
    entry_kind,
    split_key,
    era,
    horizon_days,
    target_pct,
    n_events,
    n_eff,
    n_tickers,
    mean_cofire,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE p_hit
        END AS p_hit,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE baseline_empirical
        END AS baseline,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE edge
        END AS edge,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_low
        END AS ci_low,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_high
        END AS ci_high,
    q_value,
    p_value_randomization,
    mean_ret,
    median_ret,
    ret_p25,
    ret_p75,
    mean_mfe,
    mean_mae,
    median_time_to_mfe,
    capture_ratio,
    p_touch_2pct,
    p_touch_3pct,
    p_touch_5pct,
    p_touch_10pct,
    median_day_touch_5pct,
    exit_mix,
    earnings_frac,
    suppressed,
    suppress_reason
   FROM cell_stats
"""


def upgrade() -> None:
    # Dropped and recreated rather than replaced: `CREATE OR REPLACE VIEW`
    # cannot insert a column in the middle of the list, and `arm` belongs
    # with the other grain columns rather than appended at the end.
    op.execute("DROP VIEW IF EXISTS public.v_stats")
    op.execute(_V_STATS_AT_THIS_REVISION)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.v_stats")
    op.execute(_V_STATS_BEFORE_THIS_REVISION)
