"""forward_log_exclusions: forward-log rows set aside because their inputs were corrupt

**Why (ADR 210).** On 2026-11-24 the forward log held 34 predictions whose
prices were wrong when they were scored or resolved. EMBJ's split repair
divided its history by 4 nine times between 2026-11-16 and the rebuild on
2026-11-22, and outcomes resolved in that window read 5-day returns of up to
17,892 (1,789,200%). MNST, APH and CTVA resolved against unadjusted splits
in August and September. The bars were repaired afterwards; the forward log
keeps what it first wrote (ADR 029, ADR 195), so the bad numbers stayed and
any average over `realized_ret_5d` was meaningless.

**A table beside the log, not a column on it.** `predictions` and `outcomes`
stay exactly as written. An exclusion is a separate statement about a row,
with its own reason, `run_id` and `git_sha` (invariant 6). It also syncs on
its own key: `predictions` syncs insert-only, so a column set on research
would never reach serving's existing rows.

`ON DELETE CASCADE`: `cscan predict --clear` deletes a generation's
predictions, and an exclusion of a deleted row has nothing left to say.

Applies to both databases (ADR 053).
"""

from __future__ import annotations

from alembic import op

revision = "a3c9e1f7b204"
down_revision = "e2b6f4a8c913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE forward_log_exclusions (
            prediction_id bigint PRIMARY KEY
                REFERENCES predictions(id) ON DELETE CASCADE,
            reason        text        NOT NULL,
            detail        text,
            excluded_at   timestamptz NOT NULL DEFAULT now(),
            run_id        text        NOT NULL,
            git_sha       text        NOT NULL
        )
        """
    )
    # The serving web role reads it by grant, like `outcomes`.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'capscan_ro') THEN "
        "GRANT SELECT ON forward_log_exclusions TO capscan_ro; END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS forward_log_exclusions")
