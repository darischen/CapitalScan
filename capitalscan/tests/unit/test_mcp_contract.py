"""ADR 027: the MCP server wraps *the same tools* and adds no query logic.

That sentence is the whole session, and it is the kind of rule that decays
by one convenient exception at a time. Four structural assertions hold it:

1. No module under `mcp/` imports `sqlalchemy`, `db_io`, or a driver.
2. Each wrapper calls exactly one handler, and serializes.
3. The tool registry matches `handlers.SEVEN_TOOLS` key for key.
4. Every schema enum equals its source, and no signal type, bucket label, or
   split name is spelled as a string literal anywhere under `mcp/`.

The fourth is the one that makes "generated, not hand-written" checkable.
A schema that happens to match today is not a generated schema; a schema
that cannot contain a hand-typed value is.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pkgutil

import pytest

from capitalscan import handlers
from capitalscan.core.types import EntryKind, SignalType
from capitalscan.handlers import enums
from capitalscan.mcp import serialize, tools
from capitalscan.mcp.server import build_mcp_server

# Anything that would mean this layer had grown a database of its own.
FORBIDDEN_IMPORTS = {"sqlalchemy", "psycopg", "pandas", "alembic"}
FORBIDDEN_NAMES = {"db_io"}


def _mcp_modules():
    import capitalscan.mcp as package

    for info in pkgutil.iter_modules(package.__path__):
        yield __import__(f"capitalscan.mcp.{info.name}", fromlist=["_"])


@pytest.fixture(scope="module")
def schemas():
    server = build_mcp_server()
    listed = asyncio.run(server.list_tools())
    return {tool.name: tool.input_schema for tool in listed}


# ---------------------------------------------------------------------------
# 1. No database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module", list(_mcp_modules()), ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_no_mcp_module_reaches_the_database(module):
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
            imported.update(alias.name for alias in node.names)
    offenders = (imported & FORBIDDEN_IMPORTS) | (imported & FORBIDDEN_NAMES)
    assert not offenders, (
        f"{module.__name__} imports {sorted(offenders)}. The MCP layer calls "
        "handlers and serializes; a query here has bypassed the validator."
    )


# A statement shape rather than a keyword. `FROM` alone matches the word
# "from" in any sentence, and a test that fires on prose gets weakened until
# it fires on nothing.
_SQL_SHAPES = (
    ("select", "from"),
    ("insert", "into"),
    ("update", "set"),
    ("delete", "from"),
)


@pytest.mark.parametrize(
    "module", list(_mcp_modules()), ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_no_mcp_module_constructs_sql(module):
    """A *string literal* shaped like a query, in a layer that has no database.

    Catches what the import test cannot: SQL text built here and handed to
    a connection obtained some other way. Scoped to string constants in the
    AST, so a docstring explaining why there is no SQL does not trip it.
    """
    tree = ast.parse(inspect.getsource(module))

    # Docstring *nodes*, by identity. `ast.get_docstring` returns the
    # cleaned text (dedented via `inspect.cleandoc`), which is not the raw
    # `Constant.value` it came from - comparing the two by string silently
    # matches nothing, and every docstring gets scanned as if it were code.
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_nodes.add(id(first.value))

    literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]
    for node in literals:
        text = node.value
        low = text.lower()
        for first, second in _SQL_SHAPES:
            assert not (first in low and second in low), (
                f"{module.__name__} holds a string shaped like SQL: {text[:80]!r}"
            )


# ---------------------------------------------------------------------------
# 2. Each wrapper is a wrapper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(tools.TOOLS))
def test_each_tool_calls_exactly_one_handler_and_serializes(name):
    """One `handlers.<x>(...)` call, wrapped in one `to_wire_dict(...)`.

    A second handler call would be a tool that combines two, which saves a
    round trip and puts query logic in the wrong layer. A branch would be
    filtering. Both are visible here and nowhere else, because the result
    of either still looks like a valid tool response.
    """
    tree = ast.parse(inspect.getsource(tools.TOOLS[name]))
    handler_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and getattr(node.func.value, "id", "") == "handlers"
    ]
    assert len(handler_calls) == 1, (
        f"{name} makes {len(handler_calls)} handler calls; a tool that "
        "combines two belongs in the handler layer (ADR 027)"
    )
    assert handler_calls[0].func.attr == name, (
        f"{name} calls handlers.{handler_calls[0].func.attr}, not its namesake"
    )

    wire_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "to_wire_dict"
    ]
    assert len(wire_calls) == 1, f"{name} does not serialize exactly once"


@pytest.mark.parametrize("name", sorted(tools.TOOLS))
def test_each_tool_has_one_return_and_no_control_flow(name):
    """One `return to_wire_dict(handlers.<name>(...))`, and no statements around it.

    Argument shaping with a ternary (`list(x) if x else None`) is fine and
    unavoidable - `None` and `[]` mean different things to the handler.
    What is excluded is *statement-level* control flow: an `if`, a loop, or
    a second return. Any of those is filtering or combining, which the
    handler already did or deliberately did not, and the result of either
    still looks like a valid tool response from outside.
    """
    tree = ast.parse(inspect.getsource(tools.TOOLS[name]))
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)

    statements = [
        node
        for node in ast.walk(fn)
        if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With))
    ]
    assert not statements, f"{name} contains control flow: {[type(s).__name__ for s in statements]}"

    returns = [node for node in ast.walk(fn) if isinstance(node, ast.Return)]
    assert len(returns) == 1, f"{name} has {len(returns)} returns"
    call = returns[0].value
    assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "to_wire_dict"


def test_the_two_registries_have_the_same_keys():
    """A tool in one and not the other is a tool that half exists."""
    assert set(tools.TOOLS) == set(handlers.SEVEN_TOOLS)
    assert len(tools.TOOLS) == 7


def test_the_server_registers_exactly_seven_tools(schemas):
    assert set(schemas) == set(handlers.SEVEN_TOOLS)


# ---------------------------------------------------------------------------
# 3. Schemas are generated
# ---------------------------------------------------------------------------


def _enum_of(schema: dict, field: str) -> list[str]:
    prop = schema["properties"][field]
    if "enum" in prop:
        return list(prop["enum"])
    # An optional enum arrives as anyOf[{enum...}, {null}].
    for branch in prop.get("anyOf", []):
        if "enum" in branch:
            return list(branch["enum"])
        if branch.get("type") == "array":
            return list(branch["items"]["enum"])
    if prop.get("type") == "array":
        return list(prop["items"]["enum"])
    raise AssertionError(f"{field} carries no enum: {prop}")


def test_the_signal_type_enum_matches_core_types(schemas):
    listed = _enum_of(schemas["get_stats"], "signal_type")
    assert listed == [m.value for m in SignalType]


def test_the_entry_kind_enum_matches_core_types(schemas):
    assert _enum_of(schemas["get_stats"], "entry_kind") == [m.value for m in EntryKind]


def test_the_dd_bucket_enum_matches_stats_params(schemas):
    assert _enum_of(schemas["get_stats"], "dd_bucket") == list(enums.dd_buckets())


def test_the_split_enum_has_two_members_and_no_holdout(schemas):
    """The refusal is in the handler; this makes it unrepresentable earlier.

    A client that reads the schema never composes a holdout request, so the
    handler's raise becomes the second line of defence rather than the only
    one.
    """
    listed = _enum_of(schemas["get_stats"], "split")
    assert listed == list(enums.SPLITS)
    assert "holdout" not in listed


def test_the_list_valued_enums_carry_their_domain(schemas):
    assert _enum_of(schemas["screen_signals"], "signal_types") == [m.value for m in SignalType]
    assert _enum_of(schemas["get_events"], "signal_types") == [m.value for m in SignalType]


def test_no_enum_value_is_spelled_as_a_literal_anywhere_under_mcp():
    """What makes "generated" checkable rather than coincidental.

    A schema that happens to match today is not generated. A layer that
    contains no signal-type string cannot have a hand-written one, so
    adding a member upstream changes the schema with no edit here - which
    is 16.1's acceptance criterion, stated as a property of the source.

    `entry_kind` defaults are excluded: `"next_open"` appears as a *default
    argument value*, which is a choice this layer makes rather than a copy
    of the domain.
    """
    spelled = set(enums.signal_types()) | set(enums.dd_buckets()) | {"holdout"}
    for module in _mcp_modules():
        source = inspect.getsource(module)
        for value in spelled:
            # In prose it is fine and often necessary; in code it is a copy.
            code = "\n".join(
                line for line in source.splitlines() if not line.strip().startswith("#")
            )
            code = code.replace('"""', "\x00").split("\x00")
            code = "".join(code[::2])  # drop docstring bodies
            assert f'"{value}"' not in code and f"'{value}'" not in code, (
                f"{module.__name__} spells {value!r} in code. The enums are "
                "generated from handlers.enums; a literal here drifts."
            )


# ---------------------------------------------------------------------------
# 4. Descriptions carry ADR 112
# ---------------------------------------------------------------------------


def test_the_server_instructions_state_the_negative_result():
    """Phase 5 gate item 8: ADR 112's result visible on every surface that
    reports a statistic. A client that never opens the docs still gets the
    instructions."""
    from capitalscan.mcp.server import INSTRUCTIONS

    assert "no cell survived" in INSTRUCTIONS.lower()
    assert "0.706" in INSTRUCTIONS
    assert "holdout" in INSTRUCTIONS.lower()


def test_the_screener_tool_says_the_default_is_the_feed():
    assert "with_stats" in (tools.screen_signals.__doc__ or "")


def test_predict_says_no_model_exists():
    doc = (tools.predict.__doc__ or "").lower()
    assert "no model exists" in doc


def test_serialize_exposes_the_union_tag():
    """Session 16 gate item 7: `Suppressed` distinguishable on the wire."""
    assert serialize.KIND_FIELD == "kind"
