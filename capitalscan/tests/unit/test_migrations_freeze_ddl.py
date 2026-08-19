"""A migration may not import a DDL constant that later revisions mutate.

**This shipped and broke every fresh database, on 2026-08-19.**

`jobs/views.py` holds the *current* definition of each view. A migration is
a statement about one point in history. When a migration imports the live
constant, it stops describing its own revision and starts emitting whatever
the view looks like today — so the moment a later migration changes that
view, the older one is wrong.

ADR 122 added `events.in_trade`. Four earlier migrations imported
`V_SCREEN_LIVE_DDL` and immediately began emitting `AND e.in_trade` against
a table that does not have the column until `f2d16b47c093` runs. Every
from-scratch replay failed:

    ProgrammingError: column e.in_trade does not exist

**It was invisible locally and had to be**, which is the part worth
remembering. A developer applies only the new migrations, so the broken
path is never taken. Only a full replay hits it — CI, and any new
deployment.

The rule this file enforces: a migration carries its SQL as a literal, or
imports only a constant whose name pins it to a revision (`*_PRE_119`,
`*_AT_THIS_REVISION`). A bare `V_SCREEN_LIVE_DDL` is a live reference and
is refused.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[3] / "db" / "migrations" / "versions"

# A name is revision-pinned if it says so. `_PRE_<rev>` is the existing
# convention for "the form before this migration"; `_AT_THIS_REVISION` is
# for a literal defined in the migration itself and is not imported at all.
#
# Anything else from `jobs.views` is the live definition and will drift.
_PINNED_SUFFIXES = ("_AT_THIS_REVISION",)


def _is_pinned(name: str) -> bool:
    if any(name.endswith(s) for s in _PINNED_SUFFIXES):
        return True
    # `_PRE_115`, `_PRE_119`, `_PRE_120`, ... — frozen by construction,
    # because they exist to record a form that has already been superseded.
    parts = name.rsplit("_PRE_", 1)
    return len(parts) == 2 and parts[1].isdigit()


def _migrations() -> list[Path]:
    return sorted(p for p in VERSIONS.glob("*.py") if not p.name.startswith("__"))


def _views_imports(path: Path) -> list[str]:
    """Names this migration imports from `capitalscan.jobs.views`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "capitalscan.jobs.views":
            out.extend(alias.name for alias in node.names)
    return out


@pytest.mark.parametrize("path", _migrations(), ids=lambda p: p.name)
def test_migration_imports_no_mutable_ddl(path: Path) -> None:
    live = [n for n in _views_imports(path) if not _is_pinned(n)]
    assert not live, (
        f"{path.name} imports {live} from jobs.views.\n\n"
        "Those are the *current* definitions and will change under this "
        "migration the next time a view is edited — which is how ADR 122 "
        "broke every from-scratch replay with `column e.in_trade does not "
        "exist`. Inline the SQL as a literal named `_*_AT_THIS_REVISION`, "
        "or import a `_PRE_<rev>` constant that is frozen by construction."
    )


def test_the_sweep_sees_the_migrations() -> None:
    """A moved directory would make every assertion above pass silently."""
    found = _migrations()
    assert len(found) >= 10, f"only {len(found)} migrations found under {VERSIONS}"


def test_at_this_revision_constants_are_literals() -> None:
    """`_*_AT_THIS_REVISION` must be a plain string, not a computed value.

    Naming a constant "at this revision" and then building it from an
    imported one would satisfy the check above while keeping the defect.
    """
    offenders: list[str] = []
    for path in _migrations():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if not target.id.endswith("_AT_THIS_REVISION"):
                    continue
                # `literal_eval` is the right definition of "literal":
                # it accepts a string, a dict of constants, a tuple, and
                # refuses anything computed — including a name, a call, or
                # a concatenation of an imported constant.
                try:
                    ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    offenders.append(f"{path.name}:{target.id}")
    assert not offenders, (
        f"{offenders} are named _AT_THIS_REVISION but are computed rather "
        "than literal. The name promises the SQL is frozen; only a string "
        "literal keeps that promise."
    )
