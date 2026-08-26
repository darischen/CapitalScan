"""`bar_rejects.payload` must survive `json.dumps` (2026-08-26).

`run_shares` builds reject payloads straight off a DataFrame, so `filed_on`
and `period_end` arrive as `datetime.date` and `shares` as a numpy integer.
`payload` is JSONB and psycopg serialises it with the stdlib encoder, which
raises on all of those.

**What it cost.** `cscan nightly` died at `db_io.append(engine,
"bar_rejects", share_rejects)` *after* `run_shares` had written 236,008
rows. The share data landed, the job still reported `failed`, and that
aborted every remaining nightly step -- earnings, indicators, events, path
capture and the sync. A reject log took down the pipeline it exists to
annotate.

The traceback ended `[NOTE] when serializing dict item 'filed_on'`, which
named the field but not the fix.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from capitalscan.jobs.ingest import _json_safe, _json_safe_payload


def _dumps(payload: dict) -> str:
    """`json.dumps` with no `default=`, exactly as psycopg calls it."""
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# The failure that happened
# ---------------------------------------------------------------------------


def test_a_date_serialises():
    """The exact field and type from the traceback."""
    assert _json_safe(date(2026, 8, 26)) == "2026-08-26"
    _dumps(_json_safe_payload({"filed_on": date(2026, 8, 26)}))


def test_the_real_shares_payload_shape_round_trips():
    """Every field `run_shares` actually puts in, with the types a DataFrame
    row actually produces."""
    payload = _json_safe_payload(
        {
            "shares": np.int64(4_250_000_000),
            "filed_on": date(2026, 8, 26),
            "period_end": pd.Timestamp("2026-06-30"),
            "source": "sec_xbrl",
            "accn": "0000320193-26-000012",
        }
    )
    assert json.loads(_dumps(payload))["shares"] == 4_250_000_000


# ---------------------------------------------------------------------------
# The near-misses found while fixing it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_become_null_not_bare_NaN(bad):
    """`json.dumps` emits bare `NaN` / `Infinity` for these. That is **not
    valid JSON** and Postgres rejects it on the way into JSONB, so the guard
    would have failed a second time on the next run.

    Absent stays absent (invariant 4) rather than becoming a number.
    """
    assert _json_safe(bad) is None
    assert json.loads(_dumps(_json_safe_payload({"v": bad})))["v"] is None


def test_NaT_becomes_null_not_the_string_NaT():
    """`pd.NaT` is a `datetime` subclass, so it reaches the date branch and
    `isoformat()` returns the literal string "NaT" -- a missing date
    recorded as though it were a real one."""
    assert _json_safe(pd.NaT) is None
    assert json.loads(_dumps(_json_safe_payload({"period_end": pd.NaT})))["period_end"] is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (np.int64(7), 7),
        (np.float64(1.5), 1.5),
        (np.bool_(True), True),
        (datetime(2026, 8, 26, 13, 45), "2026-08-26T13:45:00"),
    ],
)
def test_numpy_scalars_become_python_scalars(value, expected):
    assert _json_safe(value) == expected


# ---------------------------------------------------------------------------
# What must not change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "sec_xbrl", True, False, 0, -1, 3.25])
def test_ordinary_json_types_pass_through_untouched(value):
    assert _json_safe(value) == value


def test_an_unknown_type_is_stringified_rather_than_raising():
    """A reject log that cannot be written is worse than one that is coarse.
    The whole point of this guard is that annotating a failure must never
    become a failure."""
    from decimal import Decimal

    payload = _json_safe_payload({"odd": Decimal("1.25")})
    assert json.loads(_dumps(payload))["odd"] == "1.25"


def test_keys_are_preserved():
    payload = _json_safe_payload({"shares": np.int64(1), "filed_on": date(2026, 1, 1)})
    assert set(payload) == {"shares", "filed_on"}
