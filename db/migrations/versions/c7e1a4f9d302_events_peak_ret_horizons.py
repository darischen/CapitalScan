"""events peak_ret_1d/2d/3d/5d/10d columns

Revision ID: c7e1a4f9d302
Revises: b2e5d81a4c76
Create Date: 2026-08-09 00:00:00.000000

ADR 093's peak-within-horizon amendment (2026-08-09). Materializes the
running-maximum label family

    M_h = max over 1 <= t <= h of R_t,   h in {1, 2, 3, 5, 10}

as five columns on `events`, alongside the existing terminal family
(`fwd_ret_*d`) and the binary reachability family (`touched_*pct`).

Schema design:

- `numeric(12,6)`, nullable. Same type as `path.favorable`, which these
  are the running maximum of, so the cache cannot lose precision the
  source retained. Same type and nullability as every other per-event
  label column (`mfe`, `mae`, `capture_ratio`, `fwd_ret_*d` — see
  `01c32499e1b2_events.py`), since a peak return is the same shape of
  quantity computed the same way.

- Nullable and NOT backfilled by this migration. An unfilled entry has no
  forward position and therefore no peak (invariant 4 forbids inventing
  one), and an event whose forward window is still open has a peak that
  will grow. NULL is the honest value until the population path runs.

- No index. Phase 4 reads these in bulk alongside the other label columns
  on rows already selected by `config_hash` and `split_key`, both of which
  are indexed. An index on a label column would pay write cost on every
  refresh for a scan these queries do not perform.

Why materialize at all, when `path` already holds the data: DESIGN §6.1
pins the statistics job as reading `events`. Joining `path` (57M rows)
inside it is far heavier than reading a column, and ADR 094 explicitly
provides for this — "any future label family that needs efficiency can add
a materialized column in a separate migration." `path` stays the source of
truth; these are a cache refreshed by the same weekly `run_backtest` that
already refreshes `mfe` and `touched_*pct`.

**The population path ships with this migration, deliberately.**
`699cb410d219_events_giveback.py` added `events.giveback` with no writer,
and that column has been NULL on all 5.57M rows ever since. See
`research/path_labels.py::derive_labels_from_path` for the writer here.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e1a4f9d302"
down_revision: Union[str, Sequence[str], None] = "b2e5d81a4c76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HORIZONS = (1, 2, 3, 5, 10)


def upgrade() -> None:
    """Add one nullable numeric(12,6) column per horizon.

    `ADD COLUMN` with no DEFAULT and no NOT NULL is a catalog-only change
    in Postgres 11+: existing rows are not rewritten, so this returns in
    milliseconds even against 11.4M rows. It still takes a brief ACCESS
    EXCLUSIVE lock, so do not run it while a job is writing `events`
    (CLAUDE.md: no migrate while a job is running).
    """
    for h in _HORIZONS:
        op.execute(f"ALTER TABLE events ADD COLUMN peak_ret_{h}d numeric(12,6)")


def downgrade() -> None:
    """Drop the five columns, returning `events` to its pre-migration shape.

    Reversible without data loss in any sense that matters: every value in
    these columns is derivable from `path`, which this migration does not
    touch. Re-running `upgrade()` plus the population path reconstructs
    them exactly.
    """
    for h in _HORIZONS:
        op.execute(f"ALTER TABLE events DROP COLUMN peak_ret_{h}d")
