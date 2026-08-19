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
# **ADR 122 adds `e.in_trade` to both screener views.**
#
# Detection now records trade-universe membership rather than filtering on
# it, so `events` holds roughly 4.4x the rows it used to and a screener
# reading it unfiltered would list names outside the trade universe.
#
# Both consumers already join `v_universe` and filter there, so this is
# defence in depth — but `v_universe` carries *current* membership while
# the column carries membership **as of the bar**, which is the correct
# question for a historical row. A name that left the universe in 2019
# should keep its 2015 rows on the screener's own terms.
#
# `v_chart` and `v_events` deliberately do **not** take this predicate:
# they are what `/ticker/[sym]` reads, and showing every firing on a name
# you do not trade is the entire point of ADR 122.
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
     AND e.in_trade
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
# - **`live_price` is `bars_live.close` for the current session**, with its
#   timestamp beside it. It is the last price the poller saw, not a
#   real-time quote, and `live_price_ts` is what stops those being confused.
#
#   **It read `quotes_live` until 2026-08-19 and was usually wrong.**
#   `poll.py` writes that table inside its breach loop, so a ticker gets a
#   row only on a tick where it fired -- ROST fired at 09:36 ET, never
#   again, and hours after the close this column still read its 238.72
#   against a true 234.63. The lateral had no date bound either, so a
#   ticker that last fired in July reported a July price as live.
#
#   `bars_live` is rewritten every tick for every ticker the poller covers
#   (ADR 128), so `close` is the current price by construction. An equality
#   join on its primary key replaces the lateral. Null outside a session
#   is the honest answer and is new: the old lateral always found *a* row.
# - **`fired_at` is the first `signal_reports` row for the event**, which is
#   when the poller detected and notified it. `min()` because a re-notified
#   event would otherwise report its latest mention rather than its first.
#
# All four are LATERAL-with-LIMIT or an equality join on a primary key, and
# the `indicators_daily_latest` index (ADR 116) serves the band lookup.
# **ADR 123 adds the poller's intraday reversal judgement.**
#
# Two different things carry the word "reversal" and they answer different
# questions at different times:
#
# - `bear_close_above_upper` is close-confirmed (ADR 108/109). It lands in
#   `signal_types_all` and only exists after that night's `cscan events`.
# - `poll.py::reversal_state` is the live analogue (ADR 117): price above
#   the band but below today's open. It exists only while the poller runs
#   and only in `signal_reports.state_json`.
#
# The screener joined `signal_reports` for `min(fired_at)` and never read
# `state_json`, so during a session the poller's terminal knew a confluence
# was sitting below its open and the home page could not say so. The badge
# could only appear the next morning.
#
# **The newest report, not the first.** `fired_at` deliberately takes
# `min()` -- when the signal was first detected -- while the reversal takes
# the latest row, because it is a statement about where price is *now* and
# the earliest quote of the session is the least useful one. Two laterals
# rather than one for exactly that reason.
#
# `state_json ? 'bear_reversal'` skips rows written before ADR 117 merged
# (2026-08-18 11:18 PT); every report older than that lacks the key, and a
# missing key would otherwise cast to NULL and read as "not a reversal"
# rather than as "not recorded".
# **ADR 124 moves the cluster-head filter out of this view.**
#
# It read `is_cluster_head IS NOT FALSE`, which was correct intraday and
# wrong the next morning. The poller cannot cluster (ADR 054's gap window
# needs the whole session) so its rows carry NULL and all of them passed;
# overnight `cscan events` clustered them and the repeats became `false`,
# so **rows visibly disappeared from a date between the session and the
# next morning.**
#
# Measured on Thursday 2026-08-06: 19 confluence fires, 4 heads and 15
# repeats. A reader watching live saw 19 and came back to 4, with nothing
# on the page accounting for the other 15.
#
# `is_cluster_head` is still projected, so the caller decides. The screener
# defaults to heads and offers every fire, which is the same shape the
# ticker page's history already uses.
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
    e.bb_pctb,
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
    lq.close AS live_price,
    lq.ts AS live_price_ts,
    fr.fired_at,
    rev.confirmed AS rev_confirmed,
    rev.above_band AS rev_above_band,
    rev.open_gap_atr AS rev_open_gap_atr,
    rev.rev_ts,
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
     LEFT JOIN public.bars_live lq ON ((lq.ticker = e.ticker)
         AND (lq.session_date = market_date()))
     LEFT JOIN LATERAL ( SELECT min(r.fired_at) AS fired_at
           FROM public.signal_reports r
          WHERE (r.event_id = e.id)) fr ON (true)
     LEFT JOIN LATERAL ( SELECT
              (r2.state_json -> 'bear_reversal' ->> 'confirmed')::boolean AS confirmed,
              (r2.state_json -> 'bear_reversal' ->> 'above_band')::boolean AS above_band,
              (r2.state_json -> 'bear_reversal' ->> 'open_gap_atr')::numeric AS open_gap_atr,
              r2.fired_at AS rev_ts
           FROM public.signal_reports r2
          WHERE ((r2.event_id = e.id)
              AND (r2.state_json ? 'bear_reversal'))
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON (true)
{_CELL_JOIN}
  WHERE ((e.entry_kind = 'touch'::text)
     AND e.in_trade
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


# ---------------------------------------------------------------------------
# v_stats (ADR 126)
# ---------------------------------------------------------------------------
#
# **The view predates `cell_stats.arm` and never gained it.** ADR 105 added
# the column to separate a measured arm from a recommended one; `v_stats`
# was written before that and DESIGN §8.2's DDL still omits it.
#
# So the serving surface for statistics could not express the one predicate
# that keeps the control and benchmark arms out of a rate. `v_screen` and
# `v_screen_live` join `cell_stats` directly and pin `arm = 'signal'`
# themselves, which hid it: every path that mattered had its own copy of
# the filter, and the view nobody had queried yet did not.
#
# Found when `/` first ran with `?stats=1` against the live database --
# `web/lib/screen.ts` filters `arm = 'signal'` on `v_stats` and every
# request returned `column "arm" does not exist`. The toggle had been
# broken since Session 17 shipped, because the statistics panel was only
# ever tested against a fixture.
V_STATS_DDL = """CREATE VIEW public.v_stats AS
SELECT cell_id,
    run_id,
    config_hash,
    signal_type,
    dd_bucket,
    signal_strength,
    side,
    entry_kind,
    arm,
    split_key,
    era,
    horizon_days,
    target_pct,
    n_events,
    n_eff,
    n_tickers,
    mean_cofire,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE p_hit
        END AS p_hit,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE baseline_empirical
        END AS baseline,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE edge
        END AS edge,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_low
        END AS ci_low,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_high
        END AS ci_high,
    q_value,
    p_value_randomization,
    mean_ret,
    median_ret,
    ret_p25,
    ret_p75,
    mean_mfe,
    mean_mae,
    median_time_to_mfe,
    capture_ratio,
    p_touch_2pct,
    p_touch_3pct,
    p_touch_5pct,
    p_touch_10pct,
    median_day_touch_5pct,
    exit_mix,
    earnings_frac,
    suppressed,
    suppress_reason
   FROM cell_stats
"""

# `v_stats` as it stood before ADR 126, for `downgrade()`.
V_STATS_DDL_PRE_126 = """CREATE VIEW public.v_stats AS
SELECT cell_id,
    run_id,
    config_hash,
    signal_type,
    dd_bucket,
    signal_strength,
    side,
    entry_kind,
    split_key,
    era,
    horizon_days,
    target_pct,
    n_events,
    n_eff,
    n_tickers,
    mean_cofire,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE p_hit
        END AS p_hit,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE baseline_empirical
        END AS baseline,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE edge
        END AS edge,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_low
        END AS ci_low,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_high
        END AS ci_high,
    q_value,
    p_value_randomization,
    mean_ret,
    median_ret,
    ret_p25,
    ret_p75,
    mean_mfe,
    mean_mae,
    median_time_to_mfe,
    capture_ratio,
    p_touch_2pct,
    p_touch_3pct,
    p_touch_5pct,
    p_touch_10pct,
    median_day_touch_5pct,
    exit_mix,
    earnings_frac,
    suppressed,
    suppress_reason
   FROM cell_stats
"""


# ---------------------------------------------------------------------------
# bars_live (ADR 128)
# ---------------------------------------------------------------------------
#
# Today's partial candle, from the quote the poller already fetches.
#
# **A separate table, and that is the whole design.** The obvious place is
# `bars` with a partial row, and it would be a silent look-ahead bug:
# `cscan indicators` computes on whatever is in `bars`, so a partial row
# gets an indicator row, and the poller's t-1 lookup then reads *today's*
# unfinished indicators instead of yesterday's closed ones. Every band
# would tighten around a price that had not finished happening, and
# nothing would raise.
#
# Invariant 3 stays structural this way. No consumer needs a new predicate
# -- which is the alternative, and it is ADR 122's problem shape: a flag on
# `bars` would need eighteen readers to carry it and nothing would fail
# loudly when one forgot.
#
# **One row per ticker per session, upserted.** Yahoo's
# `regularMarketDayHigh`/`Low` are cumulative session extremes rather than
# interval values, so each tick overwrites with a strictly better answer.
# There is no accumulation to get wrong and no missed tick to reconstruct:
# a poller that was down for an hour still writes the correct session high
# on its next quote. ~141 rows a day rather than ~11,000.
#
# `close` is `NOT NULL` because it is the current price and a row without
# one describes nothing. Everything else is nullable: pre-market a quote
# can carry a price and no high, and invariant 4 says absent stays absent.

# `v_ticker_events` -- the ticker page's event history, one row per signal
# with the outcome that was actually measured.
#
# **The page showed a blank entry and exit on 47% of its rows, forever.**
# Reported 2026-08-19: old events with the horizon long past still read
# `-- -- --`. Measured across in-trade cluster heads at the `touch` grain:
#
#     stoch_overbought  11,780 rows  11,780 with no entry
#     stoch_oversold     7,438 rows   7,438 with no entry
#     bb_upper_touch     8,876 rows       0 with no entry
#
# Not a data fault. A `touch` entry means *enter at the level you touched*,
# and a stochastic threshold crossing has no level -- `touch_level` is NULL
# on every one of those rows by construction, so no entry price exists and
# no exit can follow. The same signals carry complete outcomes at
# `next_open`, `touch_5m` and `touch_30m`, where entry does not need a
# band.
#
# So the history read a grain that is undefined for two of the seven signal
# types, and rendered the undefined half as an em-dash indistinguishable
# from missing data.
#
# **This view reads `next_open` -- the grain every Phase 4 statistic used
# (`GRID_ENTRY_KIND`) -- and adds back the `touch` rows that have no
# `next_open` sibling yet.** Those are the poller's fires from sessions
# `cscan backtest` has not reached, 246 of 41,065 when measured. Reading
# `next_open` alone would drop today's fires off the page, which is the row
# a reader most wants; reading `touch` alone is the defect above.
#
# **Three outcome states, not two, and the first cut of this view got that
# wrong.** `pending` initially meant "no `next_open` sibling", which counted
# 47,310 rows where it should have counted 246. The other 179,286 are
# `in_trade = false`: events ADR 122 records so they stay visible on the
# ticker page, and that the backtest deliberately never measures. Calling
# those "not yet backtested" promises a number that is never coming.
#
# So `pending` is `in_trade AND no sibling` -- a session `cscan backtest`
# has not reached -- and `in_trade` is projected beside it, letting the page
# separate "measured", "waiting on the backtest", and "outside the trade
# universe, never measured by design". Caught by checking the counts against
# the estimate rather than by any test.
#
# **Marker alignment survives.** `v_chart` marks bars at `entry_kind =
# 'touch' AND is_cluster_head IS NOT FALSE`, and every historical touch row
# has a `next_open` sibling on the same `(ticker, signal_date,
# signal_type)` -- that four-column tuple plus `entry_kind` is the natural
# key, so the correspondence is exact rather than approximate. A marker
# without a history row underneath it remains impossible.
V_TICKER_EVENTS_DDL = """
CREATE VIEW public.v_ticker_events AS
 SELECT
    e.ticker,
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
    false AS pending
   FROM public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker))
  WHERE ((e.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
     AND (e.entry_kind = 'next_open'::text))
UNION ALL
 SELECT
    e.ticker,
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
    e.in_trade AS pending
   FROM public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker))
  WHERE ((e.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
     AND (e.entry_kind = 'touch'::text)
     AND (NOT (EXISTS ( SELECT 1
           FROM public.events n
          WHERE ((n.config_hash = e.config_hash) AND (n.ticker = e.ticker)
              AND (n.signal_date = e.signal_date) AND (n.signal_type = e.signal_type)
              AND (n.entry_kind = 'next_open'::text))))))
"""

BARS_LIVE_DDL = """CREATE TABLE IF NOT EXISTS public.bars_live (
    ticker        text        NOT NULL REFERENCES public.tickers(ticker),
    session_date  date        NOT NULL,
    ts            timestamptz NOT NULL,
    open          numeric(12,4),
    high          numeric(12,4),
    low           numeric(12,4),
    close         numeric(12,4) NOT NULL,
    volume        bigint,
    run_id        text,
    PRIMARY KEY (ticker, session_date)
)
"""
