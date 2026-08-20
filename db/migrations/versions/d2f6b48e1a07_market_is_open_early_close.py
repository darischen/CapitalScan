"""market_is_open() honours early closes; the calendar already knew

Revision ID: d2f6b48e1a07
Revises: b6e4a1c3d905
Create Date: 2026-08-20 01:10:00.000000

`b6e4a1c3d905` closed the live price at 16:00 ET every trading day and
recorded half-days as an accepted limitation: "modelling them needs a
holiday calendar with session lengths, which nothing here has."

That was wrong twice. `trading_days.is_early_close` has existed since the
reference-tables migration, and `jobs/ingest.py:177` populates it from
measured session length -- 38 days marked across 2009-2026. Verified
against the hourly bars, which agree exactly:

    regular session   7 bars, last 15:30 ET
    half-day          3 bars, last 11:30 ET

    2024-11-29  2024-12-24  2025-07-03  2025-11-28  2025-12-24
    all is_early_close = true, all 3 bars

The data was present, correct, and unread. The cost of not asking was a
price labelled live for three hours after trading ended, on roughly two
afternoons a year.

The function now closes at 13:00 ET on a flagged day and 16:00 otherwise.
Still `STABLE`, so the calendar lookup is evaluated once per statement
rather than per row.

**Per ADR 125 the SQL below is a literal, never an import.**

**Verify:**

    psql -c "SELECT market_is_open()"
    psql -c "SELECT d FROM trading_days WHERE is_early_close AND d >= '2026-01-01'"
"""

from alembic import op

revision = "d2f6b48e1a07"
down_revision = "b6e4a1c3d905"
branch_labels = None
depends_on = None

NEW_DDL = """CREATE OR REPLACE FUNCTION public.market_is_open() RETURNS boolean
    LANGUAGE sql
    STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::time >= TIME '09:30'
             AND (now() AT TIME ZONE 'America/New_York')::time <
                 CASE WHEN EXISTS (
                        SELECT 1 FROM public.trading_days td
                         WHERE td.d = (now() AT TIME ZONE 'America/New_York')::date
                           AND td.is_early_close)
                      THEN TIME '13:00' ELSE TIME '16:00' END $$
"""

# The 16:00-every-day version, so `downgrade()` restores it exactly.
OLD_DDL = """\nCREATE OR REPLACE FUNCTION public.market_is_open() RETURNS boolean
    LANGUAGE sql
    STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::time
                 >= TIME '09:30'
             AND (now() AT TIME ZONE 'America/New_York')::time
                 <  TIME '16:00' $$
"""


def upgrade() -> None:
    op.execute(NEW_DDL)


def downgrade() -> None:
    op.execute(OLD_DDL)
