"""Closed enums and server-side limits (ADR 074, session 15.5).

Every value in this module is **derived**, never transcribed. `SignalType`
lives in `core/types.py`; the drawdown labels are computed by
`core.cells.dd_bucket_labels` from `StatsParams.dd_buckets`; `EntryKind` is
`core/types.py` again. Copying any of them here as string literals would
create the exact drift ADR 074 closes the enums to prevent: a new signal
type would be detectable, backtestable, and invisible to every tool.

`test_handlers_enums.py` asserts each set equals its source, so adding a
member upstream and forgetting this module fails the fast tier rather than
shipping a tool that silently rejects a real value.

**Splits are two, not three.** `holdout` is not a member and not an
accepted input. See `HoldoutRequested`.
"""

from __future__ import annotations

from datetime import date, timedelta

from capitalscan.core.cells import dd_bucket_labels
from capitalscan.core.config import SplitParams, StatsParams
from capitalscan.core.types import EntryKind, SignalType
from capitalscan.handlers.errors import DateOutOfWindow, HoldoutRequested, InvalidEnum

# ADR 074: "`limit` is capped server-side at 200 regardless of the value
# passed." Regardless is the operative word - a caller passing 10,000 gets
# 200 rows, not an error, because a hard failure on an over-large limit
# punishes a caller for asking politely for too much.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50

# The two splits a serving layer may read. `holdout` is deliberately absent
# from the tuple as well as from the accepted inputs: nothing should be able
# to iterate "all splits" and reach it.
SPLITS: tuple[str, ...] = ("train", "validate")
HOLDOUT = "holdout"

# `universe` selects which membership flag on `universe` / `v_universe` is
# read. ADR 001's two-universe split: `train` is the wide statistical
# population, `trade` the narrower live one.
UNIVERSES: tuple[str, ...] = ("train", "trade")


def signal_types() -> tuple[str, ...]:
    """Every signal type in `core.types.SignalType`, in declaration order.

    Deliberately the full domain rather than `SignalParams.
    enabled_signal_types` (ADR 108). A tool should accept a query about a
    type the current config happens not to emit and answer it with an empty
    result, because "this config does not produce that" and "that is not a
    thing" are different facts and only the second is an input error.
    """
    return tuple(member.value for member in SignalType)


def entry_kinds() -> tuple[str, ...]:
    return tuple(member.value for member in EntryKind)


def dd_buckets(sp: StatsParams | None = None) -> tuple[str, ...]:
    """Drawdown labels, computed from `StatsParams.dd_buckets`.

    `core.cells.dd_bucket_labels` is the one implementation. `compute.
    DD_BUCKETS` assigns the label onto an event; this reads the same edges
    back out. They agree because they share `StatsParams`, and
    `test_handlers_enums.py` asserts it rather than assuming.
    """
    return dd_bucket_labels(sp or StatsParams())


def _reject(name: str, value: object, allowed: tuple[str, ...]) -> None:
    raise InvalidEnum(f"{name}={value!r} is not valid. Allowed: {', '.join(allowed)}")


def _check_one(name: str, value: str, allowed: tuple[str, ...]) -> str:
    # Exact match only. A case-insensitive fallback would accept "LONG" for
    # "long" here and then be rejected by Postgres three layers down, where
    # the error names a constraint instead of an argument.
    if not isinstance(value, str) or value not in allowed:
        _reject(name, value, allowed)
    return value


def parse_split(value: str) -> str:
    """`train` or `validate`. `holdout` raises `HoldoutRequested`.

    The holdout branch comes first and is matched against the string
    directly rather than falling out of `SPLITS` membership. Both reject it;
    only this one tells the caller why, and the why is the part worth
    saying.
    """
    if value == HOLDOUT:
        raise HoldoutRequested(
            "split='holdout' is refused. Holdout is evaluated exactly once, "
            "at the end of the project (ADR 019, ADR 033), and no serving "
            f"surface may read it. Allowed: {', '.join(SPLITS)}"
        )
    return _check_one("split", value, SPLITS)


def parse_universe(value: str) -> str:
    return _check_one("universe", value, UNIVERSES)


def parse_entry_kind(value: str) -> str:
    return _check_one("entry_kind", value, entry_kinds())


