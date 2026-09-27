"""`v_forward` carries each probability's companions (2026-09-27).

It sat in `test_serving_view_contract.py::KNOWN_GAPS` from Phase 5 until
migration `b6d1e8f30a27`. That contract test needs a database; this one
reads the migration, so the fast tier also fails if a later rebuild of the
view drops a companion.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from capitalscan.handlers.types import COMPANION_FIELDS

VERSIONS = Path(__file__).resolve().parents[3] / "db" / "migrations" / "versions"


def _ddl() -> str:
    path = VERSIONS / "b6d1e8f30a27_v_forward_companions.py"
    spec = importlib.util.spec_from_file_location("b6d1e8f30a27", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.V_FORWARD_NEW)


def test_every_companion_is_projected():
    select = _ddl().split("FROM predictions p", 1)[0]
    for name in COMPANION_FIELDS:
        assert name in select, name


def test_the_interval_and_n_eff_come_from_the_prediction_itself():
    """ADR 174: the calibration bucket's Wilson interval and Kish n_eff,
    the same pair `handlers.predict` returns."""
    ddl = _ddl()
    assert "p.calib_n_eff AS n_eff" in ddl
    assert "p.ci_low" in ddl and "p.ci_high" in ddl


def test_q_value_is_null_not_invented():
    """One calibrated probability is not a hypothesis test; the handler
    returns `q_value=None` for the same reason."""
    assert "NULL::numeric AS q_value" in _ddl()
