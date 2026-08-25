"""The ADR index and the ADR bodies must name the same set of decisions.

Third occurrence before this test existed: ADR 094 alone, then 110 and 111
together, then 130 through 146 in one block. Each was caught by a manual
audit, which is why each was caught late. The index is what `CLAUDE.md`
sends a reader to first, so an ADR missing from it is an ADR that does not
exist for anyone who does not already know its number.

Ordering is asserted as well as membership. An out-of-order body is how a
block ends up appended past `## Open items` and `## Phase gates` instead of
landing with its neighbours, which is exactly how 145 and 146 were written.
"""

from __future__ import annotations

import re
from pathlib import Path

DECISIONS = Path(__file__).resolve().parents[3] / "docs" / "DECISIONS.md"

INDEX_ROW = re.compile(r"^\| (\d{3}) \|", re.M)
BODY_HEADING = re.compile(r"^## (\d{3})\.", re.M)


def _read() -> str:
    assert DECISIONS.exists(), f"DECISIONS.md not found at {DECISIONS}"
    return DECISIONS.read_text(encoding="utf-8")


def test_every_body_has_an_index_row() -> None:
    text = _read()
    index = set(INDEX_ROW.findall(text))
    bodies = set(BODY_HEADING.findall(text))
    missing = sorted(bodies - index)
    assert not missing, f"ADR bodies with no index row: {missing}"


def test_every_index_row_has_a_body() -> None:
    text = _read()
    index = set(INDEX_ROW.findall(text))
    bodies = set(BODY_HEADING.findall(text))
    orphans = sorted(index - bodies)
    assert not orphans, f"index rows with no ADR body: {orphans}"


def test_no_duplicate_ids() -> None:
    text = _read()
    for label, ids in (("index", INDEX_ROW.findall(text)), ("body", BODY_HEADING.findall(text))):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert not dupes, f"duplicate {label} ids: {dupes}"


def test_ids_are_in_ascending_order() -> None:
    text = _read()
    for label, ids in (("index", INDEX_ROW.findall(text)), ("body", BODY_HEADING.findall(text))):
        assert ids == sorted(ids), f"{label} ids are out of order"


def test_every_body_declares_a_status() -> None:
    """A decision with no status is not a decision anyone can act on."""
    text = _read()
    # Three formats are in use and all three are fine: `Status: Pinned`,
    # `**Status.** Pinned, <date>`, and `**Date:** ... **Status:** ...`.
    status = re.compile(r"\*{0,2}Status\*{0,2}\s*[.:]")
    sections = re.split(r"^## (?=\d{3}\.)", text, flags=re.M)[1:]
    undeclared = [s[:3] for s in sections if not status.search(s[:1200])]
    assert not undeclared, f"ADR bodies with no Status line: {undeclared}"
