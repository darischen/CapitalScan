"""ADR 149 amendment: a third watch_reason, 'near_trade'.

`core.universe.watch_reason` now admits a name to `in_watch` when
`crit_mcap`, `crit_above_sma200` and `crit_sma200_slope` all pass and
`crit_rel_return` alone does not -- unjudgeable (`None`, past the history
floor `WATCH_HISTORY` covers) or judged and failed (`False`, a real
sector-median shortfall). User's decision, 2026-09-02: `in_watch` exists to
surface names worth detecting on that are not quite `in_trade` yet, and
failing on relative return alone while the trend and size gates hold is
exactly that -- the same "one named failure, not any of four" shape
`pullback` already has for `crit_above_sma200`.

**The CHECK constraint from `a4e91c7b2f58` is the same discipline as before,
just wider.** `watch_reason IN ('history', 'pullback')` would reject the new
value at the database, not just leave it unblessed by a docstring, so this
widens the same three invariants ADR 149 states in prose rather than
loosening them:

- a watched row must say why (`in_watch` implies a reason),
- an unwatched row must not (no orphan reason),
- `in_trade` and `in_watch` stay disjoint.

Postgres has no `ALTER CONSTRAINT ... ADD VALUE` for a CHECK (unlike an
`ALTER TYPE ... ADD VALUE` on a real enum) -- the only way to change one is
drop and recreate, so both directions do exactly that.

**No existing row changes.** This widens what `watch_reason` may say next
time `cscan universe` runs; it does not backfill history. A ticker whose
only blocking criterion is `crit_rel_return` keeps its existing `in_watch =
NULL`/`False` row from before this migration until the next `cscan
universe --quarter` pass re-evaluates it -- same as every other criteria
change in this file's history (`a4e91c7b2f58` made the identical point about
the column existing before any row used it).

Revision ID: e2c7a94b3d15
Revises: b7f3c5d21a94
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op

revision = "e2c7a94b3d15"
down_revision = "b7f3c5d21a94"
branch_labels = None
depends_on = None

_CHECK_NAME = "universe_watch_consistent"

_OLD_CHECK = (
    "((in_watch IS NOT TRUE AND watch_reason IS NULL) "
    " OR (in_watch IS TRUE AND watch_reason IN ('history', 'pullback'))) "
    "AND NOT (in_trade AND in_watch IS TRUE)"
)

_NEW_CHECK = (
    "((in_watch IS NOT TRUE AND watch_reason IS NULL) "
    " OR (in_watch IS TRUE AND watch_reason IN ('history', 'pullback', 'near_trade'))) "
    "AND NOT (in_trade AND in_watch IS TRUE)"
)


def upgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "universe", type_="check")
    op.create_check_constraint(_CHECK_NAME, "universe", _NEW_CHECK)


def downgrade() -> None:
    # Recreating the narrower constraint would fail if any row already
    # carries `watch_reason = 'near_trade'` -- correct, not a bug: a
    # downgrade that silently dropped those rows' reason would be worse
    # than an error naming exactly which rows block it.
    op.drop_constraint(_CHECK_NAME, "universe", type_="check")
    op.create_check_constraint(_CHECK_NAME, "universe", _OLD_CHECK)
