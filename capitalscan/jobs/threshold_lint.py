"""ADR 092's matcher, replaced (Session 14 task 14.5).

ADR 092's original enforcement was `test_exits.py::test_exits_module_contains_
no_bare_stochastic_literal`: `assert "80.0" not in body and "20.0" not in
body` over `inspect.getsource(capitalscan.core.exits)`. ADR 095 recorded
exactly why that is not enough, three ways:

1. **One module.** `inspect.getsource` only ever sees `core/exits.py`.
2. **Two spellings.** `db/schema.sql` spells the long-exit threshold
   `(s.k_full >= (80)::numeric)` — no `"80.0"` substring, so the old
   assertion finds nothing even pointed straight at the file.
3. **No SQL at all.** ADR 095's own proposed fix ("widen the file list")
   does not work either, for the same reason: the string just isn't there.

This module replaces the substring search with a **pattern**: a numeric
literal sitting on one side of a comparison operator (`>=`, `<=`, `==`,
`!=`, `>`, `<`), with a threshold-bearing column identifier on the other
side, in Python or in checked-in SQL. That is what "bare literal" actually
means for this defect class — the exact spelling never mattered.

This module does filesystem IO (reads source files off disk), so it lives
in `jobs/`, not `core/` (invariant 1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Threshold-bearing columns, named once (ADR 092's task 14.5 rule: "named in
# one place, not scattered"). These are exactly the stochastic and Bollinger
# outputs `core/indicators.py` computes and `core/exits.py` / `core/signals.py`
# compare against a threshold. `%K`/`%D` are the DESIGN.md prose spelling of
# `k_full`/`k_fast`/`d_full`; SQL and Python both use the underscore names, so
# there is only one set of identifiers to match, not two.
THRESHOLD_COLUMNS: tuple[str, ...] = (
    "k_full",
    "k_fast",
    "d_full",
    "bb_upper",
    "bb_lower",
    "bb_mid",
)

# Comparison operators considered. Deliberately excludes bare `=` — that is
# a Python assignment (`ExitParams.exit_stoch_threshold: float = 80.0` is a
# *definition*, not a *use*, and must not be flagged) and SQL's `=` is
# handled separately below since SQL uses `=` for equality, not assignment.
_PY_OPERATORS = ("==", ">=", "<=", "!=", ">", "<")
_SQL_OPERATORS = ("=", ">=", "<=", "<>", "!=", ">", "<")

# A bare numeric literal, optionally wrapped in int(...)/float(...)/parens/
# a Postgres cast (`::numeric`). Captures the literal itself.
#
# Three explicit branches, each with its own lookbehind, rather than one
# pattern with every wrapper optional. A single pattern with optional
# leading parens lets the engine simply *skip* an unwanted paren and start
# matching at the bare digit instead — which is exactly what happened during
# development: `k_full.shift(1)` was misread as a wrapped-number use of
# `1`, because the lookbehind only ever inspected the character immediately
# before wherever the match happened to start, and the regex was free to
# start one character later, right past the `(` that would have failed it.
# Making the paren mandatory per-branch, with the lookbehind pinned to
# whatever character actually starts that branch's match, closes that gap:
# `shift(1)` fails branch b's lookbehind (`t` precedes the `(`), while
# `>= (80)` and `>= 80` both still pass (an operator or whitespace precedes
# them either way).
_NUMBER = r"\d+(?:\.\d+)?"
_WRAPPED_NUMBER = (
    "(?:"
    rf"(?:(?<![A-Za-z0-9_])(?:int|float)\(\s*({_NUMBER})\s*\))"  # int(80), float(80.0)
    rf"|(?:(?<![A-Za-z0-9_])\(\s*({_NUMBER})\s*\)(?:::\w+)?)"  # (80), (80)::numeric
    rf"|(?:(?<![A-Za-z0-9_(])({_NUMBER}))"  # bare 80, 80.0
    ")"
)

# Column identifier, optionally qualified (`s.k_full`, `ep.k_full`) or
# subscripted (`row["k_full"]`, `bar['k_full']`). `\b` on both sides of the
# name itself so `k_full` cannot match as a substring of some other
# identifier that happens to contain it.
_COLUMN = r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:\[[\"'])?\b({cols})\b(?:[\"']\])?"

# Files/directories excluded from every scan regardless of extension.
_EXCLUDED_PARTS = {".venv", "__pycache__", ".git", "node_modules"}

# This module itself, excluded from its own default scan roots — explicitly,
# not silently. Its docstring quotes ADR 095's exact offending SQL
# (`s.k_full >= (80)::numeric`) verbatim as the worked example, and
# `KNOWN_EXCEPTIONS` below stores that same text as literal string data for
# the exception-matching logic to compare against. Both are prose/data
# *about* the defect pattern, not a live comparison deciding an exit or a
# signal, so they are exactly what this matcher exists to leave alone — but
# they are indistinguishable from a real hit by source pattern alone, since
# the whole point of quoting them here is to reproduce the defect text
# exactly. Excluding the file by name is the honest fix; asking a future
# reader to keep the docstring worded around the linter's own regex would
# quietly rot the documentation instead.
_SELF_PATH = Path(__file__).resolve()

_ALLOWLIST_COMMENT = re.compile(r"ADR\s*\d+", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    """One reported literal-threshold-comparison. Never a bare boolean —
    `path`, `line_no`, and `text` are what makes a finding actionable."""

    path: Path
    line_no: int
    text: str
    column: str
    literal: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_no}: {self.text.strip()}"


def _column_pattern(operators: tuple[str, ...]) -> re.Pattern[str]:
    cols = "|".join(re.escape(c) for c in THRESHOLD_COLUMNS)
    column = _COLUMN.format(cols=cols)
    ops = "|".join(re.escape(op) for op in sorted(operators, key=len, reverse=True))
    # column <op> number, or number <op> column — either order.
    return re.compile(
        rf"(?:{column}\s*(?:{ops})\s*{_WRAPPED_NUMBER})"
        rf"|(?:{_WRAPPED_NUMBER}\s*(?:{ops})\s*{column})"
    )


_PY_PATTERN = _column_pattern(_PY_OPERATORS)
_SQL_PATTERN = _column_pattern(_SQL_OPERATORS)


def _is_allowlisted(lines: list[str], idx: int, lookback: int = 3) -> bool:
    """True when the matched line, or one of the immediately preceding
    comment lines, names the ADR that permits the literal.

    Allowlisting is by explicit annotation only — never by silence. A
    trailing `-- ADR 092: ...` on the same line, or a `# ADR 095: ...`
    comment on one of the lines directly above, both count. A comment
    three paragraphs away, or no comment at all, does not.
    """
    if _ALLOWLIST_COMMENT.search(lines[idx]):
        return True
    for back in range(1, lookback + 1):
        j = idx - back
        if j < 0:
            return False
        stripped = lines[j].strip()
        if not stripped:
            return False
        is_comment = stripped.startswith("#") or stripped.startswith("--")
        if not is_comment:
            return False
        if _ALLOWLIST_COMMENT.search(stripped):
            return True
    return False


def _scan_text(path: Path, text: str, pattern: re.Pattern[str]) -> list[Finding]:
    lines = text.splitlines()
    findings = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("--"):
            continue
        for match in pattern.finditer(line):
            groups = [g for g in match.groups() if g is not None]
            # One capture is the column name, the other the numeric literal;
            # which is which depends on which alternative matched. The
            # column name is always one of THRESHOLD_COLUMNS; the other
            # group is the literal.
            col = next((g for g in groups if g in THRESHOLD_COLUMNS), None)
            lit = next((g for g in groups if g != col), None)
            if col is None or lit is None:
                continue
            if _is_allowlisted(lines, i):
                continue
            findings.append(Finding(path=path, line_no=i + 1, text=line, column=col, literal=lit))
    return findings


def scan_python_source(text: str, path: Path = Path("<string>")) -> list[Finding]:
    """Scan Python source text for a bare numeric literal compared against
    a threshold-bearing column. Does not touch the filesystem."""
    return _scan_text(path, text, _PY_PATTERN)


def scan_sql_source(text: str, path: Path = Path("<string>")) -> list[Finding]:
    """Scan SQL source text for the same defect class. Does not touch the
    filesystem."""
    return _scan_text(path, text, _SQL_PATTERN)


def _iter_files(root: Path, suffix: str) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == suffix else []
    out = []
    for p in sorted(root.rglob(f"*{suffix}")):
        if any(part in _EXCLUDED_PARTS for part in p.parts):
            continue
        if p.resolve() == _SELF_PATH:
            continue
        out.append(p)
    return out


# Default scan roots. Production Python (`core/`, `jobs/`, `research/`) and
# every checked-in SQL surface (ADR 095: "extend ADR 092's source assertion
# to scan checked-in SQL (`db/schema.sql`, `db/migrations/`)"). Deliberately
# excludes `tests/`: test files assert computed values against fixture
# numbers (`assert row["k_full"] < 20`), which is verifying a result, not
# deciding an exit or a signal with a bare threshold — the defect class this
# matcher exists for is production logic disagreeing with `ExitParams`, not
# a test fixture.
DEFAULT_PYTHON_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "capitalscan" / "core",
    REPO_ROOT / "capitalscan" / "jobs",
    REPO_ROOT / "capitalscan" / "research",
)
DEFAULT_SQL_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "db" / "schema.sql",
    REPO_ROOT / "db" / "migrations",
)


# Explicit, named exceptions for findings that cannot carry an inline
# annotation. `db/schema.sql` is `pg_dump` output (see its own header,
# "PostgreSQL database dump") regenerated wholesale by a documented process
# and diffed for drift by `test_schema_matches_db` — a hand-written `--`
# comment inside a `CREATE VIEW` body does not survive that round trip
# (Postgres stores a view's parsed query, not its original comment text), so
# there is no line inside the file itself that could carry an annotation.
# This registry is the annotation instead: explicit, named, and reviewed in
# code review the same as any other source change — never silent.
#
# ADR 095's `v_positions` finding is exactly this case. ADR 095's own status
# line authorizes fixing it in a "Phase 5 rebuild of `v_positions`", and
# Session 14 §0 lists "anything touching the serving store" as explicitly
# out of scope. Fixing it here would be scope creep past what this session
# is authorized to touch; leaving it unlisted would be the silent allowlist
# the task forbids. Remove this entry the moment `v_positions` is rebuilt
# (Phase 5) — its continued presence past that point is itself a signal the
# rebuild missed a spot.
KnownException = tuple[str, str]  # (path suffix/substring, line substring)
KNOWN_EXCEPTIONS: tuple[KnownException, ...] = (
    # ADR 095: v_positions' long-exit stochastic threshold, baked into the
    # view as `(80)::numeric` instead of reading ExitParams.exit_stoch_
    # threshold. Deferred to the Phase 5 serving-layer rebuild.
    ("db/schema.sql", "s.k_full >= (80)::numeric"),
    # Same defect, same ADR, in the migration that first created the view.
    ("db/migrations/versions/6d86bf1f668e_views.py", "s.k_full >= 80"),
)


def _is_known_exception(finding: Finding) -> bool:
    posix = finding.path.as_posix()
    return any(
        path_part in posix and line_part in finding.text
        for path_part, line_part in KNOWN_EXCEPTIONS
    )


def scan_repo(
    python_roots: tuple[Path, ...] = DEFAULT_PYTHON_ROOTS,
    sql_roots: tuple[Path, ...] = DEFAULT_SQL_ROOTS,
    apply_known_exceptions: bool = True,
) -> list[Finding]:
    """Scan the repository as it stands. Reports file, line, and offending
    text for every non-allowlisted hit — never a bare boolean.

    `apply_known_exceptions=False` surfaces the `KNOWN_EXCEPTIONS` entries
    too, which is how the regression test proves the matcher still sees
    them rather than trusting the exception list blindly."""
    findings: list[Finding] = []
    for root in python_roots:
        for path in _iter_files(root, ".py"):
            findings.extend(scan_python_source(path.read_text(encoding="utf-8"), path))
    for root in sql_roots:
        # `db/schema.sql` is plain SQL. Alembic migrations are `.py` files
        # whose bodies are almost entirely `op.execute("""<SQL>""")` blocks
        # (`db/migrations/versions/*.py`) — ADR 095 names both as "checked-in
        # SQL" to scan, so migrations are scanned with the *SQL* operator set
        # (which includes bare `=`), not the Python one: a migration file's
        # own Python syntax (`revision: str = "..."`) never mentions a
        # threshold column, so there is nothing for the wider operator set to
        # false-positive on, and using the Python set here would silently
        # miss a `k_full = 80` equality spelled inside the embedded SQL.
        is_sql_file = root.suffix == ".sql"
        files = _iter_files(root, ".sql") if is_sql_file else _iter_files(root, ".py")
        scanner = scan_sql_source
        for path in files:
            findings.extend(scanner(path.read_text(encoding="utf-8"), path))
    if apply_known_exceptions:
        findings = [f for f in findings if not _is_known_exception(f)]
    return findings
