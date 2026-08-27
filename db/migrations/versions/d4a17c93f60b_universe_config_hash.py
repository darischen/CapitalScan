"""universe.config_hash: which definition produced this membership

Revision ID: d4a17c93f60b
Revises: c2b91e4a7d08
Create Date: 2026-08-27 02:10:00.000000

`universe` was `PRIMARY KEY (ticker, as_of)` with no record of the config
that evaluated it, so two configs' membership could not coexist --
evaluating a second one overwrote the first, row for row.

**Two things followed, and the second is the sharp one.** The three-arm
ablation plan needed a full 66-quarter pass per arm, because each arm
destroyed the previous one's table. And the poller builds its ticker list
from `universe.in_trade` while `v_universe` feeds the site, so after any
arm ran, **live membership was that arm's** whether or not it was the one
meant to be serving. Restoring production meant another 20-minute pass.

ADR 060 makes universe definition part of the config. This closes the gap
where the table storing that definition's output could not say which
definition it was -- a stale `universe` and a current one were
indistinguishable by inspection.

**Existing rows become `'unknown'`, not the current hash.**

Nothing on file records what produced them. `runs` for the `universe` job
stores only `{"quarter": "2026Q2"}` -- no config, no hash -- so labelling
78,554 rows `a38d3ca6b58295e8` would be a guess written down as a fact. The
last pass did run at 00:09-00:11 on 2026-08-26, which is when the NYSE
rebuild ran, and that is *inference*. `'unknown'` is what is actually
known.

**This means the table serves nothing until a pass re-tags it**, because
every reader now scopes on a real hash. That is deliberate: an empty
screener is loud, and a wrongly-labelled universe is silent. The 66-quarter
pass costs ~20 minutes (measured, ~18 s/quarter) and writes correctly
tagged rows that coexist with the `'unknown'` ones under the new key.

Delete the `'unknown'` rows once a tagged pass is verified. This migration
does not, because a downgrade could not put them back.

**`NOT NULL` with the default dropped afterwards** so a writer that forgets
to set it fails loudly on the next insert rather than silently creating a
second `'unknown'` generation.
"""

from alembic import op

