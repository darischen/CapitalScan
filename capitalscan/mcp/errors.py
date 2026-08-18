"""Handler exceptions to protocol errors, with nothing leaking (16.3).

Two jobs, and they pull in opposite directions.

**Say enough.** An invalid enum names the valid values; an out-of-window
date names the window. A caller who gets "invalid input" spends a round trip
finding out what was expected, and the handler already computed the answer.

**Say nothing else.** No SQL, no table names, no file paths, no connection
strings, no traceback, and never the bearer token. A protocol error is
delivered to a client that may not be trusted, and the shape of a database
is information.

The two are reconciled by a **allowlist, not a scrubber.** Every message
that reaches the wire is either one this layer composed from the exception's
own class and a known-safe field, or a fixed string. A regex that stripped
`SELECT` from an arbitrary message would pass anything it had not been
taught about, and the first leak would be a column name nobody thought to
add to the pattern.
"""

from __future__ import annotations

from capitalscan.handlers.errors import (
    DateOutOfWindow,
    HandlerError,
    HoldoutRequested,
    InvalidEnum,
    NotConfigured,
    ResponseInvalid,
)
from capitalscan.handlers.explain import SignalNotFound

# Stable, client-facing codes. A client branches on these; the human text
# beside them is for a person. Adding one is a wire change.
CODE_INVALID_INPUT = "invalid_input"
CODE_HOLDOUT_REFUSED = "holdout_refused"
CODE_DATE_OUT_OF_WINDOW = "date_out_of_window"
CODE_NOT_FOUND = "not_found"
CODE_NOT_CONFIGURED = "not_configured"
CODE_INTERNAL = "internal_error"

# Ordered most specific first. `HoldoutRequested` subclasses `InvalidEnum`,
# so a dict keyed by type with an `isinstance` walk would resolve it to
# whichever key came first in iteration order - a bug that would only show
# up as holdout refusals reported as ordinary enum errors, which is
# precisely the one this system most wants to see distinctly.
_MAPPING: tuple[tuple[type, str], ...] = (
    (HoldoutRequested, CODE_HOLDOUT_REFUSED),
    (DateOutOfWindow, CODE_DATE_OUT_OF_WINDOW),
    (InvalidEnum, CODE_INVALID_INPUT),
    (SignalNotFound, CODE_NOT_FOUND),
    (NotConfigured, CODE_NOT_CONFIGURED),
    (ResponseInvalid, CODE_INTERNAL),
)

# What a caller is told when something outside the handler contract broke.
# Deliberately uninformative: an unexpected exception's message is the one
# place a table name, a file path, or a driver string is most likely to be,
# and by definition nobody has checked it.
INTERNAL_MESSAGE = (
    "The server failed to complete this request. The failure has been "
    "logged server-side; no further detail is available to the client."
)


class ToolError(Exception):
    """A protocol-level error with a stable code and a safe message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_wire(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


def _message_for(exc: HandlerError) -> str:
    """The exception's own message, which this layer composed.

    Safe because every `HandlerError` message in `handlers/` is built from
    the caller's arguments and the enum domains - never from a driver, a
    query, or a row. `test_mcp_errors.py` asserts that by constructing each
    one and checking the text, so the safety is measured rather than
    assumed.
    """
    return str(exc)


def to_tool_error(exc: BaseException) -> ToolError:
    """Map any exception to a `ToolError`. Never raises, never leaks.

    Anything that is not a `HandlerError` is a defect in this system rather
    than a problem with the request, and is reported as `internal_error`
    with a fixed string.
    """
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, HandlerError):
        for exc_type, code in _MAPPING:
            if isinstance(exc, exc_type):
                return ToolError(code, _message_for(exc))
        return ToolError(CODE_INTERNAL, INTERNAL_MESSAGE)
    return ToolError(CODE_INTERNAL, INTERNAL_MESSAGE)
