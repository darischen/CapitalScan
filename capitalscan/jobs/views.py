"""`v_positions`' DDL and the settings row it reads (ADR 095, ADR 115).

ADR 095 found a second exit implementation living in SQL: `v_positions`
compared `k_full` against a bare `80` and `CURRENT_DATE - entry_date`
against a bare `5`. Those are `ExitParams.exit_stoch_threshold` and
`ExitParams.max_hold_days` written as constants, which ADR 092 bans and
invariant 9 bans.

The offending text is quoted exactly once in this file, in
`V_POSITIONS_DDL_PRE_115` below, where `downgrade()` needs it verbatim.
`jobs/threshold_lint.py` matches the pattern rather than the spelling, so
every additional copy is another finding to allowlist for no gain. Sweep
`exit_stoch_threshold` to 70 and the backtest moves while the position page
still reports exits at 80, with every number on both sides looking
reasonable.

**ADR 095 offered two fixes and this module implements the second one, not
the first.** ADR 095 preferred generating the view's DDL from `ExitParams`
inside the migration. That was written before `jobs/threshold_lint.py`
existed, and the linter changes the arithmetic: a generated DDL still bakes
`80` into the *database*, `pg_dump` still writes `(80)::numeric` into
`db/schema.sql`, and the `KNOWN_EXCEPTIONS` entry that exempts it would have
to stay forever. The defect class would remain allowlisted in the one file
most likely to grow the next instance.

A settings row leaves no literal in any checked-in SQL, so the exemption is
deleted rather than renewed, and the linter starts meaning what it says.
It also lets a threshold sweep move the serving surface without a migration.
ADR 115 records the reversal and its evidence.

**The cost, stated plainly.** The view now depends on a row existing. It is
joined `LEFT ... ON true`, so a missing row yields NULL exit flags rather
than zero rows: a position still renders and its exit signals read as
"unknown", which is the honest failure. `cscan db sync-config` writes the
row, the migration seeds it, and `test_serving_config_matches_live_config`
fails if config moves and the row does not.
"""

from __future__ import annotations

from typing import Any

from capitalscan.core.config import Config, ExitParams

# One row, enforced by a primary key that can only hold one value. A
# settings table that can hold two rows is a settings table that eventually
# does, and the view would then multiply every position by the row count.
SERVING_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS public.serving_config (
    only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row),
    config_hash text NOT NULL,
    exit_stoch_source text NOT NULL,
    exit_stoch_threshold numeric(12,4) NOT NULL,
    exit_stoch_threshold_short numeric(12,4) NOT NULL,
    exit_on_stoch_80 boolean NOT NULL,
    exit_on_upper_band boolean NOT NULL,
    exit_on_mid_band boolean NOT NULL,
    max_hold_days integer NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT serving_config_stoch_source_check
        CHECK (exit_stoch_source = ANY (ARRAY['k_full'::text, 'k_fast'::text]))
)
"""

SERVING_CONFIG_COLUMNS: tuple[str, ...] = (
    "config_hash",
    "exit_stoch_source",
    "exit_stoch_threshold",
    "exit_stoch_threshold_short",
    "exit_on_stoch_80",
    "exit_on_upper_band",
    "exit_on_mid_band",
    "max_hold_days",
)

SERVING_CONFIG_UPSERT = """
INSERT INTO public.serving_config (
    only_row, config_hash, exit_stoch_source,
    exit_stoch_threshold, exit_stoch_threshold_short,
    exit_on_stoch_80, exit_on_upper_band, exit_on_mid_band,
    max_hold_days, updated_at
) VALUES (
    true, :config_hash, :exit_stoch_source,
    :exit_stoch_threshold, :exit_stoch_threshold_short,
    :exit_on_stoch_80, :exit_on_upper_band, :exit_on_mid_band,
    :max_hold_days, now()
)
ON CONFLICT (only_row) DO UPDATE SET
    config_hash = EXCLUDED.config_hash,
    exit_stoch_source = EXCLUDED.exit_stoch_source,
    exit_stoch_threshold = EXCLUDED.exit_stoch_threshold,
    exit_stoch_threshold_short = EXCLUDED.exit_stoch_threshold_short,
    exit_on_stoch_80 = EXCLUDED.exit_on_stoch_80,
    exit_on_upper_band = EXCLUDED.exit_on_upper_band,
    exit_on_mid_band = EXCLUDED.exit_on_mid_band,
    max_hold_days = EXCLUDED.max_hold_days,
    updated_at = now()
