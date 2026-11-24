"""ADR 210: forward-log exclusions are a separate statement, never an edit.

Structural checks, no database. The property that matters is that every
reader measuring the model filters on the same table, and that writing an
exclusion can never touch `predictions` or `outcomes`.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from capitalscan.jobs import exclusions, outcomes, sync

REPO = Path(__file__).resolve().parents[3]


def test_writing_an_exclusion_touches_only_its_own_table():
    """ADR 029: the forward log is never rewritten. The insert names one
    table, and no statement in the module updates or deletes anything."""
    sql = exclusions.INSERT_SQL
    assert re.search(r"INSERT INTO forward_log_exclusions\b", sql)
    assert "ON CONFLICT (prediction_id) DO NOTHING" in sql
    source = Path(exclusions.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(UPDATE|DELETE FROM)\s+(predictions|outcomes)\b", source)


def test_the_score_skips_excluded_rows():
    assert exclusions.NOT_EXCLUDED in outcomes.SCORE_SQL


@pytest.mark.parametrize(
    "path",
    ["web/lib/reliability.ts", "web/lib/ticker.ts"],
)
def test_each_web_reader_filters_on_the_shared_clause(path: str):
    """The web spells the clause once, in `reliability.ts`, with the same
    text as the Python constant, and each query interpolates it."""
    text = (REPO / path).read_text(encoding="utf-8")
    assert "${NOT_EXCLUDED}" in text
    reliability = (REPO / "web/lib/reliability.ts").read_text(encoding="utf-8")
    assert f'"{exclusions.NOT_EXCLUDED}"' in reliability


def test_both_reliability_queries_filter():
    """The reliability table and the recent realised rate are separate
    queries; filtering one leaves the other averaging corrupt rows."""
    text = (REPO / "web/lib/reliability.ts").read_text(encoding="utf-8")
    assert text.count("AND ${NOT_EXCLUDED}") == 2


def test_the_table_ships_to_serving_after_predictions():
    """Foreign key: an exclusion arriving before its prediction would fail
    on a fresh serving store. And it must ship at all, or serving's
    reliability table keeps averaging what research set aside."""
    names = [t.name for t in sync._tables(date(1996, 8, 27), "86e91448a65aa40b")]
    assert "forward_log_exclusions" in names
    assert names.index("predictions") < names.index("forward_log_exclusions")
    table = next(
        t
        for t in sync._tables(date(1996, 8, 27), "86e91448a65aa40b")
        if t.name == "forward_log_exclusions"
    )
    assert table.key == ("prediction_id",)


def test_an_exclusion_needs_a_reason():
    with pytest.raises(ValueError, match="reason"):
        exclusions.exclude([1], "  ", engine=None)


def test_the_summary_names_what_it_skipped():
    report = exclusions.ExcludeReport(requested=5, written=3, already=1, missing=[42])
    assert report.summary() == "excluded 3 of 5, 1 already excluded, 1 not in predictions: [42]"
