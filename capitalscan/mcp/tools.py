"""The seven tools, as thin wrappers over the seven handlers (ADR 027).

**No query logic lives here, and a test enforces it**: no module under
`mcp/` may import `sqlalchemy` or `db_io` or construct SQL. Each function
below validates nothing, filters nothing, and aggregates nothing. It calls
one handler and serializes the result. If it ever needs to do more, the
handler contract was wrong and the fix belongs in session 15.

**Why wrappers at all**, rather than registering the handlers directly. Two
reasons, both about the wire rather than about the logic:

- The handlers take an `Engine` and `StatsParams` for testability. Those are
  not tool arguments, and a generated schema would advertise them.
- The handlers return frozen dataclasses. `mcp/serialize.py` decides how a
  union member appears on the wire, and that decision belongs in one place
  rather than in the SDK's introspection of thirteen result types.

**The enums are generated, never written.** The `*_Arg` aliases below are
built at import time from `handlers.enums`, which is built from
`core.types.SignalType` and `core.config.StatsParams`. Adding a signal type
upstream changes every tool schema with no edit under `mcp/` -
`test_mcp_schemas.py` asserts exactly that.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from capitalscan import handlers
from capitalscan.handlers import enums
from capitalscan.mcp.serialize import to_wire_dict

# The runtime/type-check split is load-bearing, not a workaround.
#
# At runtime these must be real `Literal` types, because that is what
# pydantic turns into a JSON Schema `enum` when the SDK introspects the
# signature - and a schema whose enum is generated is the only thing keeping
# the wire contract and the handler contract in agreement.
#
# A static checker cannot see through `Literal[some_function()]`, since
# `Literal` requires literal arguments by definition. Annotating them as
# `str` for the checker keeps `mypy` meaningful over the rest of this
# module instead of forcing a blanket ignore, and costs nothing: the
# handlers validate every one of these values again anyway, so the checker
# is not the thing preventing a bad value from reaching the database.
if TYPE_CHECKING:
    SignalTypeArg = str
    UniverseArg = str
    DdBucketArg = str
    EntryKindArg = str
    SplitArg = str
    IndicatorFieldArg = str
else:
    from capitalscan.handlers.indicators import ALL_FIELDS

    SignalTypeArg = Literal[enums.signal_types()]
    UniverseArg = Literal[enums.UNIVERSES]
    DdBucketArg = Literal[enums.dd_buckets()]
    EntryKindArg = Literal[enums.entry_kinds()]
    # Two members, and `holdout` is not one of them. The refusal is in the
    # handler; this makes the request unrepresentable a layer earlier, so a
    # client that reads the schema never composes it.
    SplitArg = Literal[enums.SPLITS]
    IndicatorFieldArg = Literal[ALL_FIELDS]


def screen_signals(
    date_: date | None = None,
    signal_types: list[SignalTypeArg] | None = None,
    universe: UniverseArg = "trade",
    dd_bucket: DdBucketArg | None = None,
    min_strength: int | None = None,
    limit: int | None = None,
    with_stats: bool = False,
) -> dict[str, Any]:
    """Events that fired on a date, newest first.

    Returns the event feed by default. Pass `with_stats=true` for the cell
    statistics behind each row; they arrive whole or as a suppression
    reason, never as a partial set. No cell has survived FDR correction on
    the current data (ADR 112), so every hit rate returned carries
    `survives_fdr: false` and a q-value near 1.
    """
    return to_wire_dict(
        handlers.screen_signals(
            date_=date_,
            signal_types=list(signal_types) if signal_types else None,
            universe=universe,
            dd_bucket=dd_bucket,
            min_strength=min_strength,
            limit=limit,
            with_stats=with_stats,
        )
    )


def get_stats(
    signal_type: SignalTypeArg,
    target_pct: float,
    dd_bucket: DdBucketArg,
    split: SplitArg,
    entry_kind: EntryKindArg = "next_open",
    horizon_days: int | None = None,
    era: str | None = None,
) -> dict[str, Any]:
    """Historical frequencies for one cell of the grid.

    `target_pct` is a **decimal fraction**, not a percentage: 0.03 is the
    3% target. Only the measured values are accepted and anything else is
    refused rather than rounded to the nearest one.

    Returns either a measured cell or a `"kind": "suppressed"` object
    carrying the reason it reports nothing. A suppressed cell is never
    replaced by a broader one. `split` accepts `train` and `validate`;
    holdout is refused, because it is evaluated exactly once at the end of
    the project.
    """
    return to_wire_dict(
        handlers.get_stats(
            signal_type=signal_type,
            target_pct=target_pct,
            dd_bucket=dd_bucket,
            split=split,
            entry_kind=entry_kind,
            horizon_days=horizon_days,
            era=era,
        )
    )


def get_indicators(
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    fields: list[IndicatorFieldArg] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """A daily indicator and price series for one ticker.

    Defaults to the chart set: close, the three Bollinger bands, both `%K`
    series, and volume. Capped at 200 points per call.
    """
    return to_wire_dict(
        handlers.get_indicators(
            ticker=ticker,
            start=start,
            end=end,
            fields=list(fields) if fields else None,
            limit=limit,
        )
    )


def get_events(
    ticker: str | None = None,
    start: date | None = None,
    end: date | None = None,
    signal_types: list[SignalTypeArg] | None = None,
    cluster_head_only: bool = True,
    entry_kind: EntryKindArg = "next_open",
    split: SplitArg | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Detected events, newest first, cluster heads only by default.

    A run of consecutive touches is one cluster and is counted once; pass
    `cluster_head_only=false` to see every member. Holdout events are never
    returned.
    """
    return to_wire_dict(
        handlers.get_events(
            ticker=ticker,
            start=start,
            end=end,
            signal_types=list(signal_types) if signal_types else None,
            cluster_head_only=cluster_head_only,
            entry_kind=entry_kind,
            split=split,
            limit=limit,
        )
    )


