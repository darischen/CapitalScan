"""The seven tools (ADR 074), and the only layer allowed to query for them.

Three consumers call this package: the MCP server (session 16), the web
routes (session 17), and the chat layer (session 18). ADR 027 requires the
MCP server to wrap "the same tools", which only means something if there is
one implementation to wrap. This is it.

    screen_signals   what fired, event feed by default (ADR 114)
    get_stats        one cell, or the stored reason it reports nothing
    get_indicators   a daily series for one ticker
    get_events       one ticker's history, cluster heads by default
    predict          NotFound, for every input, until Phase 6
    explain_signal   features and the cell, no attribution
    get_universe     membership and the criteria that decided it

Four properties hold across all seven, each enforced by a test rather than
by convention:

1. **No probability leaves without `n_eff`, an interval, and a q-value.**
   `handlers/validate.py` refuses; it never repairs.
2. **`split='holdout'` raises.** Everywhere, unconditionally.
3. **No handler imports `rich`, `fastapi`, or any HTTP client.** A handler
   that formats has the wrong shape.
4. **Every result carries `meta`**, including empty ones, so a reader can
   tell "nothing fired" from "nothing was ingested".

`SEVEN_TOOLS` is the registry session 16 generates its schemas from. Adding
an eighth entry is a design decision, not a convenience - see that session's
"what will be tempting" list.
"""

from __future__ import annotations

from typing import Callable

from capitalscan.handlers.errors import (
    DateOutOfWindow,
    HandlerError,
    HoldoutRequested,
    InvalidEnum,
    NotConfigured,
    ResponseInvalid,
)
from capitalscan.handlers.events import get_events, last_fire
from capitalscan.handlers.explain import SignalNotFound, explain_signal
from capitalscan.handlers.indicators import get_indicators
from capitalscan.handlers.predict import predict
from capitalscan.handlers.screen import screen_signals
from capitalscan.handlers.stats import get_stats
from capitalscan.handlers.types import (
    CellStats,
    EventList,
    EventRow,
    Explanation,
    IndicatorPoint,
    IndicatorSeries,
    Meta,
    NotFound,
    Prediction,
    ScreenResult,
    ScreenRow,
    Suppressed,
    UniverseResult,
    UniverseRow,
)
from capitalscan.handlers.universe import get_universe

# `validated` only. Re-exporting the bare `validate` function would shadow
# the `capitalscan.handlers.validate` *module* on the package, so
# `from capitalscan.handlers import validate` would bind the function and
# `V._DISABLED` would be an AttributeError. Session 16 imports the function
# from its module directly.
from capitalscan.handlers.validate import validated

# Ordered as DESIGN §10.1 lists them. A dict rather than a list of names so
# session 16 can register without a `getattr` over a module, which would
# also pick up helpers.
SEVEN_TOOLS: dict[str, Callable[..., object]] = {
    "screen_signals": screen_signals,
    "get_stats": get_stats,
    "get_indicators": get_indicators,
    "get_events": get_events,
    "predict": predict,
    "explain_signal": explain_signal,
    "get_universe": get_universe,
}

__all__ = [
    "SEVEN_TOOLS",
    "CellStats",
    "DateOutOfWindow",
    "EventList",
    "EventRow",
    "Explanation",
    "HandlerError",
    "HoldoutRequested",
    "IndicatorPoint",
    "IndicatorSeries",
    "InvalidEnum",
    "Meta",
    "NotConfigured",
    "NotFound",
    "Prediction",
    "ResponseInvalid",
    "ScreenResult",
    "ScreenRow",
    "SignalNotFound",
    "Suppressed",
    "UniverseResult",
    "UniverseRow",
    "explain_signal",
    "get_events",
    "get_indicators",
    "get_stats",
    "get_universe",
    "last_fire",
    "predict",
    "screen_signals",
    "validated",
]