"""


def serving_config_values(config: Config | None = None, config_hash: str = "") -> dict[str, Any]:
    """The row, built from `ExitParams`. The only place these are copied.

    `config_hash` is passed in rather than computed here because computing
    it means importing `jobs.config`, and this module is imported by an
    Alembic migration where a heavier import graph is a liability.
    """
    ep: ExitParams = (config or Config()).exits
    return {
        "config_hash": config_hash,
        "exit_stoch_source": ep.exit_stoch_source,
        "exit_stoch_threshold": ep.exit_stoch_threshold,
        "exit_stoch_threshold_short": ep.exit_stoch_threshold_short,
        "exit_on_stoch_80": ep.exit_on_stoch_80,
        "exit_on_upper_band": ep.exit_on_upper_band,
        "exit_on_mid_band": ep.exit_on_mid_band,
        "max_hold_days": ep.max_hold_days,
    }


# The rebuilt view. Four corrections to the original, each one a defect ADR
# 095 named:
#
# 1. **Thresholds come from `serving_config`**, not from literals.
# 2. **The stochastic exit respects `p.side`.** The original applied
#    `k_full >= 80` to every row, so an open short read its exit off the
#    long threshold and was wrong in the direction that *suppresses* the
#    exit. `exit_stoch_threshold_short` exists precisely so the two sides
#    stay independent (ADR 016: the short is not a mirror of the long).
# 3. **The %K column follows `exit_stoch_source`.** ADR 110 moved the entry
#    trigger to `k_fast`; whichever column the exit policy reads, the view
#    must read the same one or the position page and the backtest disagree
#    about what "overbought" means.
# 4. **`exit_signal_mid_band` is gated on `exit_on_mid_band`**, which
#    defaults False (ADR 046). The original published a signal the policy
#    had switched off, with nothing saying it was inactive. Gated to NULL
#    rather than to false: "this rule is not in force" and "this rule is in
#    force and has not fired" are different facts.
#
# And one correction ADR 095 did not name. `days_held` was
# `CURRENT_DATE - p.entry_date`, calendar days, while `ExitParams.
# max_hold_days` counts *bars* -- `core/exits.py` walks `fwd_bars` and
# `holding_days` equals `exit_idx + 1`. A position opened on a Thursday
# reads 4 calendar days on the following Monday and 2 sessions, so a
# 5-session timeout fired a day early over every weekend. Counting
# `trading_days` makes the view agree with the backtest.
#
# `exit_stoch_k` is exposed so the comparison is auditable from the row: a
# reader can see which %K the flag was computed from without knowing what
# `exit_stoch_source` currently says. It is **appended last** rather than
# placed beside the flag it explains, because every other column keeps its
# original ordinal and a consumer reading positionally does not break.
#
# `CREATE VIEW`, not `CREATE OR REPLACE`. Replace can only append columns
# and cannot change a column's type, and `days_held` moves from `integer`
# (a date subtraction) to `bigint` (a count). The migration drops first.
V_POSITIONS_DDL = """
CREATE VIEW public.v_positions AS
 SELECT p.id,
    p.user_id,
    p.ticker,
    p.side,
    p.entry_date,
    p.entry_price,
    p.quantity,
    p.source,
    p.status,
    p.exit_date,
    p.exit_price,
    p.exit_reason,
    p.realized_ret,
    p.idempotency_key,
    p.created_at,
    s.close AS current_price,
        CASE
            WHEN (p.status = 'open'::text) THEN ((s.close - p.entry_price) / p.entry_price)
            ELSE p.realized_ret
        END AS unrealized_or_realized_ret,
    ( SELECT count(*) AS count
        FROM public.trading_days td
       WHERE ((td.d > p.entry_date) AND (td.d <= CURRENT_DATE))) AS days_held,
        CASE
            WHEN (NOT c.exit_on_stoch_80) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN ((CASE
                    WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                    ELSE s.k_full
                END) <= c.exit_stoch_threshold_short)
            ELSE ((CASE
                    WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                    ELSE s.k_full
                END) >= c.exit_stoch_threshold)
        END AS exit_signal_stoch,
        CASE
            WHEN (NOT c.exit_on_upper_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_lower)
            ELSE (s.close >= s.bb_upper)
        END AS exit_signal_upper_band,
        CASE
            WHEN (NOT c.exit_on_mid_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_mid)
            ELSE (s.close >= s.bb_mid)
        END AS exit_signal_mid_band,
    (( SELECT count(*) AS count
         FROM public.trading_days td
        WHERE ((td.d > p.entry_date) AND (td.d <= CURRENT_DATE)))
        >= c.max_hold_days) AS exit_signal_timeout,
        CASE
            WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
            ELSE s.k_full
        END AS exit_stoch_k
   FROM ((public.positions p
     LEFT JOIN public.v_ticker_state s ON ((s.ticker = p.ticker)))
     LEFT JOIN public.serving_config c ON (true))
