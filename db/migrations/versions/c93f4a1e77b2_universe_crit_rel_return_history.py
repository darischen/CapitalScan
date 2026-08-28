"""universe gains crit_rel_return_history — arm 3's separable criterion

Revision ID: c93f4a1e77b2
Revises: a7d4e91c2b35
Create Date: 2026-08-28 03:15:00.000000

ADR 014 defines `crit_rel_return` as two things at once: "trailing 3-year
total return above the sector median" is a **history requirement** (757
daily bars) *and* a **relative-performance test**. Arm 3 keeps the first and
drops the second, so it needs a criterion that expresses only the first.

**Why a second column rather than a config flag.** `config_hash` is
`sha256(asdict(Config))`, so adding a config *field* moves the hash even at
its default value — measured, it took the default from `a38d3ca6b58295e8`
to `be4e4702241ce90c`, orphaning a fully built and harness-passed
generation and leaving nightly writing a generation the site cannot see.

Naming a second criterion instead means arm 3 changes the **value** of the
existing `required_criteria` tuple. The hash moves for the arm, as ADR 060
requires, and does not move for anyone else.

It also matches what `REBUILD_ARMS.md` asks for: `crit_rel_return` "stays
computed and honest in the audit log; it simply stops deciding membership".
Both criteria are evaluated and stored on every row from now on; only which
one `required_criteria` names changes.

**Nullable with no backfill, deliberately.** The column is `None` for every
row written before this, and that is the honest value: those evaluations did
not compute it. Invariant 4 — an absent value is absent, not fabricated.
Backfilling it would also be wrong in a subtler way, since `None` is
meaningful here (it is what ADR 149's `history` watch route keys on) and a
backfill cannot distinguish "not computed" from "no history".

Catalogue-only: nullable, no default, so no table rewrite (Postgres 11+).
No DML in this revision — a7d4e91c2b35 records why DDL and a long UPDATE
must never share one.
"""

from alembic import op

revision = "c93f4a1e77b2"
down_revision = "a7d4e91c2b35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE universe ADD COLUMN IF NOT EXISTS crit_rel_return_history boolean")
    op.execute(
        "COMMENT ON COLUMN universe.crit_rel_return_history IS "
        "'ADR 014 history gate alone: TRUE when 757 bars of return exist, "
        "NULL when they do not. Never FALSE -- ADR 149 watch route keys on "
        "NULL. Decides membership only when required_criteria names it "
        "(arm 3). NULL on rows written before c93f4a1e77b2.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN universe.crit_rel_return_history IS NULL")
    op.execute("ALTER TABLE universe DROP COLUMN IF EXISTS crit_rel_return_history")