def predict(ticker: str, as_of: date | None = None) -> dict[str, Any]:
    """No model exists. Always returns `"kind": "not_found"` with the reason.

    Kept in the tool set so the contract is settled before Phase 6 builds
    the model, rather than negotiated across three clients afterwards. Use
    `get_stats` for historical frequencies.
    """
    return to_wire_dict(handlers.predict(ticker=ticker, as_of=as_of))


def explain_signal(
    ticker: str,
    date_: date,
    entry_kind: EntryKindArg = "next_open",
    split: SplitArg | None = None,
    target_pct: float | None = None,
) -> dict[str, Any]:
    """The stored state a signal fired on, and optionally its cell.

    `split` and `target_pct` select a cell together; pass both or neither.
    No feature attribution is returned, because no model exists.
    """
    return to_wire_dict(
        handlers.explain_signal(
            ticker=ticker,
            date_=date_,
            entry_kind=entry_kind,
            split=split,
            target_pct=target_pct,
        )
    )


def get_universe(as_of: date | None = None, universe: UniverseArg | None = None) -> dict[str, Any]:
    """Universe membership and the per-criterion pass/fail behind it.

    Omit `as_of` for current membership; pass a date for the membership in
    force then, which is the last rebalance at or before it.
    """
    return to_wire_dict(handlers.get_universe(as_of=as_of, universe=universe))


# The measured targets, appended rather than written, for the same reason
# the enums are generated: invariant 9 keeps the numbers in
# `core/config.py`, and a docstring spelling them out would be a copy that
# stops being true the first time the sweep changes.
#
# **Worth the two lines.** Without the values in the description, a model
# asked about "the 3% target" sends `target_pct=3`, the handler refuses it,
# and the model retries in decimals. Measured on the first real question
# through `/chat`: six of an eight-call tool budget spent discovering the
# units, leaving two for the actual question.
get_stats.__doc__ = (get_stats.__doc__ or "") + (
    "\n    Measured targets: " + ", ".join(str(t) for t in enums.reach_targets()) + ".\n"
)

# Ordered as DESIGN §10.1 lists them, and keyed by the same names
# `handlers.SEVEN_TOOLS` uses. `test_mcp_schemas.py` asserts the two
# registries have identical keys, so a tool added to one and not the other
# fails rather than half-existing.
TOOLS: dict[str, Any] = {
    "screen_signals": screen_signals,
    "get_stats": get_stats,
    "get_indicators": get_indicators,
    "get_events": get_events,
    "predict": predict,
    "explain_signal": explain_signal,
    "get_universe": get_universe,
}
