"""cell_stats.arm, and v_screen's config_hash and arm predicates

Revision ID: e3c7f5a91d24
Revises: d4a91c7b6e08
Create Date: 2026-08-11 00:00:00.000000

Two changes, both required in the same revision (ADR 100, ADR 105).

**1. `cell_stats.arm`.** Phase 4 measures populations it will never
recommend: a random-entry null across 200 replications, DCA variants, and
ADR 017's naive-short control. A row holding `p_hit = 0.61` looks identical
whether it describes a signal or a control, and the difference is the whole
point. `arm` is the discriminator, defaulting to `'signal'` so every row
Session 12 already wrote takes the correct value with no backfill.

Not derived from `cell_id`'s string, not inferred from `split_key`, and not
maintained in a lookup table: a measured population needs to say what it is
on its own row, or a consumer filtering on `arm = 'signal'` silently drops
it, or worse, silently keeps it.

**2. `v_screen` gains two predicates.**

`c.config_hash = current_setting('capitalscan.default_config_hash', true)`
is ADR 100's correction. `cell_stats`' primary key became
`(cell_id, config_hash)` under ADR 096, so the table holds one row per cell
*per config*. The first Phase 4 run writing two configs therefore
duplicates every screener row through the join. Session 9's sweep already
wrote 18 distinct `config_hash` values into `events`, so this is a live
hazard rather than a hypothetical one.

`c.arm = 'signal'` is ADR 105's, and it is what keeps a control or
benchmark row off a surface a person reads as advice.

**Both predicates go in the `ON` clause, never the `WHERE` clause.** This
is the detail that decides whether the change is correct or catastrophic.
`cell_stats` is LEFT JOINed, so an event with no matching statistics row
still appears with null statistics — which is the current state of every
one of the 683,653 rows `v_screen` returns. Moving either predicate to
`WHERE` converts the LEFT JOIN into an inner join and empties the screener
entirely.

`current_setting(..., true)` takes the `missing_ok` argument, so an unset
GUC yields NULL rather than raising. `c.config_hash = NULL` is never true,
so an unconfigured database shows events with null statistics — the same
shape as today. The failure mode is "no numbers", not "no rows" and not an
error, which is the right direction for a serving view.

**`CREATE OR REPLACE VIEW`, not `DROP` then `CREATE`.** The output column
list is unchanged — only join predicates move — so `REPLACE` is legal here,
and it avoids a window in which `v_screen` does not exist and avoids
cascading to anything that depends on it. `6d86bf1f668e` used DROP in its
`downgrade()` because it was removing the view outright, which is a
different operation.

Rollback restores the exact pre-existing view body and drops the column and
its constraint. No data is lost on the way down beyond `arm` itself, which
is reconstructible: every row written before Session 13 is `'signal'`.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3c7f5a91d24"
down_revision: Union[str, Sequence[str], None] = "d4a91c7b6e08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The view body, parameterized on the two predicates so `upgrade` and
# `downgrade` cannot drift from each other. Everything else is a verbatim
# copy of the definition `6d86bf1f668e` created.
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
      AND c.signal_strength = e.signal_strength
      AND c.entry_kind      = e.entry_kind
      AND c.split_key       = 'validate'      -- NEVER e.split_key
      AND c.era             IS NULL           -- pooled row
      AND c.horizon_days    = 5
      AND c.target_pct      = 0.03
{extra_predicates}
LEFT JOIN predictions p
       ON p.ticker = e.ticker AND p.as_of = e.signal_date
WHERE e.is_cluster_head
  AND e.entry_kind = 'next_open'
"""

_NEW_PREDICATES = (
    "      AND c.config_hash     = "
    "current_setting('capitalscan.default_config_hash', true)\n"
    "      AND c.arm             = 'signal'"
)


def upgrade() -> None:
    """Add `cell_stats.arm`, then rebuild `v_screen` with both predicates."""
    # `NOT NULL DEFAULT 'signal'` in one statement. Postgres 11+ stores the
    # default in the catalogue rather than rewriting the table, so this is
    # fast and takes its ACCESS EXCLUSIVE lock only briefly. `cell_stats`
    # holds 192 rows, all of which are signal rows, so the default is also
    # the correct historical value.
    op.execute("ALTER TABLE cell_stats ADD COLUMN arm text NOT NULL DEFAULT 'signal'")

    # A separate statement rather than an inline column constraint, so the
    # constraint carries a name we choose. An unnamed CHECK gets a
    # system-generated name that `downgrade()` would have to guess.
    op.execute(
        "ALTER TABLE cell_stats ADD CONSTRAINT cell_stats_arm_check "
        "CHECK (arm IN ('signal', 'control', 'benchmark'))"
    )

    op.execute(_V_SCREEN.format(extra_predicates=_NEW_PREDICATES))


def downgrade() -> None:
    """Restore the pre-ADR-100 view, then drop the constraint and column.

    Order matters in both directions. The view references `c.arm`, so the
    column cannot be dropped while the view still selects on it — Postgres
    would refuse. Rebuilding the view first removes that dependency.
    """
    op.execute(_V_SCREEN.format(extra_predicates=""))
    op.execute("ALTER TABLE cell_stats DROP CONSTRAINT cell_stats_arm_check")
    op.execute("ALTER TABLE cell_stats DROP COLUMN arm")