"""

# What the view looked like before ADR 115, kept so `downgrade()` restores
# it exactly rather than approximately. It carries the literals on purpose:
# a downgrade that quietly fixed them would not be a downgrade.
V_POSITIONS_DDL_PRE_115 = """
CREATE VIEW public.v_positions AS
 SELECT p.id,
    p.user_id,
    p.ticker,
    p.side,
    p.entry_date,
    p.entry_price,
    p.quantity,
    p.source,
    p.status,
    p.exit_date,
    p.exit_price,
    p.exit_reason,
    p.realized_ret,
    p.idempotency_key,
    p.created_at,
    s.close AS current_price,
        CASE
            WHEN (p.status = 'open'::text) THEN ((s.close - p.entry_price) / p.entry_price)
            ELSE p.realized_ret
        END AS unrealized_or_realized_ret,
    (CURRENT_DATE - p.entry_date) AS days_held,
    (s.k_full >= (80)::numeric) AS exit_signal_stoch,
    (s.close >= s.bb_upper) AS exit_signal_upper_band,
    (s.close >= s.bb_mid) AS exit_signal_mid_band,
    ((CURRENT_DATE - p.entry_date) >= 5) AS exit_signal_timeout
   FROM (public.positions p
     LEFT JOIN public.v_ticker_state s ON ((s.ticker = p.ticker)))
