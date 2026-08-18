"""Invariant 8 on the SQL side (ADR 118, ADR 076).

Under ADR 118 the web routes are TypeScript and select from the views, so
`handlers/validate.py` does not guard them. ADR 076's answer is that the
guarantee is structural instead: `n_eff`, `ci_low`, `ci_high`, and
`q_value` are *columns*, so returning a bare probability requires
deliberately dropping columns.

Structural is not the same as true, and nothing checked it. **This module is
what makes ADR 076's claim a fact rather than an intention**, and ADR 118
requires it before any route work.

**One definition of "probability", two enforcement points.** The predicate
is `handlers.types.is_probability_field`, the same function that governs
the Python result types. A column named `p_hit` on a view and a field named
`p_hit` on a dataclass are the same claim, and they should not be able to
disagree about whether they need backing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.handlers.types import COMPANION_FIELDS, is_probability_field
from capitalscan.jobs import db_io

# The views ADR 118 puts TypeScript routes on. These are the ones where the
# raise is absent and the columns are the only guarantee, so they are named
# explicitly rather than left to the sweep below.
WEB_VIEWS = ("v_screen", "v_stats")

# Views that expose a probability without its companions, each with the
# reason it is allowed to. Same shape as
# `jobs/threshold_lint.py::KNOWN_EXCEPTIONS`, and for the same reason: an
# undocumented exemption is indistinguishable from a defect nobody noticed.
#
# A test below asserts every entry still describes a real gap, so an entry
# that stops matching has to be deleted rather than quietly kept.
KNOWN_GAPS: dict[str, str] = {
    "v_forward": (
        "Phase 6. Exposes p_touch_* and p_adverse_* from `predictions`, which "
        "is empty because no model exists (ADR 093 Provisional, ADR 113 "
        "opened the phase conditionally). It carries `cell_n_eff` but no "
        "interval and no q-value, so it cannot satisfy invariant 8 as it "
        "stands. **ADR 113's model must add them before `/forward` ships** - "
        "a quantile fan with a sample size and no interval is exactly the "
        "object ADR 112 argues against."
    ),
}


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


def _view_columns() -> dict[str, list[str]]:
    """Every public view and its columns, in declaration order."""
    engine = db_io.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.table_name, c.column_name "
                "FROM information_schema.columns c "
                "JOIN information_schema.views v "
                "  ON v.table_schema = c.table_schema AND v.table_name = c.table_name "
                "WHERE c.table_schema = 'public' "
                "ORDER BY c.table_name, c.ordinal_position"
            )
        ).fetchall()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out


@pytest.fixture(scope="module")
def views() -> dict[str, list[str]]:
    found = _view_columns()
    assert found, "no views found; the database is not migrated"
    return found


def _missing(columns: list[str]) -> list[str]:
    return [f for f in COMPANION_FIELDS if f not in columns]


def _probabilities(columns: list[str]) -> list[str]:
    return [c for c in columns if is_probability_field(c)]


# ---------------------------------------------------------------------------
# ADR 118's requirement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", WEB_VIEWS)
def test_the_views_typescript_reads_carry_the_companions(views, view):
    """The two views ADR 118 puts a route on, with no validator behind them.

    Named individually rather than swept, because these are the ones whose
    failure would ship a bare probability to a browser.
    """
    columns = views[view]
    stated = _probabilities(columns)
    assert stated, f"{view} exposes no probability; this test would be vacuous"
    missing = _missing(columns)
    assert not missing, (
        f"{view} exposes {stated} and is missing {missing}. Under ADR 118 no "
        "validator guards this view, so the columns are the only guarantee."
    )


def test_the_web_views_carry_the_probabilities_the_screener_needs(views):
    """Guards the test above against passing on a view that lost its rate.

    A `v_screen` that stopped exposing `p_hit` would satisfy "no probability
    without companions" trivially and break every statistical panel.
    """
    assert "p_hit" in views["v_screen"]
    assert "p_hit" in views["v_stats"]


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def test_no_view_exposes_a_probability_without_its_companions(views):
    """Every view, not only the two. A future migration adding `p_hit` to
    `v_chart` should fail here rather than reach a route."""
    offenders = {
        name: (_probabilities(columns), _missing(columns))
        for name, columns in views.items()
        if _probabilities(columns) and _missing(columns) and name not in KNOWN_GAPS
    }
    assert not offenders, "\n".join(
        f"{name}: exposes {probs}, missing {missing}"
        for name, (probs, missing) in offenders.items()
    )


def test_at_least_one_view_is_checked_by_the_sweep(views):
    """The sweep passes vacuously on a database with no probability columns
    at all, which is a state worth telling apart from success."""
    checked = [n for n, c in views.items() if _probabilities(c)]
    assert len(checked) >= 2, f"only {checked} expose a probability"


# ---------------------------------------------------------------------------
# The exception list is checked, not trusted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", sorted(KNOWN_GAPS))
def test_every_known_gap_is_still_a_gap(views, view):
    """An entry that stops matching describes a defect that was fixed.

    `threshold_lint.KNOWN_EXCEPTIONS` learned this the hard way: an
    exemption kept past its fix stops documenting a known problem and
    starts hiding a working one, and nobody thinks to look.
    """
    assert view in views, f"{view} no longer exists; delete its KNOWN_GAPS entry"
    columns = views[view]
    assert _probabilities(columns), (
        f"{view} no longer exposes a probability; delete its KNOWN_GAPS entry"
    )
    assert _missing(columns), (
        f"{view} now carries every companion. Delete its KNOWN_GAPS entry - the gap is closed."
    )


def test_the_known_gap_names_what_would_close_it(views):
    """A reason string that does not say what to do is a shrug in a dict."""
    assert "ADR 113" in KNOWN_GAPS["v_forward"]


# ---------------------------------------------------------------------------
# One predicate, two enforcement points
# ---------------------------------------------------------------------------


def test_the_sql_side_uses_the_same_probability_predicate_as_the_types(views):
    """A column named `p_hit` and a dataclass field named `p_hit` are the
    same claim and must not disagree about whether they need backing.

    Asserted by finding the columns this module would flag and checking the
    Python types treat those names identically.
    """
    columns = set(views["v_stats"])
    for name in ("p_hit", "baseline", "edge"):
        assert name in columns
        assert is_probability_field(name)
    # And the companions are not themselves probabilities, or the rule would
    # recurse: an interval needing an interval.
    for name in COMPANION_FIELDS:
        assert not is_probability_field(name), f"{name} is a companion, not a claim"


def test_p_value_columns_are_not_treated_as_probabilities(views):
    """`v_stats` exposes `p_value_randomization`. A p-value is a property of
    the test, has no interval, and requiring one would recurse."""
    assert "p_value_randomization" in views["v_stats"]
    assert not is_probability_field("p_value_randomization")
