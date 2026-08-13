"""v_screen selects the cell pooled over signal_strength

Revision ID: f1a8d3b62c07
Revises: e3c7f5a91d24
Create Date: 2026-08-13 00:00:00.000000

ADR 107. One line changes:

    -  AND c.signal_strength = e.signal_strength
    +  AND c.signal_strength IS NULL

**The defect.** `v_screen` was built under ADR 011's grid, where
`signal_strength` was a real dimension (the 1-versus-2 cut inside
`CONFLUENCE_LOW`). ADR 102 established that cut cannot exist:
`core/signals.py:242` sets `signal_strength = len(signal_types_all)`, and
`confluence_*` fires exactly when both primitives fire, so the value jumps
from 1 to 3 with no 2 reachable. Measured across 626,791 events, strength
is a pure function of `signal_type` with six combinations and no
exceptions.

Session 12 therefore writes `signal_strength = NULL` on every `cell_stats`
row, meaning "pooled over strength". `NULL = 1` is never true, so **no
Session 12 statistic could reach the screener**: `v_screen` returned
683,653 rows with zero non-null `cell_id`. The view worked, returned rows,
raised nothing, and showed no numbers.

**Why `IS NULL` rather than dropping the condition.** `cell_id` embeds the
strength slot, so a strength-split row and a pooled row are two distinct
rows describing one cell. A view with no strength condition would match
both and duplicate every screener row — the same fan-out ADR 100 exists to
prevent, arriving through a different column. `IS NULL` keeps the guard and
selects the pooled row, which is exactly what the line two rows below it
already does for `era`.

**Why not populate the column instead.** Writing `3 if confluence else 1`
would satisfy the old equality join with no migration, but it bakes today's
arithmetic into the writer. The user plans to add trigger families beyond
Bollinger and stochastic; a fourth primitive outside the confluence
definition makes strength 2 reachable, at which point a cell holds mixed
strengths and the derived value stamps it with one of them. Nothing would
raise. `IS NULL` stays true regardless of how many triggers exist, because
it describes the cell's construction rather than its contents.

**ADR 102 is not amended and does not need to be.** It removed strength as
a *cut* — a dimension that splits one cell into several — and that claim
holds: bringing strength back would not produce a single additional cell,
since every cell's events share one strength value. This revision fixes the
view that was never updated to match, which is a different thing.

Rollback restores the equality join verbatim, which reinstates the defect.
That is intentional: a `downgrade()` that "improved" on the previous state
would make the migration irreversible in practice.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a8d3b62c07"
down_revision: Union[str, Sequence[str], None] = "e3c7f5a91d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Parameterized on the strength predicate alone, so `upgrade` and
# `downgrade` cannot drift. Everything else is the body `e3c7f5a91d24`
# left in place, including both of its predicates.
_V_SCREEN = """
CREATE OR REPLACE VIEW v_screen AS
SELECT e.ticker, e.signal_date, e.signal_type, e.signal_types_all,
       e.signal_strength, e.touch_level, e.bb_pctb, e.k_full, e.k_fast,
       e.k_cross_up, e.dd_52w, e.dd_bucket, e.above_sma200,
       e.seq_in_cluster, e.cofire_count, e.sector,
       c.cell_id,
       CASE WHEN c.suppressed THEN NULL ELSE c.p_hit END              AS p_hit,
       CASE WHEN c.suppressed THEN NULL ELSE c.baseline_empirical END AS baseline,
       CASE WHEN c.suppressed THEN NULL ELSE c.edge END               AS edge,
       CASE WHEN c.suppressed THEN NULL ELSE c.ci_low END             AS ci_low,
       CASE WHEN c.suppressed THEN NULL ELSE c.ci_high END            AS ci_high,
       c.n_events, c.n_eff, c.q_value, c.suppressed, c.suppress_reason,
       p.q50, p.p_touch_3, p.p_touch_5, p.p_adverse_3, p.model_version
FROM events e
LEFT JOIN cell_stats c
       ON c.signal_type     = e.signal_type
      AND c.side            = e.side
      AND c.dd_bucket       = e.dd_bucket
      AND {strength_predicate}
      AND c.entry_kind      = e.entry_kind
      AND c.split_key       = 'validate'      -- NEVER e.split_key
      AND c.era             IS NULL           -- pooled row
      AND c.horizon_days    = 5
      AND c.target_pct      = 0.03
      AND c.config_hash     = current_setting('capitalscan.default_config_hash', true)
      AND c.arm             = 'signal'
LEFT JOIN predictions p
       ON p.ticker = e.ticker AND p.as_of = e.signal_date
WHERE e.is_cluster_head
  AND e.entry_kind = 'next_open'
"""

# "This cell is pooled over strength", the exact parallel of `c.era IS NULL`.
_POOLED = "c.signal_strength IS NULL   -- ADR 102: not a dimension"

# ADR 011's grid, where strength was a real cut. Kept only for rollback.
_CONDITIONED = "c.signal_strength = e.signal_strength"


def upgrade() -> None:
    """Select the strength-pooled cell."""
    op.execute(_V_SCREEN.format(strength_predicate=_POOLED))


def downgrade() -> None:
    """Restore the ADR 011-era equality join, reinstating the defect."""
    op.execute(_V_SCREEN.format(strength_predicate=_CONDITIONED))
