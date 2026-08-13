"""indicators.bear_close_above_upper, and the events signal_type value

Revision ID: a9d3c04f7b15
Revises: f1a8d3b62c07
Create Date: 2026-08-13 00:00:00.000000

ADR 108. One new column, and one widened check constraint.

**What the column holds.** `open > close AND close >= bb_upper[t-1]` — a
down bar closing at or above the *prior* upper band. Boolean, nullable, and
the nullability is load-bearing: it is NULL through the 273-bar warmup,
because "no band yet" is not "did not fire" (invariant 4). A NOT NULL
DEFAULT false would read as a measured negative on every warmup bar in the
history, which is a different and false claim.

**Why a stored column rather than a computed one.** Postgres could express
this as a generated column, but not correctly: the comparison needs
`bb_upper` from the *previous* row, and a generated column may only
reference the row it belongs to. A view could do it with a window function,
but then `core/indicators.py` and the view would each hold a copy of the
rule — exactly the duplication invariant 2 exists to prevent. The value is
computed once, in `core/`, and stored.

**The constraint.** `events_signal_type_check` does not currently exist —
`signal_type` is unconstrained text. This revision does **not** add one.
Adding a CHECK over 5.5M+ existing rows takes an ACCESS EXCLUSIVE lock for
the duration of a full table scan, and the value it protects is already
enforced upstream by `SignalType` being an enum in `core/types.py`, which
every writer routes through. A constraint here would restate that guarantee
at the cost of a long lock on a live table.

**What this migration deliberately does not do.**

- It does not backfill the column. `cscan indicators` recomputes it, and
  that is the only writer. A SQL backfill would be a second implementation
  of the rule (invariant 2), and it would have to reimplement the t-1 shift
  correctly to agree with `core/indicators.py` — the exact kind of
  duplicated arithmetic that drifts silently.
- It does not touch `events`. The new `signal_type` value arrives with the
  next backtest under a new `config_hash`; existing rows keep theirs. ADR
  096's composite key on `cell_stats` means the prior config's measurements
  remain valid rather than being replaced.
- It does not rebuild `v_screen`. The view joins on `signal_type` generically
  and needs no change to serve a new value.

**Rollback drops the column.** That discards computed values, which is
recoverable by rerunning `cscan indicators` over the affected window — the
column is derived, never a source of truth. Nothing else references it, so
the drop is clean.

Verify with:

    cscan db status
    psql -c "\\d indicators"
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d3c04f7b15"
down_revision: Union[str, Sequence[str], None] = "f1a8d3b62c07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable boolean.

    `ADD COLUMN` with no default and no NOT NULL is a catalog-only change in
    Postgres 11+: no table rewrite, no full scan, and the ACCESS EXCLUSIVE
    lock is held for microseconds rather than for the duration of a scan
    over the indicators table. That is why the column is nullable rather
    than `NOT NULL DEFAULT false`, quite apart from the invariant-4 reason
    in the docstring above.
    """
    op.add_column(
        "indicators",
        sa.Column("bear_close_above_upper", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    """Drop it. Derived data, recomputable by `cscan indicators`."""
    op.drop_column("indicators", "bear_close_above_upper")
