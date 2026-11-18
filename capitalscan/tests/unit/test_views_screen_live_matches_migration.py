"""`views.V_SCREEN_LIVE_DDL` is the view the database runs (2026-11-18).

The constant drifted for weeks: it still resolved `fired_at` through
`signal_reports.event_id`, which ADR 150's sweep nulls, long after
`d5e91a7c3b48`, `b7f3c5d21a94` and `e4b7a2c9d160` moved the live view onto
`(ticker, ET date, signal_type)`. A reader who took it for the definition
got the wrong one.

The live definition is `V_SCREEN_LIVE_NEW` in `e4b7a2c9d160`. A migration
that replaces `v_screen_live` should update the constant and repoint
`LIVE_MIGRATION` here.

Reads the migration module, so no database is needed.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from capitalscan.jobs import views

VERSIONS = Path(__file__).resolve().parents[3] / "db" / "migrations" / "versions"
LIVE_MIGRATION = "e4b7a2c9d160_screen_live_reversal_matches_signal_type.py"
PREFIX = "CREATE VIEW public.v_screen_live AS"


def _live_definition() -> str:
    path = VERSIONS / LIVE_MIGRATION
    spec = importlib.util.spec_from_file_location("e4b7a2c9d160", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: str = module.V_SCREEN_LIVE_NEW
    return body


def _squash(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_the_constant_is_the_live_view_modulo_whitespace() -> None:
    ddl = views.V_SCREEN_LIVE_DDL.strip()
    assert ddl.startswith(PREFIX)
    assert _squash(ddl[len(PREFIX) :]) == _squash(_live_definition()), (
        f"V_SCREEN_LIVE_DDL differs from V_SCREEN_LIVE_NEW in {LIVE_MIGRATION}"
    )


def test_no_report_lateral_matches_on_event_id() -> None:
    """ADR 150's sweep nulls `signal_reports.event_id`, so a lateral keyed on
    it loses every intraday fire after the nightly (`d5e91a7c3b48`)."""
    assert "r.event_id" not in views.V_SCREEN_LIVE_DDL
    assert "r2.event_id" not in views.V_SCREEN_LIVE_DDL


def test_every_report_lateral_prefers_signal_type() -> None:
    for alias in ("r", "r2", "r3"):
        predicate = f"({alias}.signal_type IS NULL OR {alias}.signal_type = e.signal_type)"
        assert predicate in _squash(views.V_SCREEN_LIVE_DDL), alias
