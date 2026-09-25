"""v_screen: drop the prediction columns that could never fill; add the touch twin's, named for it

**The defect, measured 2026-09-25 on `wivie`:** 73,123 `v_screen` rows, 0
with a prediction. The view selects `entry_kind = 'next_open'` and joined
`predictions` on `p.entry_kind = e.entry_kind`, while ADR 177 scores `touch`
events only. Eleven columns (`p_touch_*`, `p_adverse_3`, `q50`,
`model_version`, `pred_ci_*`, `pred_n_eff`, `calibration_json`) were NULL on
every row and always would be. No handler or page read them.

**The owner's call (DECISIONS.md Open items, B + C):** keep the `next_open`
grain -- the one feed where every row carries a measured outcome -- drop the
dead columns, and add the prediction of the **same signal's `touch` event**
under names that say so: `touch_p_touch_3`, `touch_ci_low`, ... A plain
`p_touch_3` beside this row's outcome columns would claim to predict a
`next_open` result, and ADR 177 measured the two base rates apart (0.548
touch, 0.516 next open), so the model would look ~3 points biased against
its own row. The prefix keeps that comparison from being made by accident.

**The twin is two equi-joins, not a lateral.** The touch event sharing
this row's natural key (unique on `events`), then its prediction by
`event_id` (unique, `predictions_event_id`), so each row gains at most one
match. A first draft mirrored `v_screen_live`'s `LIMIT 1` lateral with a
natural-key fallback; on the workstation copy it made the undated feed
~0.9 s slower (1.6 -> 2.5 s) and matched exactly the same 564 rows. The
plain join ran 1.15 s, faster than the old view, with 0 differing values.
The fallback exists in `v_screen_live` for serving-born predictions whose
`event_id` is unresolved (ADR 191); `v_screen` is read on research, where
`predict` writes `event_id` itself. Stochastic-only signals have no touch
event (no band level to fill at), so their `touch_*` columns stay NULL,
correctly.

**`q50` is not carried over.** ADR 172: the directional midpoint is negative
out of sample and displayed nowhere.

**`DROP VIEW`, not `CREATE OR REPLACE`.** Postgres lets a replaced view add
columns only at the end and never remove or rename one. Nothing depends on
`v_screen` (checked in `pg_depend` on `wivie`). The drop also drops its
grants; `capscan_ro` gets SELECT back from default privileges
(`jobs/roles.py`) and from the guarded grant below, for a store where the
default privileges were set by another role.

Revision ID: f4a9c2e71b58
Revises: e6b3d9a1f472
Create Date: 2026-09-25
"""

# ruff: noqa: E501 -- `pg_get_viewdef` output captured verbatim.

from alembic import op

revision = "f4a9c2e71b58"
down_revision = "e6b3d9a1f472"
branch_labels = None
depends_on = None


V_SCREEN_NEW = """SELECT e.ticker,
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
    tp.p_touch_2 AS touch_p_touch_2,
    tp.p_touch_3 AS touch_p_touch_3,
    tp.p_touch_5 AS touch_p_touch_5,
    tp.p_touch_10 AS touch_p_touch_10,
    tp.p_adverse_3 AS touch_p_adverse_3,
    tp.ci_low AS touch_ci_low,
    tp.ci_high AS touch_ci_high,
    tp.calib_n_eff AS touch_n_eff,
    tp.model_version AS touch_model_version,
    tp.calibration_json AS touch_calibration_json
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN events te ON te.config_hash = e.config_hash AND te.ticker = e.ticker AND te.signal_date = e.signal_date AND te.signal_type = e.signal_type AND te.entry_kind = 'touch'::text
     LEFT JOIN predictions tp ON tp.event_id = te.id AND tp.model_scored AND tp.config_hash = current_setting('capitalscan.default_config_hash'::text, true)
  WHERE e.is_cluster_head AND e.entry_kind = 'next_open'::text AND e.in_trade AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

# Verbatim `pg_get_viewdef('v_screen', true)` from `wivie`, 2026-09-25.
V_SCREEN_OLD = """SELECT e.ticker,
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
    p.model_version,
    p.p_touch_2,
    p.p_touch_10,
    p.ci_low AS pred_ci_low,
    p.ci_high AS pred_ci_high,
    p.calib_n_eff AS pred_n_eff,
    p.calibration_json
   FROM events e
     JOIN tickers t ON t.ticker = e.ticker
     LEFT JOIN cell_stats c ON c.signal_type = e.signal_type AND c.side = e.side AND c.dd_bucket = e.dd_bucket AND c.signal_strength IS NULL AND c.entry_kind = 'next_open'::text AND c.split_key = 'validate'::text AND c.era IS NULL AND c.horizon_days = 5 AND c.target_pct = 0.03 AND c.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND c.arm = 'signal'::text
     LEFT JOIN predictions p ON p.config_hash = e.config_hash AND p.ticker = e.ticker AND p.as_of = e.signal_date AND p.signal_type = e.signal_type AND p.entry_kind = e.entry_kind AND p.config_hash = current_setting('capitalscan.default_config_hash'::text, true) AND p.model_scored
  WHERE e.is_cluster_head AND e.entry_kind = 'next_open'::text AND e.in_trade AND e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)"""

_GRANT_RO = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'capscan_ro') THEN
        GRANT SELECT ON public.v_screen TO capscan_ro;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("DROP VIEW public.v_screen")
    op.execute(f"CREATE VIEW public.v_screen AS {V_SCREEN_NEW}")
    op.execute(_GRANT_RO)


def downgrade() -> None:
    op.execute("DROP VIEW public.v_screen")
    op.execute(f"CREATE VIEW public.v_screen AS {V_SCREEN_OLD}")
    op.execute(_GRANT_RO)
