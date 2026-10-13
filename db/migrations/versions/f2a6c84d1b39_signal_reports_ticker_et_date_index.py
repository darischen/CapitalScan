"""signal_reports: an index for the three laterals `v_screen_live` runs per row

**Why.** `v_screen_live` joins `signal_reports` three times per event row --
`fired_at`, the bear reversal, the bull reversal -- each on

    r.ticker = e.ticker
    AND (r.fired_at AT TIME ZONE 'America/New_York')::date = e.signal_date

and the table carried only its primary key, so each was a sequential scan.
Measured on the Pi, 2026-10-13, 70 confluence rows against 9,476 reports:
210 scans, **6.3 s of a 6.83 s query**, 595,140 of 596,967 buffer hits. The
home page took 7.2 s on loopback and `?all=1` (185 rows) 25.7 s. The cost
is event rows x report rows x 3, and the table grows ~215 rows a session,
so the page slowed about 2.3% every trading day with no ceiling.

**An expression index, because the predicate wraps the column.** A plain
index on `fired_at` cannot serve `(fired_at AT TIME ZONE ...)::date = ...`;
the planner matches an expression index only when the expression is the
same one, so this repeats the view's text exactly. `timezone(text,
timestamptz)` is IMMUTABLE with a constant zone, which is what lets it be
indexed at all.

**`fired_at DESC` as the third column.** All three laterals want the newest
row for the pair (`max(fired_at)`, and `ORDER BY fired_at DESC LIMIT 1`
twice), so the index hands it over in order and the sort goes away.

`ANALYZE` afterwards: an expression index has no statistics until one runs,
and the planner would cost it blind.

Applies to both databases (ADR 053). No view changes.

Revision ID: f2a6c84d1b39
Revises: e4b7a2c9d160
Create Date: 2026-10-13
"""

from alembic import op

revision = "f2a6c84d1b39"
down_revision = "e4b7a2c9d160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX signal_reports_ticker_et_date ON signal_reports "
        "(ticker, ((fired_at AT TIME ZONE 'America/New_York')::date), fired_at DESC)"
    )
    op.execute("ANALYZE signal_reports")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS signal_reports_ticker_et_date")
