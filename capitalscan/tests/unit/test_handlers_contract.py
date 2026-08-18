"""Session 15's gate, expressed as structure rather than as review.

Six properties, each of which a careful reviewer would check by reading and
each of which a careless commit would break silently:

1. Every result type is a frozen dataclass.
2. No result type states a probability without `n_eff`, an interval, and a
   q-value in the same object (invariant 8).
3. `RESULT_TYPES` covers every result dataclass in `handlers/types.py`, so
   adding a type and forgetting the registry fails here rather than
   exempting the new type from rule 2.
4. No `handlers/` module imports `rich`, an HTTP framework, or a client.
5. Every one of the seven handlers routes its return through the validator.
6. `split='holdout'` raises on every handler that takes a split.

Rule 5 is checked against the source text, not by calling. A handler that
validates on the happy path and returns bare from an early branch would pass
a behavioural test on the one input the test happened to pick.
"""

from __future__ import annotations

import ast
import inspect
import pkgutil
from dataclasses import fields, is_dataclass
from datetime import date

import pytest

from capitalscan import handlers
from capitalscan.handlers import _db, enums, types
from capitalscan.handlers.errors import HoldoutRequested
from capitalscan.handlers.types import COMPANION_FIELDS, RESULT_TYPES, is_probability_field

# Anything whose presence in this layer means the layer has grown a job it
# should not have. `pandas` is absent for a different reason and is not
# listed: it is not forbidden, it is simply the wrong shape for one row at a
# time (see `_db.rows`).
FORBIDDEN_IMPORTS = {
    "rich",
    "fastapi",
    "starlette",
    "flask",
    "django",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "typer",
    "click",
}


def _handler_modules():
    package = handlers
    for info in pkgutil.iter_modules(package.__path__):
        yield __import__(f"capitalscan.handlers.{info.name}", fromlist=["_"])


# ---------------------------------------------------------------------------
# 1-3. The result types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", RESULT_TYPES, ids=lambda c: c.__name__)
def test_every_result_type_is_a_frozen_dataclass(cls):
    assert is_dataclass(cls)
    assert cls.__dataclass_params__.frozen, f"{cls.__name__} is mutable"


@pytest.mark.parametrize("cls", RESULT_TYPES, ids=lambda c: c.__name__)
def test_no_result_type_states_a_probability_without_its_companions(cls):
    """Invariant 8, read off the annotations.

    The check is on the *type*, not on an instance, so it holds for every
    value the type can ever carry - including the ones no test constructs.
    """
    names = {f.name for f in fields(cls)}
    stated = sorted(n for n in names if is_probability_field(n))
    if not stated:
        return
    missing = [c for c in COMPANION_FIELDS if c not in names]
    assert not missing, (
        f"{cls.__name__} declares {stated} but has no {missing}. "
        "Invariant 8: every response carrying a probability carries n_eff "
        "and a confidence interval."
    )


def test_result_types_registry_covers_every_result_dataclass():
    """A type absent from `RESULT_TYPES` is a type nobody checks.

    Without this, adding a `Forecast` dataclass with a bare `p_touch_3` and
    forgetting the tuple would leave the invariant-8 test green while the
    violation shipped.
    """
    declared = {
        obj
        for name, obj in vars(types).items()
        if is_dataclass(obj) and isinstance(obj, type) and obj.__module__ == types.__name__
    }
    assert declared == set(RESULT_TYPES), (
        "handlers.types declares result dataclasses missing from RESULT_TYPES: "
        f"{sorted(c.__name__ for c in declared - set(RESULT_TYPES))}"
    )


def test_suppressed_carries_no_probability_field_at_all():
    """Not "nulled" - absent. A greyed-out rate still gets read."""
    assert not [f.name for f in fields(types.Suppressed) if is_probability_field(f.name)]


def test_the_probability_rule_does_not_capture_p_values():
    """A p-value has no interval, and requiring one would recurse."""
    assert is_probability_field("p_hit")
    assert is_probability_field("edge")
    assert is_probability_field("baseline")
    assert not is_probability_field("p_value_randomization")
    assert not is_probability_field("q_value")


