"""Alembic wrapper: applies migrations to research and serving databases.

Two Postgres instances exist per ADR 053 (research local, serving Neon).
`cscan db migrate` targets both by default, since forgetting the second
database is the main way this goes wrong (BUILD.md §2). Each target is
a separate `alembic upgrade head` invocation pointed at a different URL
via the CAPSCAN_ALEMBIC_URL environment variable, which db/migrations/env.py
reads.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from rich.console import Console

console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@dataclass(frozen=True)
class Target:
    name: str
    env_var: str


TARGETS = (
    Target("research", "DATABASE_URL_RESEARCH"),
    Target("serving", "DATABASE_URL_SERVING"),
)


def _load_env() -> None:
    load_dotenv(REPO_ROOT / ".env.local")
    load_dotenv(REPO_ROOT / ".env")


def _resolve_targets(only: str | None) -> list[Target]:
    if only is None:
        return list(TARGETS)
    matches = [t for t in TARGETS if t.name == only]
    if not matches:
        names = ", ".join(t.name for t in TARGETS)
        raise ValueError(f"unknown target '{only}', expected one of: {names}")
    return matches


def _psycopg3_url(url: str) -> str:
    """Force the psycopg (v3) driver; pyproject pins psycopg, not psycopg2."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _alembic_config(url: str) -> Config:
    # env.py reads CAPSCAN_ALEMBIC_URL itself and takes priority over
    # sqlalchemy.url, so both must carry the same (psycopg3-driver) value.
    resolved = _psycopg3_url(url)
    os.environ["CAPSCAN_ALEMBIC_URL"] = resolved
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", resolved)
    return cfg


def migrate(only: str | None = None) -> None:
    """Run `alembic upgrade head` against each selected target."""
    _load_env()
    for target in _resolve_targets(only):
        url = os.environ.get(target.env_var)
        if not url:
            console.print(f"[yellow]skip[/yellow] {target.name}: {target.env_var} not set")
            continue
        console.print(f"[bold]{target.name}[/bold] -> upgrade head")
        command.upgrade(_alembic_config(url), "head")


def status(only: str | None = None) -> None:
    """Print `alembic current` for each selected target."""
    _load_env()
    for target in _resolve_targets(only):
        url = os.environ.get(target.env_var)
        if not url:
            console.print(f"[yellow]skip[/yellow] {target.name}: {target.env_var} not set")
            continue
        console.print(f"[bold]{target.name}[/bold]:")
        command.current(_alembic_config(url), verbose=True)


def rollback(only: str | None = None) -> None:
    """Run `alembic downgrade -1` against each selected target."""
    _load_env()
    for target in _resolve_targets(only):
        url = os.environ.get(target.env_var)
        if not url:
            console.print(f"[yellow]skip[/yellow] {target.name}: {target.env_var} not set")
            continue
        console.print(f"[bold]{target.name}[/bold] -> downgrade -1")
        command.downgrade(_alembic_config(url), "-1")


def schema(only: str = "research", out: Path | None = None) -> None:
    """Dump schema-only DDL to db/schema.sql via `docker exec ... pg_dump`.

    Only the research database is expected to be reachable through the
    local Docker container; the serving database lives on Neon.
    """
    _load_env()
    out = out or (REPO_ROOT / "db" / "schema.sql")
    container = os.environ.get("CAPSCAN_PG_CONTAINER", "capitalscan-postgres")
    db_user = os.environ.get("CAPSCAN_PG_USER", "capscan")
    db_name = os.environ.get("CAPSCAN_PG_DB", "capitalscan")

    result = subprocess.run(
        ["docker", "exec", container, "pg_dump", "-U", db_user, "-d", db_name, "--schema-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        console.print(f"[red]pg_dump failed:[/red] {result.stderr}")
        raise RuntimeError(result.stderr)

    dump = _strip_restrict_tokens(result.stdout)
    out.write_text(dump, encoding="utf-8")
    console.print(f"wrote {out} ({len(dump)} bytes)")


# pg_dump 18 wraps its output in `\restrict <token>` / `\unrestrict <token>`
# psql meta-commands, and regenerates that token on every invocation. Left in,
# every `cscan db schema` produces a two-line diff whether or not the schema
# changed, which is the fastest way to teach yourself to stop reading schema
# diffs. Stripping them makes a non-empty `git diff db/schema.sql` mean a real
# DDL change. They are a psql-session guard against the dump being replayed
# into a session with unexpected search_path settings, not DDL, so removing
# them does not change what the file describes.
_RESTRICT_TOKEN = re.compile(r"^\\(?:un)?restrict\s+\S+\s*$")


def _strip_restrict_tokens(dump: str) -> str:
    return "".join(
        line
        for line in dump.splitlines(keepends=True)
        if not _RESTRICT_TOKEN.match(line.rstrip("\r\n"))
    )
