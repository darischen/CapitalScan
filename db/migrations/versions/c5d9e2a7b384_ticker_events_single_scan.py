"""v_ticker_events: one scan in date order, and an index for the confluence history

**Why.** The ticker page shows 40 history rows and read every event the
ticker has to find them. Measured on the Pi, 2026-10-29 to 2026-11-01:
the history's three reads (`EVENTS_SQL`, `EVENTS_COUNT_SQL`,
`EQUITY_TRADES_SQL`) each fetched all of a ticker's rows in the live
generation, a median 4,554, scattered across ~2,000 pages of a 7 GB table
on an SD card. 0.4-1.8 s cold, 1,160-2,705 card reads per page view.

Two things forced the full read.

1. **No index serves the confluence filter in date order.**
   `signal_types_all && ARRAY['confluence_high','confluence_low']` matches
   30.5% of rows and nothing indexed it, so `ORDER BY signal_date DESC, id
   LIMIT 40` had to fetch and sort every candidate.

2. **The `UNION ALL` hid the order from the planner.** ADR 133's view was
   two branches, `next_open` rows and twinless `touch` rows. Postgres does
   not push an outer `ORDER BY ... LIMIT` into `UNION ALL` branches that
   carry their own `WHERE`, so even with an ordered index it read every
   branch to the end, then sorted. Prototyped on research with the index
   alone: still 736 heap rows for AZO's 40.

**The view now scans once.** The two branches never overlap (they differ in
`entry_kind`), so their union is one filter on one scan:

    entry_kind = 'next_open'
    OR (entry_kind = 'touch' AND NOT EXISTS (its next_open sibling))

with `pending` as a `CASE` on the same condition the second branch used to
project. Same rows, same columns, same order of columns, so `CREATE OR
REPLACE` accepts it. Verified on research before writing this file: zero
differing rows for AZO, QQQ, MMM, PDD and eight tickers that hold twinless
`touch` rows, and across the whole live generation the same count
(2,758,552), id sum, `pending` count (14) and `touch` count (99).

**The index, `events_ticker_confluence`.**

    (config_hash, ticker, signal_date DESC, id)
      INCLUDE (entry_kind, signal_type, entry_date, exit_date, net_ret)
      WHERE signal_types_all && ARRAY['confluence_high','confluence_low']
        AND entry_kind IN ('next_open', 'touch')

- The key is `EVENTS_SQL`'s `WHERE` and `ORDER BY`, so the scan returns
  rows in page order and stops after 40.
- `INCLUDE` carries the columns the count and the `$10k` replay need, so
  both run as index-only scans instead of visiting the heap. `entry_kind`
  and `signal_type` are there for the sibling test.
- The predicate keeps it to the rows these queries read: confluence rows
  of the two kinds this view uses. Research also holds `touch_5m` and
  `touch_30m`, which the predicate leaves out. 345 MB on research.

Measured with the prototype on research (AZO, warm):

    query                  before                     after
    EVENTS, confluence     all rows fetched, sorted   246 buffers, 0.4 ms
    EVENTS, every fire     all rows fetched, sorted   323 buffers, 2.3 ms
    COUNT, confluence      all rows fetched           index-only, 5.6 ms
    EQUITY                 all rows fetched           index-only, 2.8 ms

**What it costs.** The sibling test runs per `touch` row now, as a probe
of the natural-key index, instead of one hashed anti-join over the whole
generation. That is what lets a ticker's read stop early, and it makes a
read of the *entire* view slower: 5 min 36 s on research for every row of
the live generation. Nothing in the app reads the view without a ticker.

**The index-only scans depend on the visibility map.** A page counts as
all-visible only after a vacuum, and serving's `events` was 44.8%
all-visible on 2026-11-01, with 1.88M dead rows since its last vacuum on
2026-10-19. Run `VACUUM (ANALYZE) events` on serving after applying this.
`docs/PI_MIGRATION.md` records the autovacuum setting that keeps it there.

**Not done: a sargable `signal_date` bound on the replay.** The replay
filters on `entry_date`, and a padded `signal_date` bound in front of it is
not safe: the widest gap between signal and `next_open` entry in the live
generation is 97 days, with 63 rows over 30. The index-only scan reads
~10 index pages for a ticker's confluence rows instead, so the bound is
not needed.

Applies to both databases (ADR 053).

Verify:

    cscan db status
    \\d events                     -- events_ticker_confluence, with its WHERE
    EXPLAIN SELECT id FROM v_ticker_events
     WHERE ticker = 'AZO'
       AND signal_types_all && ARRAY['confluence_high','confluence_low']
     ORDER BY signal_date DESC, id LIMIT 40;
    -- expect: Limit -> Nested Loop -> Index Scan using events_ticker_confluence

Revision ID: c5d9e2a7b384
Revises: f2a6c84d1b39
Create Date: 2026-11-01
"""

# ruff: noqa: E501 -- view text kept in `pg_get_viewdef` form, as the migrations before it.
from alembic import op

revision = "c5d9e2a7b384"
down_revision = "f2a6c84d1b39"
branch_labels = None
depends_on = None

INDEX = """CREATE INDEX events_ticker_confluence ON events
    (config_hash, ticker, signal_date DESC, id)
    INCLUDE (entry_kind, signal_type, entry_date, exit_date, net_ret)
    WHERE signal_types_all && ARRAY['confluence_high', 'confluence_low']::text[]
      AND entry_kind IN ('next_open', 'touch')"""

NEW = """SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
        CASE
            WHEN e.entry_kind = 'touch'::text THEN e.in_trade
            ELSE false
        END AS pending,
    e.in_watch
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND (e.entry_kind = 'next_open'::text OR e.entry_kind = 'touch'::text AND NOT (EXISTS ( SELECT 1
           FROM events n
          WHERE n.config_hash = e.config_hash AND n.ticker = e.ticker AND n.signal_date = e.signal_date AND n.signal_type = e.signal_type AND n.entry_kind = 'next_open'::text)))"""

# `v_ticker_events` as `d8c40a5b71e9` defined it, kept verbatim so
# `downgrade()` restores exactly that.
OLD = """SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    false AS pending,
    e.in_watch
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND e.entry_kind = 'next_open'::text
UNION ALL
 SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    e.in_trade AS pending,
    e.in_watch
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
  WHERE e.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND e.entry_kind = 'touch'::text AND NOT (EXISTS ( SELECT 1
           FROM events n
          WHERE n.config_hash = e.config_hash AND n.ticker = e.ticker AND n.signal_date = e.signal_date AND n.signal_type = e.signal_type AND n.entry_kind = 'next_open'::text))"""


def upgrade() -> None:
    # The index first, so the first read of the new view already has it.
    op.execute(INDEX)
    op.execute("CREATE OR REPLACE VIEW v_ticker_events AS " + NEW)


def downgrade() -> None:
    # The view first: the old text does not reference the index, so the
    # drop below cannot fail on a dependency.
    op.execute("CREATE OR REPLACE VIEW v_ticker_events AS " + OLD)
    op.execute("DROP INDEX IF EXISTS events_ticker_confluence")