"""


# ---------------------------------------------------------------------------
# v_ticker_state (ADR 116)
# ---------------------------------------------------------------------------
#
# **The shipped view joined four tables across every daily indicator row and
# applied `DISTINCT ON` last.** 2,912,426 rows in, 612 out. Measured
# 2026-08-18 with `max_parallel_workers_per_gather = 0`:
#
#     SELECT count(*) FROM v_ticker_state          23.8 s
#     SELECT * FROM v_positions WHERE id = 43      24.5 s
#     SELECT * FROM v_ticker_state WHERE ticker=?  17 ms
#
# The single-ticker read was already fast: the planner pushes a *constant*
# predicate down through the `DISTINCT ON`. It cannot push a *correlated*
# one, which is why `v_positions` - joining on `p.ticker` - paid the full
# cost to return one row.
#
# The rewrite is a **loose index scan**: drive off `tickers` (712 rows) and
# take one indicator row per ticker with `ORDER BY ts DESC LIMIT 1`.
#
#     SELECT count(*) FROM v_ticker_state          23.7 ms   (1000x)
#     SELECT * FROM v_ticker_state WHERE ticker=?  1.4 ms
#
# **The `LIMIT` is load-bearing twice.** It makes the scan stop at the first
# qualifying row per ticker instead of reading all ~4,800 of them, and it
# stops the planner from flattening the lateral: a correlated subquery with
# no volatile function and no LIMIT gets pulled up into a plain join, which
# is exactly what happened to an earlier attempt at fixing `v_positions`
# with a lateral over the unchanged view (22.7 s, identical plan).
#
# **`EXISTS (bars)` rather than a join, and it is not decoration.** The old
# view inner-joined `bars` and *then* took the latest surviving row per
# ticker, so a ticker whose latest indicator row had no matching bar fell
# through to the next one down. A rewrite that selected from `indicators`
# alone would drop that ticker entirely instead. `ORDER BY ts DESC LIMIT 1`
# under the EXISTS filter picks the same row the old view did.
#
# That fallback currently never fires - zero of 2,912,426 daily indicator
# rows lack a bar, measured 2026-08-18 - and the EXISTS is kept anyway, so
# the behaviour is preserved structurally rather than by that measurement
# continuing to hold. An intermediate version that put the join *inside* a
# `DISTINCT ON` was also correct and only 1.7x faster (13.7 s), because it
# reintroduced the 2.9M-row join and an external merge sort of 74 MB.
#
# Equivalence was measured, not argued: with the original view rebuilt
# alongside, `EXCEPT` in both directions returned zero rows over all 612.
V_TICKER_STATE_DDL = """
CREATE VIEW public.v_ticker_state AS
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
    (b.close > i.sma_200) AS above_sma200
   FROM ((((public.tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM public.indicators ind
          WHERE ((ind.ticker = t.ticker) AND (ind."interval" = '1d'::text)
              AND (EXISTS ( SELECT 1
                   FROM public.bars bb
                  WHERE ((bb.ticker = ind.ticker) AND (bb.ts = ind.ts)
                      AND (bb."interval" = ind."interval")))))
          ORDER BY ind.ts DESC
         LIMIT 1) latest)
     JOIN public.indicators i ON (((i.ticker = t.ticker) AND (i.ts = latest.ts)
         AND (i."interval" = '1d'::text))))
     JOIN public.bars b ON (((b.ticker = i.ticker) AND (b.ts = i.ts)
         AND (b."interval" = i."interval"))))
     LEFT JOIN public.market_days m ON ((m.ts = (i.ts)::date)))
     LEFT JOIN LATERAL ( SELECT u2.in_trade,
            u2.mcap_usd,
            u2.crit_mcap,
            u2.crit_above_sma200,
            u2.crit_sma200_slope,
            u2.crit_rel_return,
            u2.crit_rev_growth
           FROM public.universe u2
          WHERE ((u2.ticker = i.ticker) AND (u2.as_of <= (i.ts)::date))
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON (true)
"""

# The index the lateral reads. `(ticker, ts DESC)` matches its
# `WHERE ticker = ? ... ORDER BY ts DESC LIMIT 1` exactly, so each of the
# 712 lookups is an index seek that stops at the first qualifying row.
#
# Partial on `interval = '1d'`. `indicators` holds hourly rows the view
# never reads; a full index would be several times larger and maintained on
# every hourly write for no read it serves.
INDICATORS_DAILY_LATEST_INDEX = """
CREATE INDEX IF NOT EXISTS indicators_daily_latest
    ON public.indicators (ticker, ts DESC)
    WHERE ("interval" = '1d'::text)
"""

# The shipped version, kept verbatim so `downgrade()` restores it exactly.
# It is the slow one on purpose: a downgrade that quietly kept the
# optimisation would not be a downgrade.
V_TICKER_STATE_DDL_PRE_116 = """
CREATE VIEW public.v_ticker_state AS
 SELECT DISTINCT ON (i.ticker) i.ticker,
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
    (b.close > i.sma_200) AS above_sma200
   FROM ((((public.indicators i
     JOIN public.bars b ON (((b.ticker = i.ticker) AND (b.ts = i.ts)
         AND (b."interval" = i."interval"))))
     JOIN public.tickers t ON ((t.ticker = i.ticker)))
     LEFT JOIN public.market_days m ON ((m.ts = (i.ts)::date)))
     LEFT JOIN LATERAL ( SELECT u2.ticker,
            u2.as_of,
            u2.in_train,
            u2.in_trade,
            u2.mcap_usd,
            u2.mcap_rank,
            u2.adv_20d_usd,
            u2.crit_mcap,
            u2.crit_above_sma200,
            u2.crit_sma200_slope,
            u2.crit_rel_return,
            u2.crit_rev_growth
           FROM public.universe u2
          WHERE ((u2.ticker = i.ticker) AND (u2.as_of <= (i.ts)::date))
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON (true))
  WHERE (i."interval" = '1d'::text)
  ORDER BY i.ticker, i.ts DESC
"""


# ---------------------------------------------------------------------------
# market_date(), v_screen, and v_screen_live (ADR 119)
# ---------------------------------------------------------------------------
#
# **`CURRENT_DATE` is not the market's date.** The database runs `Etc/UTC`
# (measured 2026-08-19 02:15 UTC: `CURRENT_DATE` said 2026-08-19 while the
# session that had just closed was 2026-08-18). So every use of it to mean
# "today's session" is wrong from 00:00 UTC until midnight ET - about seven
# hours a day, 5pm to midnight Pacific, which is exactly when someone
# reviews the session that just ended.
#
# One definition, `STABLE` rather than `IMMUTABLE` because it reads the
# clock. Every consumer calls it instead of spelling the zone again.
MARKET_DATE_DDL = """
CREATE OR REPLACE FUNCTION public.market_date() RETURNS date
    LANGUAGE sql
    STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::date $$
"""

# The statistics grain. `research/cell_stats.py::GRID_ENTRY_KIND` is
# `next_open`, so every cell in the headline grid was measured on that entry
# and a live feed joining on its own `entry_kind` would find nothing.
#
# Written once and interpolated into both views, so the feed's grain and the
# statistics' grain can differ (ADR 119) without the *statistics* grain
# differing between them.
_CELL_JOIN = """
     JOIN public.tickers t ON (t.ticker = e.ticker)
     LEFT JOIN public.cell_stats c ON ((c.signal_type = e.signal_type)
         AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket)
         AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text)
         AND (c.split_key = 'validate'::text) AND (c.era IS NULL)
         AND (c.horizon_days = 5) AND (c.target_pct = 0.03)
         AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
         AND (c.arm = 'signal'::text))
     LEFT JOIN public.predictions p ON ((p.ticker = e.ticker) AND (p.as_of = e.signal_date))
