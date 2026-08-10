"""`db/schema.sql` must equal a live `pg_dump --schema-only`.

`tests/unit/test_holdout_firewall.py` reads that file as its source of
truth for invariant 5b, and its docstring asserts the file "is regenerated
after every migration". Nothing enforced that, and it drifted twice: once
before `b2e5d81a4c76`, and again when `c7e1a4f9d302` added the five
`events.peak_ret_*d` columns and nobody re-ran `cscan db schema`. A stale
dump means the firewall test is checking a schema that no longer exists,
which is worse than not checking one at all.

Process fix, not a one-off regeneration: this test is the enforcement.

It lives in the integration tier because it needs the running research
database, and it is read-only — `dump_schema()` shells out to `pg_dump
--schema-only` and this file never writes. It cannot damage production
data, so it is safe to run against the live instance despite the tier's
usual warning (CLAUDE.md).

Line endings are not compared: `Path.read_text` and `subprocess` text mode
both apply universal-newline translation, so both sides are `\\n` here
regardless of what sits on disk.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from capitalscan.jobs import db

SCHEMA = Path(__file__).resolve().parents[3] / "db" / "schema.sql"


def _dump_or_skip() -> str:
    try:
        return db.dump_schema()
    except RuntimeError as exc:
        pytest.skip(f"cannot reach the research database through pg_dump: {exc}")


def test_committed_schema_matches_a_live_dump():
    """Fails with the actual diff, so the fix is obvious from the output.

    If this fails, run `cscan db schema` and commit the result. If the diff
    is large and unrelated to your migration, check that the dump came from
    the container's `pg_dump` and not a different client version — see
    `db._DOCKER_FALLBACK`.
    """
    live = _dump_or_skip()
    committed = SCHEMA.read_text(encoding="utf-8")
    if committed == live:
        return
    diff = "\n".join(
        difflib.unified_diff(
            committed.splitlines(),
            live.splitlines(),
            fromfile="db/schema.sql (committed)",
            tofile="pg_dump (live)",
            lineterm="",
        )
    )
    pytest.fail(f"db/schema.sql is stale. Run `cscan db schema` and commit.\n\n{diff}")


def test_peak_ret_columns_are_in_the_committed_schema():
    """The specific drift that motivated this file, pinned by name.

    A whole-file equality test tells you *that* something drifted; this
    tells a reader *what* went missing last time, and fails loudly if a
    future regeneration is done against a database that never received
    `c7e1a4f9d302`.
    """
    committed = SCHEMA.read_text(encoding="utf-8")
    for h in (1, 2, 3, 5, 10):
        assert f"peak_ret_{h}d numeric(12,6)" in committed
