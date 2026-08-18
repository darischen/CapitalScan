"""Serialization and error mapping (session 16.3).

The easiest place to change an answer while looking like plumbing. A
rounded q-value, a `Suppressed` flattened into a `CellStats` with nulls, or
a dropped `staleness_days` all pass every test that only checks the happy
path, and all three change what the wire says the database contains.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from capitalscan.handlers.errors import (
    DateOutOfWindow,
    HandlerError,
    HoldoutRequested,
    InvalidEnum,
    NotConfigured,
    ResponseInvalid,
)
from capitalscan.handlers.explain import SignalNotFound
from capitalscan.handlers.types import Meta, NotFound, ScreenResult, Suppressed
from capitalscan.mcp import errors
from capitalscan.mcp.serialize import kind_of, to_wire
from capitalscan.tests.unit.test_handlers_validate import cell

META = Meta(
    config_hash="86e91448a65aa40b",
    as_of=date(2026, 8, 17),
    staleness_days=1,
    run_id="run-abc",
    split="train",
    stale=False,
)


# ---------------------------------------------------------------------------
# Gate item 7: Suppressed is distinguishable from CellStats on the wire
# ---------------------------------------------------------------------------


def test_a_suppressed_cell_carries_its_own_tag():
    payload = to_wire(
        Suppressed(
            cell_id="x",
            reason="n_eff 14.0 below min_n_eff 30",
            n_events=19,
            n_eff=14,
            min_n_eff=30,
            meta=META,
        )
    )
    assert kind_of(payload) == "suppressed"
    assert payload["reason"] == "n_eff 14.0 below min_n_eff 30"


def test_a_measured_cell_carries_no_tag():
    """`kind` marks the union members, not every result.

    Tagging everything would put a redundant label on the one object whose
    identity the tool name already gives, and grow every payload for
    nothing.
    """
    assert kind_of(to_wire(cell())) is None


def test_a_client_can_tell_suppressed_from_zero():
    """The distinction the whole tag exists for.

    A suppressed cell and a cell measuring `p_hit = 0.0` are opposite
    claims - "we cannot say" and "it never happened" - and they must not
    arrive as two objects that differ only by which keys are null.
    """
    suppressed = to_wire(
        Suppressed(cell_id="x", reason="too few", n_events=3, n_eff=2, min_n_eff=30, meta=META)
    )
    zero = to_wire(cell(p_hit=0.0, ci_low=0.0, ci_high=0.04, edge=-0.39))
    assert kind_of(suppressed) == "suppressed"
    assert kind_of(zero) is None
    assert "p_hit" not in suppressed
    assert zero["p_hit"] == 0.0


def test_not_found_carries_its_own_tag():
    payload = to_wire(NotFound(what="prediction for TSM", reason="no model", meta=META))
    assert kind_of(payload) == "not_found"


# ---------------------------------------------------------------------------
# Nothing is rounded
# ---------------------------------------------------------------------------


def test_a_q_value_keeps_every_stored_digit():
    """0.849 and 0.8492 are not the same statement.

    The first says "nowhere near significant". The second says that and how
    far. A `round(q, 3)` in this layer would lose the second silently, and
    no test that checked "is it about 0.85" would notice.
    """
    payload = to_wire(cell(q_value=0.849213))
    assert payload["q_value"] == 0.849213
    assert json.loads(json.dumps(payload))["q_value"] == 0.849213


@pytest.mark.parametrize("value", [0.123456, 0.000001, 0.999999, 51.234567])
def test_six_decimal_places_round_trip_through_json(value):
    """`numeric(12,6)` is the stored precision, and `json` writes floats
    with `repr`, which round-trips a double exactly."""
    assert json.loads(json.dumps(to_wire(cell(p_hit=None, mean_ret=value))))["mean_ret"] == value


# ---------------------------------------------------------------------------
# meta survives
# ---------------------------------------------------------------------------


def test_meta_survives_serialization_whole():
    """Gate item 8. A client cannot render a staleness banner it never
    receives."""
    # `cell()` carries its own bare Meta; this test is about the rich one.
    payload = to_wire(cell(meta=META))["meta"]
    assert payload["config_hash"] == "86e91448a65aa40b"
    assert payload["staleness_days"] == 1
    assert payload["as_of"] == "2026-08-17"
    assert payload["stale"] is False


def test_meta_reaches_the_client_on_an_empty_result():
    """The reader has to tell "nothing fired" from "nothing was ingested"."""
    payload = to_wire(ScreenResult(rows=(), total_matched=0, limit=50, with_stats=False, meta=META))
    assert payload["rows"] == []
    assert payload["meta"]["config_hash"] == "86e91448a65aa40b"


def test_dates_become_iso_strings():
    assert to_wire(date(2026, 8, 17)) == "2026-08-17"


def test_tuples_become_lists_so_json_can_hold_them():
    assert to_wire((1, 2, 3)) == [1, 2, 3]


def test_the_whole_result_is_json_serializable():
    empty = ScreenResult(rows=(), total_matched=0, limit=50, with_stats=True, meta=META)
    json.dumps(to_wire(empty))
    json.dumps(to_wire(cell()))


def test_an_unknown_type_is_not_silently_stringified():
    """It reaches `json.dumps` and raises there, naming the value.

    A `str()` fallback here would put a Python repr on the wire and call it
    data, which a client would then parse.
    """

    class Odd:
        pass

    with pytest.raises(TypeError):
        json.dumps(to_wire({"x": Odd()}))


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


CASES = [
    (HoldoutRequested("split='holdout' is refused"), errors.CODE_HOLDOUT_REFUSED),
    (DateOutOfWindow("1987-10-19 is outside the ingested window"), errors.CODE_DATE_OUT_OF_WINDOW),
    (InvalidEnum("dd_bucket='0-15' is not valid"), errors.CODE_INVALID_INPUT),
    (SignalNotFound("No event for TSM"), errors.CODE_NOT_FOUND),
    (NotConfigured("the GUC is unset"), errors.CODE_NOT_CONFIGURED),
    (ResponseInvalid("states p_hit with no n_eff"), errors.CODE_INTERNAL),
]


@pytest.mark.parametrize("exc,code", CASES, ids=lambda x: getattr(x, "__class__", type(x)).__name__)
def test_each_handler_exception_maps_to_a_distinct_code(exc, code):
    assert errors.to_tool_error(exc).code == code


def test_holdout_is_not_reported_as_an_ordinary_enum_error():
    """`HoldoutRequested` subclasses `InvalidEnum`.

    A dict keyed by type with an `isinstance` walk would resolve it to
    whichever key came first in iteration order, and the bug would surface
    as holdout refusals reported as generic input errors - the one refusal
    this system most wants to see distinctly.
    """
    assert errors.to_tool_error(HoldoutRequested("x")).code != errors.CODE_INVALID_INPUT


def test_the_codes_are_all_different():
    assert len({code for _, code in CASES}) == len({c for _, c in CASES})


def test_an_invalid_enum_names_the_valid_values():
    """A failure that does not say what was expected costs a round trip,
    and the handler already computed the answer."""
    from capitalscan.handlers import enums

    try:
        enums.parse_dd_bucket("0-15")
    except InvalidEnum as exc:
        message = errors.to_tool_error(exc).message
    assert "0-10" in message and "35+" in message


def test_an_out_of_window_date_names_the_window():
    from capitalscan.handlers import enums

    try:
        enums.check_date_window(date(1987, 10, 19), date(2010, 1, 4), date(2026, 8, 17))
    except DateOutOfWindow as exc:
        message = errors.to_tool_error(exc).message
    assert "2010-01-04..2026-08-17" in message


# ---------------------------------------------------------------------------
# Nothing leaks
# ---------------------------------------------------------------------------

LEAKS = (
    "SELECT",
    "INSERT",
    "cell_stats",
    "v_screen",
    "v_events",
    "indicators",
    "postgresql://",
    "psycopg",
    "Traceback",
    "capitalscan/handlers",
    "C:\\",
)


@pytest.mark.parametrize(
    "exc,_code", CASES, ids=lambda x: getattr(x, "__class__", type(x)).__name__
)
def test_no_mapped_message_contains_internal_detail(exc, _code):
    message = errors.to_tool_error(exc).message
    for leak in LEAKS:
        assert leak not in message, f"{leak!r} leaked into a protocol error"


def test_an_unexpected_exception_gets_a_fixed_message():
    """By definition nobody has checked its text.

    An unhandled exception's message is the likeliest place for a table
    name, a file path, or a driver string, so none of it reaches the wire.
    """
    leaky = RuntimeError("SELECT * FROM cell_stats WHERE config_hash = 'abc' -- C:\\secret")
    mapped = errors.to_tool_error(leaky)
    assert mapped.code == errors.CODE_INTERNAL
    assert mapped.message == errors.INTERNAL_MESSAGE
    assert "SELECT" not in mapped.message


def test_a_handler_error_subclass_nobody_mapped_falls_back_safely():
    """A new `HandlerError` added without a mapping entry must not leak.

    The fallback is `internal_error` with the fixed string, not the
    exception's own text - the safety of a message comes from this layer
    having composed it, and nobody composed that one.
    """

    class Novel(HandlerError):
        pass

    mapped = errors.to_tool_error(Novel("SELECT secret FROM cell_stats"))
    assert mapped.code == errors.CODE_INTERNAL
    assert mapped.message == errors.INTERNAL_MESSAGE


def test_a_tool_error_serializes_to_a_code_and_a_message():
    wire = errors.to_tool_error(InvalidEnum("bad")).to_wire()
    assert set(wire) == {"error", "message"}
    assert wire["error"] == errors.CODE_INVALID_INPUT


def test_the_bearer_token_never_reaches_an_error():
    """It is not in scope for any handler, so it cannot be - asserted so a
    later change that threaded it through fails here."""
    from capitalscan.mcp import auth

    token = "s3cr3t"
    for exc, _ in CASES:
        assert token not in errors.to_tool_error(exc).message
    assert token not in errors.INTERNAL_MESSAGE
    assert token not in json.dumps(auth.UNAUTHORIZED_BODY)