# ---------------------------------------------------------------------------
# 4. What the layer may not import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module", list(_handler_modules()), ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_no_handler_module_imports_display_or_http(module):
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    offenders = imported & FORBIDDEN_IMPORTS
    assert not offenders, (
        f"{module.__name__} imports {sorted(offenders)}. Handlers return typed "
        "results; formatting and transport belong to their consumers."
    )


# ---------------------------------------------------------------------------
# 5. Every handler validates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(handlers.SEVEN_TOOLS))
def test_every_handler_returns_through_the_validator(name):
    """`return validated(...)`, in the source, on every return path.

    Reading the source rather than calling the function: a handler that
    validates its main return and falls out of an early branch bare would
    pass any test whose fixture did not hit that branch, and the early
    branches are exactly where `Suppressed` and `NotFound` come from.
    """
    fn = handlers.SEVEN_TOOLS[name]
    tree = ast.parse(inspect.getsource(fn))
    returns = [
        node for node in ast.walk(tree) if isinstance(node, ast.Return) and node.value is not None
    ]
    assert returns, f"{name} has no value-returning statement"
    for node in returns:
        call = node.value
        assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "validated", (
            f"{name} has a return that does not go through validated(): "
            f"line {node.lineno} of the function"
        )


def test_there_are_exactly_seven_tools():
    """ADR 074 fixes the count. An eighth is a design decision.

    Tool schemas dominate input tokens on the MCP and chat surfaces, so the
    count is a cost decision as well as a correctness one.
    """
    assert len(handlers.SEVEN_TOOLS) == 7
    assert set(handlers.SEVEN_TOOLS) == {
        "screen_signals",
        "get_stats",
        "get_indicators",
        "get_events",
        "predict",
        "explain_signal",
        "get_universe",
    }


# ---------------------------------------------------------------------------
# 6. Holdout
# ---------------------------------------------------------------------------


def _split_taking_handlers():
    return [
        name
        for name, fn in handlers.SEVEN_TOOLS.items()
        if "split" in inspect.signature(fn).parameters
    ]


def test_at_least_the_expected_handlers_take_a_split():
    """Guards the loop below against passing by finding nothing."""
    assert set(_split_taking_handlers()) >= {"get_stats", "get_events", "explain_signal"}


@pytest.mark.parametrize("name", sorted(_split_taking_handlers()))
def test_holdout_raises_on_every_handler_that_takes_a_split(name, fake_db, monkeypatch):
    """`split='holdout'` is refused before any query runs.

    `test_holdout_firewall.py` guards the database. Nothing guarded a
    serving layer that could ask for it, and Phase 5 is the first layer that
    could. The refusal is at the argument, not at the row filter: a filter
    can be widened by a later predicate, and an argument check cannot.
    """
    monkeypatch.setattr(_db, "engine_or_default", lambda engine: engine or object())
    fn = handlers.SEVEN_TOOLS[name]
    kwargs = {"split": "holdout"}
    params = inspect.signature(fn).parameters
    if "signal_type" in params:
        kwargs["signal_type"] = "confluence_low"
    if "target_pct" in params:
        kwargs["target_pct"] = 0.03
    if "dd_bucket" in params:
        kwargs["dd_bucket"] = "0-10"
    if "ticker" in params:
        kwargs["ticker"] = "TSM"
    if "date_" in params:
        kwargs["date_"] = date(2026, 8, 14)

    with pytest.raises(HoldoutRequested) as exc:
        fn(**kwargs)
    assert "evaluated exactly once" in str(exc.value)


def test_holdout_is_absent_from_the_splits_tuple_itself():
    """Not only rejected as input - absent from the enumerable domain.

    A consumer iterating "every split" to build a tab bar must not be able
    to reach it by accident, which a rejection-only guard would allow.
    """
    assert enums.HOLDOUT not in enums.SPLITS
    assert set(enums.SPLITS) == {"train", "validate"}


def test_split_bounds_refuses_holdout_too():
    """The date bounds are a side door, and it is shut."""
    with pytest.raises(HoldoutRequested):
        enums.split_bounds("holdout")
