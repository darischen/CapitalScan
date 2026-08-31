"""`cscan preflight`: is this machine set up to run the research jobs?

Read-only. Every check returns ok / warn / fail with a one-line fix.
`fail` means a scheduled job would error or write the wrong thing; `warn`
means something is missing that is not fatal here (the schedule before it
is installed, serving unreachable from a machine that only ingests).

The regression test for the portability work is that this exits 0 on the
research machine and on the Pi.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Enough to run nightly / weekly / sync. The rest of .env.local is for the
# poller's notifications, the MCP server and the web app.
REQUIRED_ENV = (
    "DATABASE_URL_RESEARCH",
    "DATABASE_URL_SERVING",
    "SEC_USER_AGENT",
    "FINNHUB_API_KEY",
)

_LEVELS = {"ok": 0, "warn": 1, "fail": 2}


@dataclass
class Check:
    name: str
    level: str  # ok | warn | fail
    detail: str
    fix: str = ""


def _env_check() -> list[Check]:
    envf = REPO_ROOT / ".env.local"
    if not envf.exists():
        return [
            Check(
                ".env.local",
                "fail",
                "missing",
                "cp .env.local.example .env.local and fill the first block",
            )
        ]
    from dotenv import dotenv_values

    vals = dotenv_values(envf)
    missing = [k for k in REQUIRED_ENV if not (vals.get(k) or os.environ.get(k))]
    if missing:
        return [
            Check(
                ".env.local",
                "fail",
                f"unset: {', '.join(missing)}",
                "fill them in .env.local (see .env.local.example)",
            )
        ]
    return [Check(".env.local", "ok", f"{len(REQUIRED_ENV)} required keys set")]


def _psql_check() -> Check:
    override = os.environ.get("CAPSCAN_PSQL")
    if override:
        if Path(override).exists():
            return Check("psql", "ok", f"$CAPSCAN_PSQL -> {override}")
        return Check(
            "psql",
            "fail",
            f"$CAPSCAN_PSQL points at {override}, which is missing",
            "fix or unset CAPSCAN_PSQL",
        )
    found = shutil.which("psql")
    if found:
        return Check("psql", "ok", found)
    return Check("psql", "warn", "not on PATH", "add PostgreSQL bin to PATH, or set CAPSCAN_PSQL")


def _db_connects(url_env: str) -> tuple[bool, str]:
    from sqlalchemy import text

    from capitalscan.jobs import db_io

    url = os.environ.get(url_env)
    if not url:
        return False, f"{url_env} unset"
    try:
        # get_engine normalises the driver (postgresql:// -> +psycopg),
        # the same path every job uses.
        with db_io.get_engine(url, use_null_pool=True).connect() as c:
            c.execute(text("SELECT 1"))
        return True, url.rsplit("@", 1)[-1]
    except Exception as exc:  # noqa: BLE001 - reported
        return False, str(exc).splitlines()[0][:120]


def _research_checks() -> list[Check]:
    ok, detail = _db_connects("DATABASE_URL_RESEARCH")
    if not ok:
        return [Check("research db", "fail", detail, "start Postgres; check DATABASE_URL_RESEARCH")]
    out = [Check("research db", "ok", detail)]

    # alembic: does the database match the repo's migration head?
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        from capitalscan.jobs import db_io

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "db" / "migrations"))
        repo_head = ScriptDirectory.from_config(cfg).get_current_head()
        with db_io.get_engine(
            os.environ["DATABASE_URL_RESEARCH"], use_null_pool=True
        ).connect() as c:
            db_rev = MigrationContext.configure(c).get_current_revision()
        if db_rev == repo_head:
            out.append(Check("research schema", "ok", f"at head {repo_head}"))
        else:
            out.append(
                Check(
                    "research schema",
                    "fail",
                    f"db at {db_rev}, repo head {repo_head}",
                    "cscan db migrate --target research",
                )
            )
    except Exception as exc:  # noqa: BLE001
        line = str(exc).splitlines()[0][:100]
        out.append(Check("research schema", "warn", f"could not compare: {line}"))
    return out


def _serving_checks() -> list[Check]:
    ok, detail = _db_connects("DATABASE_URL_SERVING")
    if not ok:
        return [
            Check(
                "serving db",
                "warn",
                detail,
                "needed for `cscan sync`; fine to skip on a machine that only ingests",
            )
        ]
    out = [Check("serving db", "ok", detail)]

    try:
        from sqlalchemy import text

        from capitalscan.jobs import sync as sync_job
        from capitalscan.jobs.config import config_hash, resolve_config

        resolved = config_hash(resolve_config())
        with sync_job.serving_engine().connect() as c:
            pinned = c.execute(text("SELECT config_hash FROM serving_config")).scalar_one()
        if resolved == pinned:
            out.append(
                Check("config hash", "ok", f"resolves to {resolved}, matches serving_config")
            )
        else:
            out.append(
                Check(
                    "config hash",
                    "fail",
                    f"resolves to {resolved}, serving_config pins {pinned}",
                    "an ablation arm is set in core/config.py, or run `cscan db sync-config`",
                )
            )
    except Exception as exc:  # noqa: BLE001
        line = str(exc).splitlines()[0][:100]
        out.append(Check("config hash", "warn", f"could not compare: {line}"))
    return out


def _schedule_check() -> Check:
    win = platform.system() == "Windows"
    try:
        if win:
            r = subprocess.run(
                ["schtasks", "/query", "/tn", "CapitalScan nightly"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                return Check("schedule", "ok", "CapitalScan nightly registered")
            return Check(
                "schedule",
                "warn",
                "CapitalScan nightly not registered",
                "scripts\\install_schedule.ps1",
            )
        r = subprocess.run(
            ["systemctl", "list-unit-files", "capitalscan-nightly.timer"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if "capitalscan-nightly.timer" in r.stdout:
            return Check("schedule", "ok", "capitalscan-nightly.timer installed")
        return Check(
            "schedule",
            "warn",
            "capitalscan-nightly.timer not installed",
            "sudo scripts/systemd/install.sh",
        )
    except Exception as exc:  # noqa: BLE001
        return Check("schedule", "warn", f"could not check: {str(exc).splitlines()[0][:80]}")


def run() -> list[Check]:
    from capitalscan.jobs.db import _load_env

    _load_env()
    checks: list[Check] = []
    checks += _env_check()
    checks.append(_psql_check())
    checks += _research_checks()
    checks += _serving_checks()
    checks.append(_schedule_check())
    return checks


def worst(checks: list[Check]) -> str:
    return max((c.level for c in checks), key=lambda lv: _LEVELS[lv], default="ok")
