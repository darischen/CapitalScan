"""`v_screen` names the touch twin's prediction for what it is (2026-09-25).

The view is the `next_open` grain: every outcome column on a row is measured
from the next session's open. ADR 177 scores `touch` events only, so the
view's old prediction join matched nothing -- 73,123 rows, 0 predictions on
`wivie`. Migration `f4a9c2e71b58` drops those columns and adds the same
signal's `touch` prediction under a `touch_` prefix.

The prefix is the point. A bare `p_touch_3` beside a `next_open` outcome
claims to predict that outcome, and the two base rates differ (0.548 touch,
0.516 next open, ADR 177), so the model would look biased against its own
row. These tests fail if a later rebuild drops the prefix.

Reads the migration module, so no database is needed.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[3] / "db" / "migrations" / "versions"


def _migration():
    path = VERSIONS / "f4a9c2e71b58_v_screen_touch_twin_prediction.py"
    spec = importlib.util.spec_from_file_location("f4a9c2e71b58", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _output_columns(ddl: str) -> list[str]:
    select = ddl.split("\n   FROM events e", 1)[0]
    names = []
    for line in select.splitlines():
        line = line.strip().rstrip(",")
        found = re.search(r"\bAS (\w+)$", line) or re.fullmatch(r"\w+\.(\w+)", line)
        if found:
            names.append(found.group(1))
    return names


def test_every_prediction_column_carries_the_touch_prefix():
    columns = _output_columns(_migration().V_SCREEN_NEW)
    model = [c for c in columns if c.startswith(("p_touch", "p_adverse", "touch_"))]
    assert model, "the view exposes no prediction; this test would be vacuous"
    assert all(c.startswith("touch_") for c in model), model


def test_the_touch_prediction_carries_its_companions():
    """Invariant 8: a probability travels with its interval and `n_eff`."""
    columns = _output_columns(_migration().V_SCREEN_NEW)
    for name in ("touch_ci_low", "touch_ci_high", "touch_n_eff", "touch_model_version"):
        assert name in columns


def test_no_directional_midpoint_is_exposed():
    """ADR 172: `q50` is negative out of sample and displayed nowhere."""
    columns = _output_columns(_migration().V_SCREEN_NEW)
    assert not any("q50" in c for c in columns)


def test_the_twin_is_the_touch_event_of_the_same_signal():
    ddl = _migration().V_SCREEN_NEW
    assert "te.entry_kind = 'touch'::text" in ddl
    for col in ("config_hash", "ticker", "signal_date", "signal_type"):
        assert f"te.{col} = e.{col}" in ddl
    assert "tp.event_id = te.id" in ddl


def test_the_row_grain_is_unchanged():
    ddl = _migration().V_SCREEN_NEW
    assert "e.entry_kind = 'next_open'::text" in ddl.split("WHERE e.is_cluster_head", 1)[1]
    assert "c.entry_kind = 'next_open'::text" in ddl


def test_the_downgrade_restores_the_old_columns():
    columns = _output_columns(_migration().V_SCREEN_OLD)
    assert "p_touch_3" in columns and "pred_n_eff" in columns
