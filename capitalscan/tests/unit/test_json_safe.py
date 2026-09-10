"""`json_safe` must walk containers, not stringify them.

**This file exists because the absence of one branch cost two weeks of
reversal display.** `json_safe` ended in `return str(value)`, so a nested
dict was stored as `"{'confirmed': False}"` -- a Python repr, which is
legal JSONB (it is a string) and which `->> 'confirmed'` reads as NULL
rather than an error.

The tests that existed asserted the key was present. A repr string
satisfies that. So these assert on *type* and on *readback*, which is what
the view actually depends on.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from capitalscan.jobs.db_io import json_safe as _json_safe
from capitalscan.jobs.db_io import json_safe_payload


def json_safe(value: object) -> Any:
    """`json_safe` is typed `-> object`, which is correct and unindexable.

    Every assertion here reaches into the result, so the cast happens once
    at the top rather than at forty call sites. Narrowing it to `Any` in the
    test does not weaken the production signature.
    """
    return _json_safe(value)


class TestNestingSurvives:
    def test_a_nested_dict_stays_a_dict(self) -> None:
        """The exact shape of the reversal bug."""
        out = json_safe({"bear_reversal": {"confirmed": False, "band_gap": -24.049838}})
        assert isinstance(out, dict)
        inner = out["bear_reversal"]
        assert isinstance(inner, dict), f"nested dict was stringified: {inner!r}"
        assert inner["confirmed"] is False
        assert inner["band_gap"] == pytest.approx(-24.049838)

    def test_a_nested_dict_is_never_a_repr_string(self) -> None:
        """Named separately because this is the signature to grep for.

        `"{'a': 1}"` is what the broken rows hold, and it is recognisable
        on sight: single quotes, capitalised booleans.
        """
        out = json_safe_payload({"bands": {"bb_lower": 10.0, "above": True}})
        assert not isinstance(out["bands"], str)
        assert "'" not in json.dumps(out), "single quotes mean a Python repr leaked in"
        assert "True" not in json.dumps(out), "capital True is a repr, not JSON"

    def test_it_recurses_past_the_second_level(self) -> None:
        """One level of recursion would have passed the tests above."""
        out = json_safe({"a": {"b": {"c": {"d": np.float64(1.5)}}}})
        assert out == {"a": {"b": {"c": {"d": 1.5}}}}
        assert isinstance(out["a"]["b"]["c"]["d"], float)

    def test_a_list_of_dicts_survives(self) -> None:
        out = json_safe([{"x": np.int64(1)}, {"x": np.int64(2)}])
        assert out == [{"x": 1}, {"x": 2}]
        assert all(isinstance(d, dict) for d in out)

    def test_a_tuple_becomes_a_list_not_a_string(self) -> None:
        assert json_safe((1, 2, 3)) == [1, 2, 3]


class TestCoercionStillWorksInsideContainers:
    """The scalar rules were correct. They just never reached nested values."""

    def test_numpy_scalars_coerce_at_depth(self) -> None:
        out = json_safe({"o": {"i": np.int64(3), "f": np.float64(1.5), "b": np.bool_(True)}})
        inner = out["o"]
        assert inner == {"i": 3, "f": 1.5, "b": True}
        assert isinstance(inner["i"], int) and not isinstance(inner["i"], np.integer)
        assert isinstance(inner["b"], bool)

    def test_a_nested_date_becomes_an_isoformat_string(self) -> None:
        out = json_safe({"o": {"d": date(2026, 9, 9), "t": datetime(2026, 9, 9, 13, 0)}})
        assert out["o"]["d"] == "2026-09-09"
        assert out["o"]["t"].startswith("2026-09-09T13:00")

    def test_nested_nan_and_inf_become_null(self) -> None:
        """`json.dumps` emits bare `NaN`, which Postgres rejects into JSONB.

        Nested values never reached that branch either, so a NaN band gap
        was stored as the string `"nan"` -- worse than the crash, because
        it reads back as a value.
        """
        out = json_safe({"o": {"n": float("nan"), "i": float("inf"), "ni": np.float64("nan")}})
        assert out["o"] == {"n": None, "i": None, "ni": None}

    def test_nested_nat_becomes_null_not_the_string_NaT(self) -> None:
        out = json_safe({"o": {"t": pd.NaT}})
        assert out["o"]["t"] is None


class TestTheResultIsActuallySerialisable:
    def test_a_full_state_json_round_trips_through_json_dumps(self) -> None:
        """The end-to-end property: whatever comes out, `json.dumps` takes.

        This is the guarantee the function's name makes, asserted on a
        payload shaped like a real `signal_reports.state_json`.
        """
        payload = {
            "live_price": np.float64(24.05),
            "day_open": np.float64(25.88),
            "bands": {"bb_lower": np.float64(24.1), "atr_14": np.float64(1.27)},
            "bear_reversal": {
                "above_band": np.bool_(True),
                "confirmed": np.bool_(False),
                "band_gap": np.float64(-24.049838),
                "open_gap_atr": np.float64(-0.14375),
            },
            "bull_reversal": {
                "below_band": np.bool_(False),
                "confirmed": np.bool_(False),
                "band_gap": float("nan"),
            },
            "asof": date(2026, 9, 9),
        }
        out = json_safe_payload(payload)
        text = json.dumps(out)  # must not raise
        back = json.loads(text)
        assert back["bear_reversal"]["confirmed"] is False
        assert back["bull_reversal"]["band_gap"] is None
        assert back["bands"]["atr_14"] == pytest.approx(1.27)

    def test_an_unknown_object_is_still_stringified(self) -> None:
        """The `str()` fallback stays -- it is only wrong for containers.

        An arbitrary object has no JSON representation, and a reject log
        that crashes is worse than one holding a repr. That was the
        original point of the function and it is unchanged.
        """

        class Odd:
            def __repr__(self) -> str:
                return "<odd>"

        assert json_safe(Odd()) == "<odd>"
