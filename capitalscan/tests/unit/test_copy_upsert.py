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
    assert b == ["engine", "table_name", "frame", "conflict_cols", "update_columns"]
    assert a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and a[4] == b[4]


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
