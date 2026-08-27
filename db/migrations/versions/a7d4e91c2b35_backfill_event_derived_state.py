"""Backfill events.bb_mid, close and vix_pct_252d, batched, without blocking readers

Revision ID: a7d4e91c2b35
Revises: f1c8a260d94e
Create Date: 2026-08-27 10:30:00.000000

The DML half of `f1c8a260d94e`, split out after that revision took the site
down on 2026-08-27.

**Why the split works.** The outage was not caused by the UPDATE being slow.
It was caused by the UPDATE sharing a transaction with three ALTER TABLEs:
Alembic commits a revision as a unit, and a lock lives until its transaction
commits, so the catalogue-only ALTER's ACCESS EXCLUSIVE was held for the
full twenty minutes of the UPDATE. ACCESS EXCLUSIVE conflicts with SELECT,
so serving queries queued and the page stopped loading.

A bare UPDATE takes ROW EXCLUSIVE. That conflicts with other writers of the
same rows and with nothing else -- readers are unaffected no matter how long
it runs. Moving the DML into its own revision removes the conflict entirely
rather than shortening it.

**Batched by year, each batch its own transaction.** Not for lock reasons --
ROW EXCLUSIVE is already harmless to the site -- but for two others:

- **Interruptibility.** CLAUDE.md records that long jobs here must be
  assumed interruptible: the container exited 255 unprompted on 2026-08-21
  and killed a 1h55m backtest, and this very UPDATE was cancelled by hand
  after eight minutes. One transaction means a cancel throws away all of it.
  Per-year commits mean a cancel costs at most one year.
- **Dead tuples.** An UPDATE writes a new row version for every row it
  touches. Doing 1.37M in one transaction means none of that space is
  reclaimable until it commits. On a Pi with an SD card that is worth
  avoiding.

**Idempotent, so a re-run is cheap and a partial run resumes.** Every batch
carries `AND (bb_mid IS NULL OR close IS NULL OR vix_pct_252d IS NULL)`,
which the original revision's UPDATE did not have. Research already ran the
combined version and is fully backfilled, so this revision is a no-op there
and does real work only on serving -- the two databases converge without a
special case for either.

**t-1 lookups, unchanged from `f1c8a260d94e` and proven there**: backfilling
`bb_pctb` through this same lateral matched the stored value on 20,000 of
20,000 rows. Invariant 3 is the highest-risk silent failure in this system,
so the construction is carried over verbatim rather than retyped.
"""

from alembic import op

revision = "a7d4e91c2b35"
down_revision = "f1c8a260d94e"
branch_labels = None
depends_on = None

CONFIG_HASH = "a38d3ca6b58295e8"

# The event history starts in 2010 (universe.as_of min) and the upper bound
# is deliberately past the present: a year with no rows costs one indexed
# lookup, and a hardcoded end that quietly stops backfilling next January is
# the kind of silent gap this file exists to avoid.
FIRST_YEAR = 2010
LAST_YEAR = 2031


def upgrade() -> None:
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        # autocommit_block ends the surrounding transaction for the duration,
        # so each year commits on its own. Without it Alembic would hold one
        # transaction across every batch and the batching would buy nothing.
        with op.get_context().autocommit_block():
            op.execute(
                f"""
                UPDATE events e
                   SET bb_mid = (
                         SELECT i.bb_mid FROM indicators i
                          WHERE i.ticker = e.ticker AND i.interval = '1d'
                            AND i.ts < e.signal_date
                          ORDER BY i.ts DESC LIMIT 1),
                       close = (
                         SELECT b.close FROM bars b
                          WHERE b.ticker = e.ticker AND b.interval = '1d'
                            AND b.ts < e.signal_date
                          ORDER BY b.ts DESC LIMIT 1),
                       vix_pct_252d = (
                         SELECT m.vix_pct_252d FROM market_days m
                          WHERE m.ts < e.signal_date
                          ORDER BY m.ts DESC LIMIT 1)
                 WHERE e.config_hash = '{CONFIG_HASH}'
                   AND e.signal_date >= DATE '{year}-01-01'
                   AND e.signal_date <  DATE '{year + 1}-01-01'
                   AND (e.bb_mid IS NULL OR e.close IS NULL
                        OR e.vix_pct_252d IS NULL)
                """
            )


def downgrade() -> None:
    # Only the three columns this revision fills, and only for the generation
    # it filled. The columns themselves belong to f1c8a260d94e and are that
    # revision's downgrade to drop.
    with op.get_context().autocommit_block():
        op.execute(
            f"UPDATE events SET bb_mid = NULL, close = NULL, vix_pct_252d = NULL "
            f"WHERE config_hash = '{CONFIG_HASH}'"
        )
