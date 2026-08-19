"""`CURRENT_DATE` never means "today" in this system (ADR 119).

The database runs `Etc/UTC`. Between 00:00 UTC and midnight ET -- about
seven hours a day, 5pm to midnight Pacific -- `CURRENT_DATE` is a day ahead
of the session that just closed. Anything using it to mean the market's day
is wrong for that window, which is exactly when someone reviews the day.

It appeared in four places and was fixed in three separate passes, which is
why this exists: a class of defect that keeps returning needs a test rather
than a third careful reading.

Source-level, because the alternative is a clock-dependent test that passes
for seventeen hours a day and fails for seven. This one fails the moment the
string reappears, at any hour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# Files that legitimately contain the string. Each is history or an
# explanation, never a live query.
ALLOWED: dict[str, str] = {
    "capitalscan/jobs/views.py": (
        "Holds `V_POSITIONS_DDL_PRE_115`, the pre-ADR-115 view kept verbatim "
        "so `downgrade()` restores it exactly, plus prose explaining the "
        "defect. `V_POSITIONS_DDL_MARKET_DATE` is what the migration applies."
    ),
    "capitalscan/handlers/_db.py": "A comment explaining why it is not used.",
    "web/lib/screen.ts": "A comment explaining why it is not used.",
}

# Where a live query could plausibly hide.
#
# **Production code only.** Tests hold the string as fixture data on purpose
# -- `test_threshold_lint.py` reconstructs the pre-ADR-095 view to prove its
# matcher sees it, and this file quotes it in its own assertions. Scanning
# them would make the guard flag the tests written to enforce the rule,
# which is how a guard gets deleted rather than obeyed.
SEARCH_GLOBS = ("capitalscan/**/*.py", "web/lib/**/*.ts", "web/app/**/*.tsx", "db/*.sql")
EXCLUDE_PARTS = {"__pycache__", "node_modules", "tests"}


def _files() -> list[Path]:
    found: list[Path] = []
    for pattern in SEARCH_GLOBS:
        found.extend(p for p in REPO.glob(pattern) if not (EXCLUDE_PARTS & set(p.parts)))
    return sorted(found)


def _offending_lines(path: Path) -> list[str]:
    """Lines mentioning `CURRENT_DATE` that are not comments.

    Comment prefixes for the three languages this repo uses. A line inside a
    Python docstring is not caught, which is why `views.py` is on the
    allowlist rather than relying on this.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines:
        if "CURRENT_DATE" not in line:
            continue
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "--")):
            continue
        out.append(stripped)
    return out


def test_no_live_query_uses_current_date():
    """The guard. `market_date()` is the one definition of the market's day."""
    offenders = {}
    for path in _files():
        rel = path.relative_to(REPO).as_posix()
        if rel in ALLOWED:
            continue
        lines = _offending_lines(path)
        if lines:
            offenders[rel] = lines
    assert not offenders, (
        "CURRENT_DATE in a live query. The database runs Etc/UTC, so it is a "
        "day ahead of the market for ~7 hours daily. Use public.market_date().\n"
        + "\n".join(f"  {f}: {ls}" for f, ls in offenders.items())
    )


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_every_allowlisted_file_still_mentions_it(rel):
    """An allowlist entry that stops matching is describing something that
    was fixed, and it must be deleted rather than kept.

    The same rule `threshold_lint.KNOWN_EXCEPTIONS` follows, for the same
    reason: an exemption kept past its fix stops documenting a known
    problem and starts hiding a working one.
    """
    text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    assert "CURRENT_DATE" in text, f"{rel} no longer mentions it; delete its ALLOWED entry"


def test_the_replacement_exists_and_is_named_once():
    """`market_date()` is defined in exactly one place."""
    from capitalscan.jobs import views

    assert "CREATE OR REPLACE FUNCTION public.market_date()" in views.MARKET_DATE_DDL
    assert "America/New_York" in views.MARKET_DATE_DDL
    # STABLE, not IMMUTABLE: it reads the clock. IMMUTABLE would let the
    # planner fold it to a constant and cache a stale day across a session.
    assert re.search(r"\bSTABLE\b", views.MARKET_DATE_DDL)
    assert "IMMUTABLE" not in views.MARKET_DATE_DDL


def test_v_positions_uses_the_function_not_the_keyword():
    from capitalscan.jobs import views

    assert "public.market_date()" in views.V_POSITIONS_DDL_MARKET_DATE
    assert "CURRENT_DATE" not in views.V_POSITIONS_DDL_MARKET_DATE
    # And the pre-115 DDL still carries it, because a downgrade restores the
    # old view exactly rather than approximately.
    assert "CURRENT_DATE" in views.V_POSITIONS_DDL_PRE_115
