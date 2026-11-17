"""`copy_upsert` streams via COPY instead of building a dict per row.

**Why it exists.** Profiled during a full `cscan sync` on 2026-08-26: the
Pi sat at load 1.11 of four cores with its SD card 15% utilised, while the
workstation held 894 MB and 53.8% of *one* core. Neither the network nor
the database was the constraint -- it was Python. `upsert` turns each row
into a dict and SQLAlchemy re-binds every dict into parameters, so rows
make three full representations of themselves to move between two
databases. 7,469,519 rows took 114.2 minutes, about 1,090 rows/s.

Measured against a scratch table after this landed: **78,589 rows/s**, a
72x improvement.

**The bug these tests exist for.** A pandas column holding integers and a
null is `float64`, so `10` is really `10.0`. `astype(object)` preserves the
float, `COPY` writes the text `"10.0"`, and Postgres rejects it for an
integer column. The dict path never hit this -- psycopg adapts a Python
float to an integer parameter, and text COPY does not.

The first fix was `payload[name] = payload[name].map(...)`, which fails in
*both* directions: assigning a Series back into an object-dtype frame
re-infers the dtype, turning the ints back into floats **and** the Nones
back into NaN. `[10, 20, None]` came out `[10.0, 20.0, nan]`. Both
regressions are pinned below.

No database here: the conversion logic is exercised directly.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import types as sa_types

from capitalscan.jobs import db_io


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """The NaN -> None step `copy_upsert` performs before COPY."""
    return frame.astype(object).where(pd.notna(frame), None)


# ---------------------------------------------------------------------------
# The float-integer trap
# ---------------------------------------------------------------------------


def test_a_pandas_int_column_with_a_null_is_float_underneath():
    """Not a test of our code -- a test of the premise. If pandas ever stops
    widening this, the coercion below becomes dead weight and should go."""
    f = pd.DataFrame({"n": [10, 20, None]})
    assert str(f["n"].dtype) == "float64"
    assert _prepare(f)["n"].iloc[0] == 10.0
    assert isinstance(_prepare(f)["n"].iloc[0], float)


def test_assigning_a_converted_series_back_reinfers_and_corrupts():
    """The first fix, pinned as a regression.

    It looks correct and is worse than doing nothing: the ints come back as
    floats *and* the Nones come back as NaN, so a null would reach Postgres
    as the text 'nan'.
    """
    payload = _prepare(pd.DataFrame({"n": [10, 20, None]}))
    payload["n"] = payload["n"].map(lambda v: None if v is None else int(v))
    values = list(payload["n"])
    assert values[0] == 10.0 and isinstance(values[0], float), "dtype re-inferred"
    assert isinstance(values[2], float) and math.isnan(values[2]), "None became NaN"


def test_converting_per_value_survives_the_frame():
    """What `copy_upsert` actually does: the frame is out of the way before
    the conversion happens, so nothing can re-infer."""
    payload = _prepare(pd.DataFrame({"n": [10, 20, None]}))
    rows = [
        [None if v is None or (isinstance(v, float) and math.isnan(v)) else int(v) for v in row]
        for row in payload.itertuples(index=False, name=None)
    ]
    assert rows == [[10], [20], [None]]
    assert all(v is None or isinstance(v, int) for row in rows for v in row)


# ---------------------------------------------------------------------------
# Validation, which must reject rather than write the wrong thing
# ---------------------------------------------------------------------------


class _Col:
    def __init__(self, name: str, type_):
        self.name = name
        self.type = type_


class _Cols(dict):
    def __iter__(self):
        return iter(self.values())


class _Table:
    def __init__(self, names: dict):
        self.columns = _Cols({n: _Col(n, t) for n, t in names.items()})


@pytest.fixture
def fake_table(monkeypatch):
    table = _Table(
        {
            "ticker": sa_types.TEXT(),
            "ts": sa_types.DATE(),
            "n": sa_types.INTEGER(),
            "v": sa_types.NUMERIC(),
        }
    )
    monkeypatch.setattr(db_io, "_table", lambda engine, name: table)
    return table


def test_an_empty_frame_writes_nothing_and_does_not_touch_the_database(fake_table):
    """`engine` is a bare object: any attempt to connect would raise."""
    assert db_io.copy_upsert(object(), "t", pd.DataFrame(), ["ticker"]) == 0


def test_a_frame_sharing_no_columns_raises(fake_table):
    with pytest.raises(ValueError, match="shares no columns"):
        db_io.copy_upsert(object(), "t", pd.DataFrame({"nope": [1]}), ["ticker"])


def test_a_conflict_column_missing_from_the_frame_raises(fake_table):
    """ON CONFLICT against a column that was never sent matches nothing, so
    every row inserts and the key silently duplicates."""
    with pytest.raises(ValueError, match="absent from the frame"):
        db_io.copy_upsert(object(), "t", pd.DataFrame({"n": [1]}), ["ticker"])


def test_an_invalid_update_column_raises(fake_table):
    """Same rule `upsert` enforces: a typo would silently update nothing."""
    with pytest.raises(ValueError, match="invalid entries"):
        db_io.copy_upsert(
            object(),
            "t",
            pd.DataFrame({"ticker": ["A"], "n": [1]}),
            ["ticker"],
            update_columns=["nonexistent"],
        )


def test_a_conflict_column_cannot_also_be_updated(fake_table):
    with pytest.raises(ValueError, match="invalid entries"):
        db_io.copy_upsert(
            object(),
            "t",
            pd.DataFrame({"ticker": ["A"], "n": [1]}),
            ["ticker"],
            update_columns=["ticker"],
        )


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_it_takes_the_same_arguments_as_upsert():
    """The two are meant to be swappable at a call site. A drifting
    signature is how a caller ends up passing conflict_cols positionally
    into update_columns."""
    import inspect

    a = list(inspect.signature(db_io.upsert).parameters)
    b = list(inspect.signature(db_io.copy_upsert).parameters)
    assert b[:5] == ["engine", "table_name", "frame", "conflict_cols", "update_columns"]
    assert a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and a[4] == b[4]
    # The one addition is keyword-only, so it cannot shift a positional.
    extra = inspect.signature(db_io.copy_upsert).parameters["skip_unchanged_ignoring"]
    assert extra.kind is inspect.Parameter.KEYWORD_ONLY
    assert b[5:] == ["skip_unchanged_ignoring"]


# ---------------------------------------------------------------------------
# The conflict action (2026-11-17)
# ---------------------------------------------------------------------------


def test_without_the_guard_every_conflict_rewrites():
    assert db_io._conflict_action("t", ["a", "b"], None) == (
        'DO UPDATE SET "a" = EXCLUDED."a", "b" = EXCLUDED."b"'
    )


def test_nothing_to_update_is_do_nothing_with_or_without_the_guard():
    assert db_io._conflict_action("t", [], None) == "DO NOTHING"
    assert db_io._conflict_action("t", [], ("a",)) == "DO NOTHING"


def test_the_guard_compares_every_updated_column_but_the_ignored_ones():
    """A re-shipped row identical to the stored one must not be rewritten.
    That rewrite is what kept a retried sync from ever finishing on the Pi:
    47 runs, each re-sending the same first rows and killed at 4 hours."""
    got = db_io._conflict_action("events", ["a", "modified_at", "b"], ("modified_at",))
    assert got == (
        'DO UPDATE SET "a" = EXCLUDED."a", "modified_at" = EXCLUDED."modified_at", '
        '"b" = EXCLUDED."b" WHERE ROW("events"."a", "events"."b") '
        'IS DISTINCT FROM ROW(EXCLUDED."a", EXCLUDED."b")'
    )


def test_the_ignored_column_is_still_written_when_another_changed():
    """Ignoring a column in the comparison must not drop it from the SET."""
    got = db_io._conflict_action("events", ["a", "modified_at"], ("modified_at",))
    assert '"modified_at" = EXCLUDED."modified_at"' in got


def test_only_ignored_columns_left_means_nothing_can_change():
    assert db_io._conflict_action("t", ["modified_at"], ("modified_at",)) == "DO NOTHING"


def test_sync_passes_the_guard_and_ignores_servings_own_stamp():
    import inspect

    from capitalscan.jobs import sync

    assert sync.SYNC_SKIP_COMPARE == ("modified_at",)
    src = inspect.getsource(sync.run_sync)
    assert "skip_unchanged_ignoring=SYNC_SKIP_COMPARE" in src


def test_the_staging_table_is_temp_and_drops_on_commit():
    """Two properties, both load-bearing. TEMP means concurrent syncs cannot
    collide on the name; ON COMMIT DROP means an exception between the COPY
    and the INSERT cannot leave it behind."""
    import inspect

    src = inspect.getsource(db_io.copy_upsert)
    assert "CREATE TEMP TABLE" in src
    assert "ON COMMIT DROP" in src


def test_non_finite_floats_become_null_in_a_numeric_column():
    """`numeric` has no NaN in Postgres's sense that survives a round trip
    here, and absent must stay absent (invariant 4)."""
    payload = _prepare(pd.DataFrame({"v": [1.5, np.nan, 3.25]}))
    assert payload["v"].iloc[1] is None
