"""Run identity: `run_id` and `git_sha` on every generated row (invariant 6).

Every job stamps its output with both, and records one `runs` row per
execution. Reference tables without a `run_id` column (`tickers`,
`corporate_actions`, `market_days`, `shares_outstanding`, `earnings`,
`trading_days`) don't carry either — invariant 6 applies where the schema
has the column, which the DDL in DESIGN §2.5 draws that line for.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_cached_sha: str | None = None


def git_sha() -> str:
    """The current commit SHA, or 'unknown' outside a git checkout.

    Cached per process — a job never changes commits mid-run, and shelling
    out to git on every row-write would be wasteful.
    """
    global _cached_sha
    if _cached_sha is not None:
        return _cached_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        _cached_sha = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        _cached_sha = "unknown"
    return _cached_sha


def new_run_id(job: str) -> str:
    """A unique, sortable id: `{job}_{utc timestamp}_{short uuid}`."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{job}_{stamp}_{uuid.uuid4().hex[:8]}"
