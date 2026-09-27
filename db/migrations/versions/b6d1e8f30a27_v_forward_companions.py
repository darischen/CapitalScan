"""v_forward carries each probability's interval and n_eff

**Invariant 8 on the one view that still broke it.** `v_forward` exposed
`p_touch_*` and `p_adverse_*` with no interval, and sat in
`test_serving_view_contract.py::KNOWN_GAPS` since Phase 5 with the reason
"no model exists". ADR 174 shipped the model and `predictions` has carried
`ci_low`, `ci_high` and `calib_n_eff` ever since; nothing projected them
here. Closed as the last open item in DECISIONS.md (audit 2026-09-26).

**The four companions, and why `q_value` is NULL.** `n_eff` is the
calibration bucket's Kish effective sample and the interval is that
bucket's Wilson interval on realised outcomes -- the same pair
`handlers.predict` returns (ADR 174). `q_value` is Phase 4's
multiple-testing correction over a family of cell hypotheses; one
calibrated probability is not a hypothesis test, so the honest value is
NULL, exactly as the handler returns `q_value=None`.

**Appended, so `CREATE OR REPLACE` works.** Postgres lets a replaced view
add columns at the end and nothing else. `downgrade()` has to `DROP` and
recreate, since removing columns is the one thing `REPLACE` refuses.
Nothing depends on `v_forward` (checked in `pg_depend`) and no handler or
web route reads it.

Revision ID: b6d1e8f30a27
Revises: f4a9c2e71b58
Create Date: 2026-09-27
"""

# ruff: noqa: E501 -- `pg_get_viewdef` output captured verbatim.

from alembic import op

revision = "b6d1e8f30a27"
down_revision = "f4a9c2e71b58"
branch_labels = None
depends_on = None


V_FORWARD_NEW = """SELECT p.id,
    p.ticker,
    p.as_of,
    p.model_version,
    p.event_id,
    p.q05,
    p.q25,
    p.q50,
    p.q75,
    p.q95,
    p.p_touch_2,
    p.p_touch_3,
    p.p_touch_5,
    p.p_touch_10,
    p.p_adverse_3,
    p.p_adverse_5,
    p.cell_id,
    p.cell_p_hit,
    p.cell_n_eff,
    o.realized_ret_5d,
    o.realized_mfe,
    o.realized_mae,
    o.touched_2,
    o.touched_3,
    o.touched_5,
    o.touched_10,
    o.pinball_loss,
    o.brier_3pct,
    o.resolved_at,
    o.prediction_id IS NOT NULL AS resolved,
        CASE
            WHEN o.prediction_id IS NULL THEN NULL::numeric
            ELSE abs(p.p_touch_3 - o.touched_3::integer::numeric)
        END AS abs_err_3pct,
    p.calib_n_eff AS n_eff,
    p.ci_low,
    p.ci_high,
    NULL::numeric AS q_value
   FROM predictions p
     LEFT JOIN outcomes o ON o.prediction_id = p.id"""

# Verbatim `pg_get_viewdef('v_forward', true)` from `wivie`, 2026-09-27.
V_FORWARD_OLD = """SELECT p.id,
    p.ticker,
    p.as_of,
    p.model_version,
    p.event_id,
    p.q05,
    p.q25,
    p.q50,
    p.q75,
    p.q95,
    p.p_touch_2,
    p.p_touch_3,
    p.p_touch_5,
    p.p_touch_10,
    p.p_adverse_3,
    p.p_adverse_5,
    p.cell_id,
    p.cell_p_hit,
    p.cell_n_eff,
    o.realized_ret_5d,
    o.realized_mfe,
    o.realized_mae,
    o.touched_2,
    o.touched_3,
    o.touched_5,
    o.touched_10,
    o.pinball_loss,
    o.brier_3pct,
    o.resolved_at,
    o.prediction_id IS NOT NULL AS resolved,
        CASE
            WHEN o.prediction_id IS NULL THEN NULL::numeric
            ELSE abs(p.p_touch_3 - o.touched_3::integer::numeric)
        END AS abs_err_3pct
   FROM predictions p
     LEFT JOIN outcomes o ON o.prediction_id = p.id"""

_GRANT_RO = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'capscan_ro') THEN
        GRANT SELECT ON public.v_forward TO capscan_ro;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(f"CREATE OR REPLACE VIEW public.v_forward AS {V_FORWARD_NEW}")


def downgrade() -> None:
    op.execute("DROP VIEW public.v_forward")
    op.execute(f"CREATE VIEW public.v_forward AS {V_FORWARD_OLD}")
    op.execute(_GRANT_RO)
