"""Backfill events.sector and events.mcap_usd for the serving config

Revision ID: c2b91e4a7d08
Revises: a4f8c21d7e63
Create Date: 2026-08-27 01:30:00.000000

Both columns have existed on `events` since the table was created and both
were empty on **every row** -- 0 of 6,021,249 after the orphan-config purge.
`BACKLOG.md` records the hazard as the shape rather than the outcome: an
unqualified `sector` in a query over `events JOIN tickers` resolves to the
`events` copy, a column that exists, is spelled correctly, and is empty.
Both model features were one missing prefix from shipping as all-NULL with
no test objecting, because "the column is there" and "the column has
values" are different claims.

The entry offered two ways out -- populate them or drop them. **Populate**
(user's decision, 2026-08-27), for data completeness.

**One config hash, full history** (user's decision). `a38d3ca6b58295e8` is
the config in service on 2026-08-27, 1,367,741 rows of the 6,021,249 on
file. The other five hashes stay NULL: they are superseded generations
nothing reads, and rewriting 4.6M rows to complete a column no query
touches is cost without a reader.

**The two columns have different honesty.**

`mcap_usd` is genuinely point-in-time. `universe` is evaluated quarterly and
the lateral is bounded `as_of <= signal_date`, so an event gets the market
cap that was on file when it fired -- the same construction `watch_reason`
uses, and the reason `BACKLOG.md` notes `universe.mcap_usd` does *not*
suffer the problem `tickers.sector` does.

`sector` is **not**. `tickers.sector` has no history, so a company GICS
reclassified in 2018 carries its post-2018 sector on its 2010 events. That
is the mild look-ahead ADR 135 names and `BACKLOG.md` already accepts;
populating the column does not make it worse, but it does make it *look*
authoritative, so the column comment says so in the database rather than
only here.

**Nothing reads these columns yet.** `research/features.py` reads sector and
market cap from `tickers` and `universe` directly, qualified, and
`test_model_features.py` pins those sources. This backfill removes a trap
and completes the data; it changes no computed result today.
"""

from alembic import op

revision = "c2b91e4a7d08"
down_revision = "a4f8c21d7e63"
branch_labels = None
depends_on = None

CONFIG_HASH = "a38d3ca6b58295e8"


def upgrade() -> None:
    # `mcap_usd` first: point-in-time, from the universe evaluation in force
    # when the event fired. A correlated subquery in SET, not `FROM LATERAL`
    # -- the target of an UPDATE is not visible inside its own FROM clause,
    # which is the error a4f8c21d7e63 hit on its first attempt
    # (`InvalidColumnReference: invalid reference to FROM-clause entry`).
    op.execute(
        f"""
        UPDATE events e
           SET mcap_usd = (
                 SELECT u.mcap_usd
                   FROM universe u
                  WHERE u.ticker = e.ticker
                    AND u.as_of <= e.signal_date
                    AND u.mcap_usd IS NOT NULL
                  ORDER BY u.as_of DESC
                  LIMIT 1
               )
         WHERE e.config_hash = '{CONFIG_HASH}'
        """
    )

    # `sector` is a plain join: `tickers` carries one current value per
    # ticker and no history. See the module docstring -- this is the
    # accepted snapshot, not a point-in-time read.
    op.execute(
        f"""
        UPDATE events e
           SET sector = t.sector
          FROM tickers t
         WHERE t.ticker = e.ticker
           AND e.config_hash = '{CONFIG_HASH}'
           AND t.sector IS NOT NULL
        """
    )

    # Said in the database, not only in this file. A reader who finds the
    # column populated has no other way to learn that one of the two is a
    # current snapshot applied backwards.
    op.execute(
        "COMMENT ON COLUMN events.sector IS "
        "'GICS sector from tickers.sector, which has NO history: a company "
        "reclassified in 2018 carries its post-2018 sector on its 2010 "
        "events. Mild look-ahead, accepted (ADR 135, BACKLOG). Use "
        "tickers.sector directly if you need to know it is a snapshot.'"
    )
    op.execute(
        "COMMENT ON COLUMN events.mcap_usd IS "
        "'Market cap from the universe evaluation in force at signal_date "
        "(as_of <= signal_date). Point-in-time, unlike sector.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN events.sector IS NULL")
    op.execute("COMMENT ON COLUMN events.mcap_usd IS NULL")
    # Only what this revision wrote. Scoped to the one config hash so a
    # downgrade cannot clear values some later revision populated for
    # another generation.
    op.execute(
        f"UPDATE events SET sector = NULL, mcap_usd = NULL WHERE config_hash = '{CONFIG_HASH}'"
    )