revision = "d4a17c93f60b"
down_revision = "c2b91e4a7d08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `DEFAULT` in the same statement makes this catalogue-only on Postgres
    # 11+ -- no rewrite of 78,554 rows -- and backfills every existing row
    # in one step. Dropping the default immediately afterwards is what makes
    # a forgetful writer fail instead of inventing another 'unknown' batch.
    op.execute("ALTER TABLE universe ADD COLUMN config_hash text NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE universe ALTER COLUMN config_hash DROP DEFAULT")

    op.execute("ALTER TABLE universe DROP CONSTRAINT universe_pkey")
    op.execute(
        "ALTER TABLE universe ADD CONSTRAINT universe_pkey PRIMARY KEY (ticker, as_of, config_hash)"
    )

    # Every hot reader filters on the hash and then takes the newest `as_of`
    # at or before a date. Without this the lateral in `v_ticker_state`,
    # `features.py` and `v_watchlist` degrades to a scan per row.
    op.execute(
        "CREATE INDEX IF NOT EXISTS universe_config_ticker_asof_idx "
        "ON universe (config_hash, ticker, as_of DESC)"
    )

    # **The three views that read `universe` must scope too**, or the
    # lateral takes whichever generation happens to have the later `as_of`.
    # They use the GUC rather than a literal, the same way `v_screen_live`
    # already scopes `events` (ADR 115): `web/lib/db.ts` sets it per
    # connection from `serving_config`, so the site and these views cannot
    # disagree about which generation is live.
    #
    # `v_screen_live` is not here: its only match for "universe" is the
    # string literal `'watch_universe'`, not a read of the table.
    op.execute(
        """CREATE OR REPLACE VIEW v_universe AS
SELECT DISTINCT ON (u.ticker) u.ticker,
    t.name,
    t.sector,
    t.industry,
    u.as_of,
    u.in_train,
    u.in_trade,
    u.mcap_usd,
    u.mcap_rank,
    u.adv_20d_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    t.is_active,
    t.delisted_on,
    u.in_watch,
    u.watch_reason
   FROM universe u
     JOIN tickers t ON t.ticker = u.ticker
  WHERE u.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
  ORDER BY u.ticker, u.as_of DESC"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW v_ticker_state AS
SELECT i.ticker,
    t.name,
    t.sector,
    i.ts AS as_of,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.bb_pctb,
    i.bb_width,
    i.bb_width_pct,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.k_cross_up,
    i.k_cross_down,
    i.sma_200,
    i.sma200_slope_60,
    i.atr_14,
    i.rv_20d,
    i.rv_pct_252d,
    i.vol_z_20d,
    i.dd_52w,
    i.days_to_earnings,
    m.vix_close,
    m.vix_pct_252d,
    m.spx_ret_1d,
    u.in_trade,
    u.mcap_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    b.close > i.sma_200 AS above_sma200,
    u.in_watch,
    u.watch_reason
   FROM tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM indicators ind
          WHERE ind.ticker = t.ticker AND ind."interval" = '1d'::text AND (EXISTS ( SELECT 1
                   FROM bars bb
                  WHERE bb.ticker = ind.ticker AND bb.ts = ind.ts AND
                      bb."interval" = ind."interval"))
          ORDER BY ind.ts DESC
         LIMIT 1) latest
     JOIN indicators i ON i.ticker = t.ticker AND i.ts = latest.ts AND i."interval" = '1d'::text
     JOIN bars b ON b.ticker = i.ticker AND b.ts = i.ts AND b."interval" = i."interval"
     LEFT JOIN market_days m ON m.ts = i.ts::date
     LEFT JOIN LATERAL ( SELECT u2.in_trade,
            u2.mcap_usd,
            u2.crit_mcap,
            u2.crit_above_sma200,
            u2.crit_sma200_slope,
            u2.crit_rel_return,
            u2.crit_rev_growth,
            u2.in_watch,
            u2.watch_reason
           FROM universe u2
          WHERE u2.ticker = i.ticker AND u2.as_of <= i.ts::date
            AND u2.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON true"""
    )
    # DROP/CREATE, not REPLACE: `v_watchlist` is dropped in its own
    # downgrade for the same reason -- CREATE OR REPLACE cannot change a
    # view's column set, and this one is rebuilt wholesale.
    op.execute("DROP VIEW IF EXISTS v_watchlist")
    op.execute(
        """CREATE VIEW v_watchlist AS
SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.touch_level,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.k_cross_up,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.cofire_count,
    t.sector,
    u.watch_reason,
    u.mcap_usd,
    u.crit_rel_return,
    u.crit_above_sma200,
    u.crit_sma200_slope
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
     JOIN LATERAL ( SELECT u2.watch_reason,
            u2.mcap_usd,
            u2.crit_rel_return,
            u2.crit_above_sma200,
            u2.crit_sma200_slope
           FROM universe u2
          WHERE u2.ticker = e.ticker AND u2.as_of <= e.signal_date
            AND u2.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON true
  WHERE e.is_cluster_head AND e.entry_kind = 'next_open'::text AND e.in_watch AND
      e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""
    )

    op.execute(
        "COMMENT ON COLUMN universe.config_hash IS "
        "'The config whose UniverseParams produced this row. ''unknown'' "
        "marks rows that predate this column - nothing recorded what "
        "evaluated them. Readers must scope on a real hash; see ADR 060.'"
    )


def downgrade() -> None:
    # The 'unknown' rows are the pre-migration set, so collapsing back to
    # (ticker, as_of) requires that no second generation exists. Deleting
    # the tagged rows is the only way back that cannot violate the old key,
    # and it is lossless in the sense that matters: they are reproducible by
    # re-running the pass that wrote them.
    # Views first: they reference the column this downgrade drops.
    op.execute("DROP VIEW IF EXISTS v_watchlist")
    op.execute(
        """CREATE VIEW v_watchlist AS
SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.touch_level,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.k_cross_up,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.cofire_count,
    t.sector,
    u.watch_reason,
    u.mcap_usd,
    u.crit_rel_return,
    u.crit_above_sma200,
    u.crit_sma200_slope
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
     JOIN LATERAL ( SELECT u2.watch_reason,
            u2.mcap_usd,
            u2.crit_rel_return,
            u2.crit_above_sma200,
            u2.crit_sma200_slope
           FROM universe u2
          WHERE u2.ticker = e.ticker AND u2.as_of <= e.signal_date
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON true
  WHERE e.is_cluster_head AND e.entry_kind = 'next_open'::text AND e.in_watch AND
      e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW v_ticker_state AS
SELECT i.ticker,
    t.name,
    t.sector,
    i.ts AS as_of,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.bb_pctb,
    i.bb_width,
    i.bb_width_pct,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.k_cross_up,
    i.k_cross_down,
    i.sma_200,
    i.sma200_slope_60,
    i.atr_14,
    i.rv_20d,
    i.rv_pct_252d,
    i.vol_z_20d,
    i.dd_52w,
    i.days_to_earnings,
    m.vix_close,
    m.vix_pct_252d,
    m.spx_ret_1d,
    u.in_trade,
    u.mcap_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    b.close > i.sma_200 AS above_sma200,
    u.in_watch,
    u.watch_reason
   FROM tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM indicators ind
          WHERE ind.ticker = t.ticker AND ind."interval" = '1d'::text AND (EXISTS ( SELECT 1
                   FROM bars bb
                  WHERE bb.ticker = ind.ticker AND bb.ts = ind.ts AND
                      bb."interval" = ind."interval"))
          ORDER BY ind.ts DESC
         LIMIT 1) latest
     JOIN indicators i ON i.ticker = t.ticker AND i.ts = latest.ts AND i."interval" = '1d'::text
     JOIN bars b ON b.ticker = i.ticker AND b.ts = i.ts AND b."interval" = i."interval"
     LEFT JOIN market_days m ON m.ts = i.ts::date
     LEFT JOIN LATERAL ( SELECT u2.in_trade,
            u2.mcap_usd,
            u2.crit_mcap,
            u2.crit_above_sma200,
            u2.crit_sma200_slope,
            u2.crit_rel_return,
            u2.crit_rev_growth,
            u2.in_watch,
            u2.watch_reason
           FROM universe u2
          WHERE u2.ticker = i.ticker AND u2.as_of <= i.ts::date
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON true"""
    )
    op.execute(
        """CREATE OR REPLACE VIEW v_universe AS
SELECT DISTINCT ON (u.ticker) u.ticker,
    t.name,
    t.sector,
    t.industry,
    u.as_of,
    u.in_train,
    u.in_trade,
    u.mcap_usd,
    u.mcap_rank,
    u.adv_20d_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    t.is_active,
    t.delisted_on,
    u.in_watch,
    u.watch_reason
   FROM universe u
     JOIN tickers t ON t.ticker = u.ticker
  ORDER BY u.ticker, u.as_of DESC"""
    )
    op.execute("DELETE FROM universe WHERE config_hash <> 'unknown'")
    op.execute("DROP INDEX IF EXISTS universe_config_ticker_asof_idx")
    op.execute("ALTER TABLE universe DROP CONSTRAINT universe_pkey")
    op.execute("ALTER TABLE universe ADD CONSTRAINT universe_pkey PRIMARY KEY (ticker, as_of)")
    op.execute("ALTER TABLE universe DROP COLUMN config_hash")
