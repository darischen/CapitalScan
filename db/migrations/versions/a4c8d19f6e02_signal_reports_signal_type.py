"""signal_reports gains signal_type, so v_screen_live can stop approximating

Revision ID: a4c8d19f6e02
Revises: e8c2f43a91d7
Create Date: 2026-08-29 21:40:00.000000

**The approximation this removes.** `e8c2f43a91d7` had to resolve
`v_screen_live.fired_at` by matching `(ticker, signal_date)`, because ADR
150's nightly sweep nulls `signal_reports.event_id` by design and the row
carries nothing else that identifies which event it belongs to. One ticker
firing two different signal types on one day therefore gives both events the
*same* timestamp — the later one, since that revision takes `max`.

The poller already has the value. `signal_type` is in scope at the
`signal_reports` write in `jobs/poll.py`; it goes into the notification
subject line and the body, and was simply never stored. So this is a column
and one dictionary key, not new logic.

**Nullable, and no backfill.** Existing rows genuinely do not know their
signal type — `state_json` carries indicator state (`k_full`, `bb_upper`,
`bear_reversal`) but no signal type, and the events that would have supplied
it are exactly the ones the sweep deleted. Inventing a value for 1,831
historical rows would be fabrication; a NULL says "not recorded", which is
true. The view keeps working for those rows through the existing
`(ticker, signal_date)` match.

**The view is not changed here.** It should prefer `signal_type` when the
column is populated and fall back to the ticker/date match when it is NULL,
which is a behaviour change worth its own revision once real rows exist —
the poller starts writing the value from the next session, so there is
nothing to prefer yet. Recorded in `BACKLOG.md`.

Nullable with no default, so this is a catalogue-only ALTER on Postgres 11+:
no table rewrite, and the ACCESS EXCLUSIVE lock is held for a catalogue
update rather than for a scan of 1,831 rows.
"""

from alembic import op

revision = "a4c8d19f6e02"
down_revision = "e8c2f43a91d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE signal_reports ADD COLUMN IF NOT EXISTS signal_type text")
    op.execute(
        "COMMENT ON COLUMN signal_reports.signal_type IS "
        "'The signal type this report fired on, written by the poller. NULL on "
        "rows predating 2026-08-29: state_json carries indicator state but no "
        "signal type, and the events that would have supplied it were removed "
        "by ADR 150 sweeps. NULL means not recorded, never unknown-so-guessed.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN signal_reports.signal_type IS NULL")
    op.execute("ALTER TABLE signal_reports DROP COLUMN IF EXISTS signal_type")
