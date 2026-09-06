"""predictions carry an interval for every field, not just the headline

ADR 174 gave a prediction row one `ci_low`/`ci_high`/`calib_n_eff`, drawn
from the `p_touch_3` reliability bucket. That was enough while `p_touch_3`
was the only field on screen.

ADR 175 adds `p_adverse_3` and `p_adverse_5`, and invariant 8 says every
**response carrying a probability** carries `n_eff` and a confidence
interval. With one interval per row, surfacing a second probability would
either ship it bare or -- worse -- ship it beside the *headline's* interval,
which is an interval for a different quantity computed over a different
reliability table. Both violate the invariant; the second does so while
looking correct.

So the row carries a JSON object keyed by field:

    {"p_touch_3": {"p": 0.62, "raw": 0.65, "lo": 0.58, "hi": 0.66,
                   "n_eff": 812.4, "bucket": 7}, ...}

**JSON rather than six more column triples.** The set of published fields
is a property of `research.predict.TARGETS`, which is code and changes with
the model; the schema should not need a migration each time a threshold is
added. The scalar `p_touch_*`/`p_adverse_*` columns stay exactly as they
are, because `v_screen` selects them by name and the wire contract in
`handlers.types.Prediction` is built on them -- this adds the evidence
beside them rather than replacing anything.

The existing `ci_low`/`ci_high`/`calib_n_eff`/`calib_bucket` columns also
stay, still describing the headline. They are what `handlers.predict` fills
`Prediction.n_eff` and the interval from, and a caller asking for one
prediction gets the headline's evidence without parsing JSON.

Revision ID: a9d3e05f7b21
Revises: f2a71d6e8c34
Create Date: 2026-09-05
"""

from alembic import op

revision = "a9d3e05f7b21"
down_revision = "f2a71d6e8c34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS calibration_json jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS calibration_json")
