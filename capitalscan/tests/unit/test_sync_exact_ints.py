"""`events.cluster_id` survives the sync digit for digit (2026-09-25).

`cluster_id` is a 63-bit hash near 6e17. pandas reads a nullable integer
column holding any NULL as `float64`, exact only to 2**53, so the full sync
of 2026-09-25 wrote research's `648924461278083920` to serving as
`648924461278083968` -- 298 of a 300-row sample differed in that column
alone. `EXACT_INT_COLUMNS` selects the column again as text and
`_restore_exact_ints` puts the exact value back before any write.

No database here.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import sync as sync_job

BIG = 648924461278083920  # a real research value from 2026-09-25


def test_the_premise_float64_loses_the_digits():
    """Why the fix exists. If pandas ever stops doing this, the guard is
    redundant but harmless; this test says which it is."""
    lossy = pd.Series([BIG, None]).astype("float64")
    assert int(lossy.iloc[0]) != BIG


def test_the_text_copy_restores_every_digit_and_keeps_nulls():
    frame = pd.DataFrame(
        {
            "cluster_id": pd.Series([BIG, None], dtype="float64"),
            "__exact_cluster_id": [str(BIG), None],
            "ticker": ["CNI", "EOG"],
        }
    )
    out = sync_job._restore_exact_ints(frame, ("cluster_id",))
    assert out["cluster_id"].tolist() == [BIG, None]
    assert out["cluster_id"].dtype == object
    assert "__exact_cluster_id" not in out.columns


def test_the_value_survives_copy_upserts_payload_conversion():
    """`copy_upsert` runs `astype(object).where(notna, None)`. An exact int
    in an object column must come out of that unchanged."""
    out = sync_job._restore_exact_ints(
        pd.DataFrame({"cluster_id": [None], "__exact_cluster_id": [str(BIG)]}), ("cluster_id",)
    )
    payload = out.astype(object).where(pd.notna(out), None)
    assert payload["cluster_id"].iloc[0] == BIG


def test_a_missing_text_copy_is_an_error_not_a_silent_float():
    frame = pd.DataFrame({"cluster_id": [float(BIG)]})
    with pytest.raises(ValueError, match="__exact_cluster_id"):
        sync_job._restore_exact_ints(frame, ("cluster_id",))


def test_a_column_less_empty_chunk_passes_through():
    empty = pd.DataFrame()
    assert sync_job._restore_exact_ints(empty, ("cluster_id",)) is empty


def _events_sql(tables) -> tuple[str, tuple[str, ...]]:
    t = next(t for t in tables if t.name == "events")
    return t.sql, t.exact_int_columns


def test_both_nightly_events_arms_select_the_text_copy():
    sql, exact = _events_sql(sync_job._tables(date(2020, 1, 1), "h"))
    assert exact == ("cluster_id",)
    for arm in sql.split("UNION ALL"):
        assert "cluster_id::text AS __exact_cluster_id" in arm


def test_the_live_events_push_selects_the_text_copy():
    sql, exact = _events_sql(
        sync_job._live_tables("h", date(2026, 9, 25), "r", sync_job.LiveWatermark())
    )
    assert exact == ("cluster_id",)
    assert "cluster_id::text AS __exact_cluster_id" in sql


def test_prepare_chunk_restores_before_anything_else_reads_the_frame():
    table = next(t for t in sync_job._tables(date(2020, 1, 1), "h") if t.name == "events")
    chunk = pd.DataFrame(
        {
            "config_hash": ["h"],
            "ticker": ["CNI"],
            "signal_date": [date(2010, 11, 16)],
            "signal_type": ["bb_lower_touch"],
            "entry_kind": ["next_open"],
            "cluster_id": pd.Series([float(BIG)]),
            "__exact_cluster_id": [str(BIG)],
        }
    )
    out = sync_job._prepare_chunk(chunk, target=None, table=table)
    assert out["cluster_id"].iloc[0] == BIG
