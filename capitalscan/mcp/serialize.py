"""Typed handler results to JSON-safe dicts. No logic, no reshaping.

ADR 027 requires the MCP server to wrap "the same tools". This module is
where that is either true or quietly false, because serialization is the
easiest place to change an answer while looking like plumbing: round a
q-value here and the wire says something the handler did not.

Three rules:

- **`Suppressed` and `NotFound` serialize as their own shapes**, tagged with
  a `kind` field. A client must be able to tell "suppressed" from "zero" and
  from "no model", and three JSON objects that differ only by which keys are
  null cannot express that.
- **Nothing is rounded, ever.** `numeric(12,6)` reaches this module as a
  Python float, and `json` writes floats with `repr`, which round-trips a
  double exactly. A `round(q, 3)` anywhere here turns 0.8492 into 0.849 -
  the distinction between "nowhere near significant" and "nowhere near
  significant, and here is how far" - and nothing would ever flag it.
- **`meta` survives whole**, `staleness_days` included. DESIGN §11.2's
  staleness banner cannot render what the client never receives.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import Any

from capitalscan.handlers.types import NotFound, Suppressed

# The tag that makes a union member identifiable on the wire. `kind` rather
# than `type`, which collides with `signal_type` in a reader's eye, and
# rather than a bare presence check on `p_hit`, which is exactly the
# null-shaped ambiguity this exists to remove.
KIND_FIELD = "kind"

# Only the union members are tagged. Tagging every result would put a
# redundant `"kind": "ScreenResult"` on the one object whose identity the
# tool name already gives, and would grow the payload for nothing.
_TAGGED: dict[type, str] = {
    Suppressed: "suppressed",
    NotFound: "not_found",
}


def to_wire(obj: Any) -> Any:
    """Recursively convert a handler result to JSON-safe primitives.

    Dataclasses become dicts, tuples become lists, dates become ISO strings,
    `Decimal` becomes float. Everything else passes through, which is
    deliberate: an unexpected type reaches `json.dumps` and raises there,
    naming the value. Silently `str()`-ing it would put a Python repr on the
    wire and call it data.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        tag = _TAGGED.get(type(obj))
        if tag is not None:
            out[KIND_FIELD] = tag
        for f in fields(obj):
            out[f.name] = to_wire(getattr(obj, f.name))
        return out
    if isinstance(obj, (tuple, list)):
        return [to_wire(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): to_wire(v) for k, v in obj.items()}
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        # Should not arrive - the handlers convert on the way out - but a
        # column added later would, and a Decimal reaching `json.dumps`
        # raises with a message about the type rather than the field.
        return float(obj)
    return obj


def to_wire_dict(obj: Any) -> dict[str, Any]:
    """`to_wire` for a result object, typed as the dict it always produces.

    `to_wire` is recursive over anything, so its return type is `Any` and
    has to be. Every tool wrapper hands it a frozen dataclass, which always
    becomes a dict, and this is where that narrowing is stated once instead
    of being seven `cast` calls or seven ignored errors.
    """
    payload = to_wire(obj)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise TypeError(f"expected a dataclass result, got {type(obj).__name__}")
    return payload


def kind_of(payload: dict[str, Any]) -> str | None:
    """The union tag on a serialized result, or None for a plain one."""
    return payload.get(KIND_FIELD)