"""

_STATS_PROJECTION = """
    c.cell_id,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed,
    c.suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version
"""

# `v_screen`, with the config predicate it was missing.
#
# **It showed events from 23 different `config_hash` values.** ADR 100 says
# the view carries a config predicate and it did - on the `cell_stats` join
# only, never on `events`. `v_events` filters correctly; this one did not.
#
# Measured 2026-08-18 on the newest date the screener could show: 46 rows,
# of which **17 belonged to a superseded config** and 29 to the live one,
# mixed together with nothing distinguishing them.
#
# The `entry_kind` in the cell join also moves from `e.entry_kind` to the
# pinned `'next_open'`. For this view they are the same value - it filters
# `e.entry_kind = 'next_open'` - so the join is unchanged in behaviour and
# now says *why* it is that kind.
V_SCREEN_DDL = f"""
CREATE VIEW public.v_screen AS
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
{_STATS_PROJECTION}
   FROM public.events e
{_CELL_JOIN}
  WHERE (e.is_cluster_head AND (e.entry_kind = 'next_open'::text)
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""

# The live feed: what fired, at detection time (ADR 119).
#
# DESIGN §11.1 calls `/` "today's screener". `v_screen` cannot answer that:
# it filters `entry_kind = 'next_open'`, and only `cscan backtest` writes
# that kind, so it trails the last full backtest - a five-hour job. Measured
# 2026-08-18: newest `next_open` was 2026-08-13 while 67 events had fired
# that day, all `touch`.
#
# Three differences from `v_screen`, each deliberate:
#
# **`entry_kind = 'touch'`.** The detection-time row, written by the poller
# during the session and by `cscan events` overnight. This is "what fired".
#
# **`is_cluster_head IS NOT FALSE`, not `is_cluster_head`.** The poller
# writes one row per breach and cannot cluster: ADR 054's gap window needs
# the whole day, which does not exist yet at 09:35. So its rows carry NULL,
# and `WHERE is_cluster_head` shows **zero rows intraday** - measured, 0 of
# 67 today. NULL means "not yet clustered", which is not "not a head", and
# the honest treatment is to show it and hide only the known non-heads.
#
# **The cell join pins `next_open`.** The statistics were measured on that
# entry (`GRID_ENTRY_KIND`), so the feed's grain and the statistics' grain
# legitimately differ. A feed is a detection-time question; a hit rate is a
# question about an entry that was actually simulated.
#
# **`sector` comes from `tickers`, not from `events`** (2026-08-19).
# `events.sector` is NULL on all 13,479,819 rows -- measured, not sampled --
# while `tickers.sector` carries 502 populated. The column exists on
# `events` and nothing has ever written it. `v_events` already joined
# `tickers` and got this right; these two projected the dead column, so a
# consumer could not tell "null" from "genuinely unknown".
#
# An inner join, matching `v_events`. Zero of 2,912,426 daily indicator rows
# and zero events lack a matching ticker, so it drops nothing, and an outer
# join here would only mask a referential break rather than report it.
#
# `is_cluster_head` is projected so a consumer can tell a clustered head
# from a not-yet-clustered row rather than having them look identical.
#
# **Four joins carry what `events` does not store** (user's request,
# 2026-08-19). `events` holds `bb_pctb` and `touch_level` but none of the
# three band levels, and no OHLCV at all.
#
# - **The bands come from `t-1`**, via a lateral ordered `ts DESC` under
#   `ts < signal_date`. That is invariant 3 and it is the row the signal
#   actually compared against, so the displayed band is the one that fired.
#   `band_ts` is projected so a reader can confirm which session it is.
#   `bb_mid` is the 20-day SMA as `core/indicators.py` computed it; nothing
#   is recomputed here.
# - **OHLCV is the signal date's own bar**, and is **NULL intraday**. Bars
#   are ingested nightly, so a row that fired at 09:35 has no bar until that
#   evening -- measured 2026-08-19, 0 of today's 67 events had one. Left
#   null rather than back-filled from quotes: an open that is really the
#   first tick anyone saw is not the session's open.
# - **`live_price` is the newest `quotes_live` row for the ticker**, with
#   its timestamp beside it. It is the last price the poller saw, not a
#   real-time quote, and `live_price_ts` is what stops those being confused.
# - **`fired_at` is the first `signal_reports` row for the event**, which is
#   when the poller detected and notified it. `min()` because a re-notified
#   event would otherwise report its latest mention rather than its first.
#
# All four are LATERAL-with-LIMIT or an equality join on a primary key, and
# the `indicators_daily_latest` index (ADR 116) serves the band lookup.
V_SCREEN_LIVE_DDL = f"""
CREATE VIEW public.v_screen_live AS
 SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.side,
    e.touch_level,
    e.entry_price,
    e.k_fast,
    e.k_full,
    e.d_full,
    e.k_cross_up,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.cofire_count,
    t.sector,
    ind.bb_lower,
    ind.bb_mid,
    ind.bb_upper,
    ind.ts AS band_ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
    lq.price AS live_price,
    lq.ts AS live_price_ts,
    fr.fired_at,
{_STATS_PROJECTION}
   FROM public.events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower, i2.bb_mid, i2.bb_upper, i2.ts
           FROM public.indicators i2
          WHERE ((i2.ticker = e.ticker) AND (i2."interval" = '1d'::text)
              AND (i2.ts < e.signal_date))
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON (true)
     LEFT JOIN public.bars b ON ((b.ticker = e.ticker) AND (b.ts = e.signal_date)
         AND (b."interval" = '1d'::text))
     LEFT JOIN LATERAL ( SELECT q.price, q.ts
           FROM public.quotes_live q
          WHERE (q.ticker = e.ticker)
          ORDER BY q.ts DESC
         LIMIT 1) lq ON (true)
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM public.signal_reports r
          WHERE (r.event_id = e.id)) fr ON (true)
{_CELL_JOIN}
  WHERE ((e.entry_kind = 'touch'::text) AND (e.is_cluster_head IS NOT FALSE)
     AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
"""

# `v_screen` as it stood before ADR 119, kept verbatim so `downgrade()`
# restores it exactly - including the missing config predicate, because a
# downgrade that quietly kept the fix would not be a downgrade.
V_SCREEN_DDL_PRE_119 = """
CREATE VIEW public.v_screen AS
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
    c.cell_id,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed,
    c.suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version
   FROM public.events e
     LEFT JOIN public.cell_stats c ON (((c.signal_type = e.signal_type)
         AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket)
         AND (c.signal_strength IS NULL) AND (c.entry_kind = e.entry_kind)
         AND (c.split_key = 'validate'::text) AND (c.era IS NULL)
         AND (c.horizon_days = 5) AND (c.target_pct = 0.03)
         AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
         AND (c.arm = 'signal'::text))))
     LEFT JOIN public.predictions p ON (((p.ticker = e.ticker) AND (p.as_of = e.signal_date))))
  WHERE (e.is_cluster_head AND (e.entry_kind = 'next_open'::text))
"""

# `v_positions` with `market_date()` in place of `CURRENT_DATE`. Identical
# in every other respect to the ADR 115/116 version above; only the two
# date comparisons change.
V_POSITIONS_DDL_MARKET_DATE = V_POSITIONS_DDL.replace("CURRENT_DATE", "public.market_date()")


# ---------------------------------------------------------------------------
# v_chart (ADR 120)
# ---------------------------------------------------------------------------
#
# **DESIGN §8.2 says "one row per bar". The deployed view was not.**
#
# Measured 2026-08-19 on TSM's last 400 days: **963 rows for 275 trading
# days**. The event join carried no `config_hash` predicate, so every bar
# that ever produced an event joined once per sweep config -- 22 of them --
# and a chart drawn from it would stack 22 candles and 22 markers on one
# date. `v_events` filters config and `v_screen` was fixed by ADR 119; this
# view was the third and last one missing it.
#
# Three changes, the same three ADR 119 made to `v_screen`, plus one this
# view needs on its own.
#
# **The config predicate.** The 22x duplication above.
#
# **`entry_kind = 'touch'`, not `next_open`.** Only `cscan backtest` writes
# `next_open`, so markers stopped at 2026-08-13 while events had fired
# through 2026-08-18. A chart whose newest marker is five sessions old is
# not showing what fired.
#
# **`is_cluster_head IS NOT FALSE`.** ADR 054's gap window needs the whole
# session, so the poller cannot cluster and writes NULL. `AND
# e.is_cluster_head` drops every intraday row -- 67 of them on the day this
# was measured. NULL means "not yet clustered", not "not a head".
#
# **And the one that is only a chart problem: a bar can carry two events.**
# Measured: **116 dates** hold both a long and a short head under the live
# config, ADBE 2016-06-22 being `bb_lower_touch` (long) and
# `stoch_overbought` (short) on the same bar. Both are real. No predicate
# removes them, because removing one would be dropping a signal that fired.
#
# So the marker columns become arrays and the bar stays one row. This is the
# grain the view was documented to have and the grain a chart requires: a
# series library indexes by time and silently keeps the last of a duplicate
# key, which is the failure mode where the chart looks right and shows the
# wrong marker.
#
# **`exit_date`, `exit_reason` and `net_ret` are dropped.** They are
# per-event outcomes, and carrying per-event columns on a bar grain is
# exactly the shape mismatch that produced the duplication. `v_events` has
# them at full fidelity, keyed by event, and the ticker page reads it for
# the history table under the chart.
V_CHART_DDL = """
CREATE VIEW public.v_chart AS
 SELECT b.ticker,
    b.ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.sma_200,
    i.bb_width_pct,
    i.dd_52w,
    ev.event_ids,
    ev.signal_types,
    ev.sides,
    ev.signal_strength
   FROM public.bars b
     LEFT JOIN public.indicators i ON ((i.ticker = b.ticker) AND (i.ts = b.ts)
         AND (i."interval" = b."interval"))
     LEFT JOIN LATERAL ( SELECT array_agg(e.id ORDER BY e.id) AS event_ids,
            array_agg(e.signal_type ORDER BY e.id) AS signal_types,
            array_agg(e.side ORDER BY e.id) AS sides,
            max(e.signal_strength) AS signal_strength
           FROM public.events e
          WHERE ((e.ticker = b.ticker) AND (e.signal_date = (b.ts)::date)
              AND (e.entry_kind = 'touch'::text)
              AND (e.is_cluster_head IS NOT FALSE)
              AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
         ) ev ON true
  WHERE (b."interval" = '1d'::text)
"""

# `v_chart` as 6d86bf1f668e created it, captured with `pg_get_viewdef` from
# the deployed database rather than retyped, so `downgrade()` restores what
# was actually there -- duplication included, because a downgrade that
# quietly kept the fix would not be a downgrade.
V_CHART_DDL_PRE_120 = """
CREATE VIEW public.v_chart AS
 SELECT b.ticker,
    b.ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.sma_200,
    i.bb_width_pct,
    i.dd_52w,
    e.id AS event_id,
    e.signal_type,
    e.signal_strength,
    e.exit_date,
    e.exit_reason,
    e.net_ret
   FROM ((public.bars b
     LEFT JOIN public.indicators i ON (((i.ticker = b.ticker) AND (i.ts = b.ts)
         AND (i."interval" = b."interval"))))
     LEFT JOIN public.events e ON (((e.ticker = b.ticker)
         AND (e.signal_date = (b.ts)::date) AND e.is_cluster_head
         AND (e.entry_kind = 'next_open'::text))))
  WHERE (b."interval" = '1d'::text)
"""
