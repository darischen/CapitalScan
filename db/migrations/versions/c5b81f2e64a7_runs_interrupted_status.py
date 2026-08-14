"""runs.status gains 'interrupted'

Revision ID: c5b81f2e64a7
Revises: a9d3c04f7b15
Create Date: 2026-08-14 00:00:00.000000

`runs_status_check` permits `running`, `ok`, and `failed`. A job killed by the
machine sleeping leaves its row at `running` forever, because the status is
written by a context manager that never got to exit.

**Why this needed a schema change rather than a cleanup script.** CLAUDE.md
records the reasoning that produced the current state: "There is no honest
terminal status to rewrite it to (`runs_status_check` allows only
`running`/`ok`/`failed`, and neither `ok` nor `failed` is true of an
interrupted run), so `system-status` flags them by age instead of mutating
history."

That reasoning is correct and its conclusion was the wrong one. The
constraint being too narrow is an argument for widening it, not for leaving
twelve rows asserting they are still running. Measured 2026-08-14: twelve
such rows, the oldest eleven days old, three of them `indicators` runs that
died on separate days.

**Why not delete them.** Seven of the twelve reference nothing and could be
deleted. The other five cannot:

- Four anchor 124 `events` rows, and `events_run_id_fkey` is `NO ACTION`, so
  Postgres refuses the delete outright.
- One anchors **402,492 `bars`** rows. `bars.run_id` carries no foreign key,
  so that delete would succeed and silently leave 402k rows pointing at a
  run that no longer exists — invariant 6 broken with nothing raising.

Deleting the seven that are safe would also erase the record that
`indicators` was attempted and failed three times, which is information
rather than noise.

**What `interrupted` means.** The job started, wrote whatever it wrote, and
never reached its terminal state. Distinct from `failed`, which means the
job ran to a conclusion and that conclusion was an error — a distinction
`cscan system-status` needs in order to say "the machine slept" rather than
"this job is broken."

**Applying this does not by itself change any row.** Widening a CHECK
constraint is permissive: every existing row still satisfies it. The twelve
rows are updated by a separate, explicit statement after this migration, so
the schema change and the data change are reviewable independently.

Verify with:

    cscan db status
    psql -c "\\d runs"
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5b81f2e64a7"
down_revision: Union[str, Sequence[str], None] = "a9d3c04f7b15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUSES_NEW = "'running', 'ok', 'failed', 'interrupted'"
_STATUSES_OLD = "'running', 'ok', 'failed'"


def _recreate(values: str) -> None:
    """Drop and recreate the CHECK. Postgres has no `ALTER CONSTRAINT` for a
    CHECK's expression, so replacement is the only route.

    The drop and the add run inside Alembic's transaction, so a failure to
    validate leaves the old constraint in place rather than an unconstrained
    table.
    """
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check")
    op.execute(f"ALTER TABLE runs ADD CONSTRAINT runs_status_check CHECK (status IN ({values}))")


def upgrade() -> None:
    """Permit `interrupted`.

    Widening only: every row that satisfied the old constraint satisfies this
    one, so the validating scan cannot fail and no row changes.
    """
    _recreate(_STATUSES_NEW)


def downgrade() -> None:
    """Narrow back to three statuses.

    **This fails if any row still reads `interrupted`**, and that is the
    correct behaviour rather than a flaw: silently rewriting those rows to
    `failed` would assert something untrue about them, and rewriting them to
    `running` would resurrect the exact defect this revision exists to fix.
    A human deciding to roll back should decide what those rows become.
    """
    _recreate(_STATUSES_OLD)
