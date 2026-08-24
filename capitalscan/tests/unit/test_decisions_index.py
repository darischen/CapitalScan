"""`DECISIONS.md`'s index table must agree with its bodies.

The index has drifted from the bodies three times: ADR 094, ADRs 110-111,
and ADRs 130-146 (found 2026-08-22, seventeen missing rows). Each was caught
by an audit rather than by CI, which is why this exists.

`DECISIONS.md` is the source of truth for every decision in the project, and
CLAUDE.md instructs that it be read before writing any code. An ADR whose row
is missing is invisible to anyone reading the table first, which is the
intended entry point.
"""

from __future__ import annotations

import re
from pathlib import Path

DECISIONS = Path(__file__).resolve().parents[3] / "docs" / "DECISIONS.md"

# `| 147 | Title | Status |` — the index. Anchored and requiring the trailing
# pipe so measurement tables elsewhere in the file cannot match.
_INDEX_ROW = re.compile(r"^\|\s*(\d{1,3})\s*\|.+\|.*\|\s*$")
# `## 147. Title` — a body.
_BODY = re.compile(r"^##\s+(\d{1,3})\.\s+\S")


def _read() -> list[str]:
    return DECISIONS.read_text(encoding="utf-8").splitlines()


def _index_numbers() -> list[int]:
    """Numbers from the index table only.

    The table sits before the first body, so reading stops there — the file
    contains other pipe tables (split ratios, market-cap measurements) whose
    first column is numeric.
    """
    out: list[int] = []
    for line in _read():
        if _BODY.match(line):
            break
        m = _INDEX_ROW.match(line)
        if m:
            out.append(int(m.group(1)))
    return out


def _body_numbers() -> list[int]:
    return [int(m.group(1)) for line in _read() if (m := _BODY.match(line))]


class TestIndexBodyParity:
    def test_every_body_has_an_index_row(self):
        missing = sorted(set(_body_numbers()) - set(_index_numbers()))
        assert not missing, (
            f"ADR bodies with no index row: {missing}. "
            "Add the row; the index is how DECISIONS.md is read."
        )

    def test_every_index_row_has_a_body(self):
        """The other direction, which the previous drifts did not exercise.

        A row pointing at nothing is worse than a missing row: it reads as a
        decision that was made and is merely hard to find.
        """
        orphans = sorted(set(_index_numbers()) - set(_body_numbers()))
        assert not orphans, f"index rows with no ADR body: {orphans}"

    def test_the_latest_adr_is_indexed(self):
        bodies = _body_numbers()
        assert max(bodies) in _index_numbers()


class TestNoDuplicatesOrDisorder:
    def test_no_duplicate_index_numbers(self):
        nums = _index_numbers()
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        assert not dupes, f"duplicate index rows: {dupes}"

    def test_no_duplicate_bodies(self):
        nums = _body_numbers()
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        assert not dupes, f"duplicate ADR bodies: {dupes}"

    def test_index_is_ascending(self):
        nums = _index_numbers()
        assert nums == sorted(nums), "index table is out of order"

    def test_bodies_are_ascending(self):
        """Ordering is not cosmetic here.

        ADRs 145 and 146 were appended after the 'Phase gates' section
        rather than in sequence, which is how an unnumbered gap goes
        unnoticed. Keeping bodies ordered makes the next gap visible.
        """
        nums = _body_numbers()
        assert nums == sorted(nums), (
            f"ADR bodies are out of order; first break at "
            f"{next(b for a, b in zip(nums, nums[1:]) if b < a)}"
        )


class TestTheSelfCheck:
    """The parser must be able to fail. A matcher that matches nothing passes
    every assertion above trivially, which is the shape of check that keeps
    biting this project."""

    def test_the_index_is_actually_being_parsed(self):
        assert len(_index_numbers()) > 100

    def test_the_bodies_are_actually_being_parsed(self):
        assert len(_body_numbers()) > 100

    def test_counts_are_equal(self):
        assert len(set(_index_numbers())) == len(set(_body_numbers()))
