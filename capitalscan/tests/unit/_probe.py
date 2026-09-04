"""`code_of`: source with docstrings and comments stripped, for probe tests.

**A substring probe that reads prose is not a probe.** This repo checks
design decisions by asserting that a name does or does not appear in a
function's source -- and the functions worth checking are exactly the ones
whose docstrings explain the name being searched for. Four tests have now
failed that way: `predictions`, `sort_quantiles` and `sync` all appear in
`research/neural.py`'s docstring saying why they are absent from its code,
and `SplitParams()` appears in `run_calendar`'s docstring saying why it is
no longer called.

The alternative -- choosing substrings no docstring would ever contain --
means writing worse documentation to satisfy a test. Stripping the prose
is the fix, and it lives here so there is one implementation rather than a
copy in every file that needs it.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any


def code_of(obj: Any) -> str:
    """Return `obj`'s source as code only: no docstrings, no comments.

    Comments never survive `ast.parse`, so they are dropped for free.
    Docstrings are real string expressions and have to be removed
    deliberately, from the module and from every class and function in it.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))
