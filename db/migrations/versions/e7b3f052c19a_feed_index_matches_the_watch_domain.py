"""The feed index must match the widened domain, or it is not used at all

Revision ID: e7b3f052c19a
Revises: d4a17c93f60b
Create Date: 2026-08-27 03:00:00.000000

**A 1,900x regression, introduced by a4f8c21d7e63 and found in production.**

That revision widened `v_screen_live`'s predicate from `e.in_trade` to
`(e.in_trade OR e.in_watch)` so watch-universe fires reach the screener.
The index built to serve that view is partial:

    events_feed_latest btree (config_hash, entry_kind, signal_date DESC)
                       WHERE in_trade

`(in_trade OR in_watch)` is **not implied by** `WHERE in_trade`, so the
planner cannot prove the index covers the query and stops using it. Same
query, same connection, one added disjunct:

    ... AND in_trade                 index scan          8.9 ms
    ... AND (in_trade OR in_watch)   parallel seq scan  17,184 ms

    Parallel Seq Scan on events (actual time=89.612..17102.784
                                 rows=113846 loops=3)
      Filter: ((in_trade OR in_watch) AND entry_kind = 'touch' AND ...)
      Rows Removed by Filter: 299963
      Buffers: shared hit=13102 read=153979

`loops=3` is two workers plus the leader -- three of the Pi's four cores --
and 154k buffer reads off an SD card. The home page took **35.2 seconds**.
`lib/screen.ts` records the same query at 265 ms on 2026-08-19.

**Nothing in the widening was wrong; the index simply stopped matching it.**
A partial index is a promise about a predicate, and changing the predicate
silently voids it. No error, no plan warning -- only a page that got slow.

**`events_feed_latest` is kept, not replaced.** It still serves any query
that genuinely means `in_trade` alone -- `cell_stats` reads the trade
population, and ADR 149 is explicit that no statistic reads `in_watch`.
Dropping it would trade this regression for a different one.

**Built without CONCURRENTLY**, deliberately. Alembic runs a migration
inside a transaction and `CREATE INDEX CONCURRENTLY` cannot; splitting the
revision out of the transaction to gain concurrency is not worth it for a
6M-row table, and this runs while the operator is watching rather than
against a live nightly.
"""

from alembic import op

revision = "e7b3f052c19a"
down_revision = "d4a17c93f60b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Column order mirrors `events_feed_latest`: the two equality
    # predicates first, then the ordering column descending, which is how
    # `LATEST_DATE_SQL` and the feed query both read it.
    op.execute(
        "CREATE INDEX IF NOT EXISTS events_feed_watch "
        "ON events (config_hash, entry_kind, signal_date DESC) "
        "WHERE (in_trade OR in_watch)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS events_feed_watch")
