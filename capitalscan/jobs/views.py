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