def parse_dd_bucket(value: str, sp: StatsParams | None = None) -> str:
    return _check_one("dd_bucket", value, dd_buckets(sp))


def parse_signal_type(value: str) -> str:
    return _check_one("signal_type", value, signal_types())


def parse_signal_types(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    """A list of signal types, or None for "all".

    None and the empty list are **not** the same. None means the caller did
    not filter; an empty list means the caller filtered to nothing, which
    can only return zero rows and is far more likely to be a bug in their
    code than an intent. Raising on it costs nothing and catches it.
    """
    if values is None:
        return None
    if len(values) == 0:
        raise InvalidEnum(
            "signal_types=[] selects nothing. Pass None for all types, "
            f"or one or more of: {', '.join(signal_types())}"
        )
    return tuple(parse_signal_type(v) for v in values)


def clamp_limit(value: int | None) -> int:
    """ADR 074's server-side cap. Never raises.

    A non-positive limit is nonsense rather than an attack, so it clamps to
    1 rather than raising: a caller who passes `limit=0` from a loop counter
    gets one row and notices, instead of an exception in a place that has
    nothing to do with limits.
    """
    if value is None:
        return DEFAULT_LIMIT
    return max(1, min(int(value), MAX_LIMIT))


def check_date_window(value: date | None, first: date | None, last: date | None) -> None:
    """A date must sit inside the ingested bar window.

    `first` and `last` come from `bars`, not from `date.today()`. A database
    that stopped ingesting three weeks ago should reject a query for
    yesterday with a message naming the real window, rather than accepting
    it and returning nothing.
    """
    if value is None or first is None or last is None:
        return
    if value < first or value > last:
        raise DateOutOfWindow(
            f"{value} is outside the ingested window {first}..{last}. "
            "No bars exist for that date, so an empty result would be "
            "indistinguishable from a quiet session."
        )


def split_bounds(value: str, sp: SplitParams | None = None) -> tuple[date, date]:
    """The inclusive date bounds of a split.

    `jobs.config.split_key_for` read backwards, so a handler filtering on
    `split_key` can *also* bound the dates. That turns a single mislabelled
    row into an empty result rather than a leak - the same belt-and-braces
    `test_split_leakage.py` applies structurally.

    `holdout` raises here too, through `parse_split`, so this cannot become
    a side door to holdout's bounds.
    """
    parse_split(value)
    sp = sp or SplitParams()
    train_end = date.fromisoformat(sp.train_end)
    if value == "train":
        return date.fromisoformat(sp.event_start), train_end
    return train_end + timedelta(days=1), date.fromisoformat(sp.validate_end)


def side_for_signal_type(value: str) -> str:
    """Which side a signal type belongs to, from `core.cells`' two tuples.

    Derived, not mapped. `LONG_SIGNALS` and `SHORT_SIGNALS` are the grid's
    own definition of the pairing (ADR 102), and ADR 108 already broke the
    positional symmetry once - a second table here would have been the thing
    that broke.

    `get_stats` needs this because `cell_stats` is keyed by side and
    DESIGN §10.1's signature does not take one. Side is a property of the
    signal type, not an independent axis, so asking the caller for it would
    let them ask for a cell that cannot exist.
    """
    from capitalscan.core.cells import LONG_SIGNALS, SHORT_SIGNALS

    parse_signal_type(value)
    if value in LONG_SIGNALS:
        return "long"
    if value in SHORT_SIGNALS:
        return "short"
    raise InvalidEnum(
        f"signal_type={value!r} is a real type with no grid side. "
        "core.cells.LONG_SIGNALS and SHORT_SIGNALS must cover every "
        "SignalType member; add it to whichever it belongs to."
    )


def parse_target_pct(value: float, sp: StatsParams | None = None) -> float:
    """A reach target, checked against `StatsParams.reach_targets`.

    No default anywhere in this layer. `v_screen` pins 0.03 in SQL and
    DESIGN §11.2 quotes "up 3% within 5 sessions", but a default spelled
    here would be a fourth copy of that number outside `core/config.py`
    (invariant 9). Callers state the target they mean.
    """
    targets = tuple(float(t) for t in (sp or StatsParams()).reach_targets)
    if float(value) not in targets:
        raise InvalidEnum(
            f"target_pct={value!r} is not measured. Allowed: " + ", ".join(str(t) for t in targets)
        )
    return float(value)
