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


def _is_sha(candidate: str) -> bool:
    """A full 40-char hex commit id. Guards against a truncated read or a
    stray line in a ref file being mistaken for a SHA."""
    return len(candidate) == 40 and all(
        c in "0123456789abcdefABCDEF" for c in candidate
    )


def _resolve_gitdir(repo_root: Path) -> Path | None:
    """Where this checkout's git metadata lives.

    Ordinary checkout: `<repo_root>/.git` is the metadata directory itself.
    Worktree or submodule: `<repo_root>/.git` is a *file* containing
    `gitdir: <path>`, pointing at the real metadata directory elsewhere
    (for a worktree, typically `<main>/.git/worktrees/<name>`).
    """
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            content = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if content.startswith(prefix):
            target = content[len(prefix) :].strip()
            path = Path(target)
            if not path.is_absolute():
                path = repo_root / path
            return path
    return None


def _common_dir(gitdir: Path) -> Path:
    """Where shared refs (`refs/heads/*`, `packed-refs`) live.

    In a plain checkout this is `gitdir` itself. In a linked worktree,
    `gitdir` is the worktree-private directory (holding this worktree's own
    `HEAD`), and a `commondir` file inside it points back at the main
    `.git` directory, where branch refs actually live.
    """
    commondir_file = gitdir / "commondir"
    if commondir_file.is_file():
        try:
            content = commondir_file.read_text(encoding="utf-8").strip()
        except OSError:
            return gitdir
        if content:
            path = Path(content)
            if not path.is_absolute():
                path = gitdir / path
            return path
    return gitdir


def _read_ref_sha(common_dir: Path, ref: str) -> str | None:
    """Resolve a ref name (e.g. `refs/heads/main`) to a SHA.

    Tries the loose ref file first (`<common_dir>/<ref>`), then falls back
    to `packed-refs` — git packs refs after a `gc` or a fresh `clone`,
    which deletes the loose file and leaves only a `<sha> <ref>` line in
    `packed-refs`. A resolver that only checks the loose file silently
    fails on any freshly-cloned repo.
    """
    loose = common_dir / ref
    if loose.is_file():
        try:
            candidate = loose.read_text(encoding="utf-8").strip()
        except OSError:
            candidate = ""
        if _is_sha(candidate):
            return candidate

    packed = common_dir / "packed-refs"
    if packed.is_file():
        try:
            lines = packed.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            line = line.strip()
            if not line or line[0] in "#^":
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and _is_sha(parts[0]):
                return parts[0]
    return None


def _git_sha_from_files(repo_root: Path) -> str | None:
    """Read the commit SHA straight out of the repo's own `.git` metadata,
    with no subprocess involved. Returns None if the metadata isn't there
    or isn't in a shape this function recognizes."""
    gitdir = _resolve_gitdir(repo_root)
    if gitdir is None or not gitdir.is_dir():
        return None

    head_file = gitdir / "HEAD"
    if not head_file.is_file():
        return None
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if _is_sha(head_content):
        return head_content  # detached HEAD: raw SHA written directly

    if head_content.startswith("ref:"):
        ref = head_content[len("ref:") :].strip()
        common_dir = _common_dir(gitdir)
        return _read_ref_sha(common_dir, ref)

    return None


def _git_sha_from_subprocess(repo_root: Path) -> str | None:
    """Last-resort fallback for git layouts `_git_sha_from_files` doesn't
    recognize (e.g. a `HEAD` pointing at something other than a plain ref
    or raw SHA). Not the primary path: it depends on `git` being
    resolvable on the caller's PATH, which is exactly the condition that
    doesn't hold under this project's PowerShell shell — reading the
    metadata files directly avoids that dependency entirely, and is faster
    besides.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    candidate = result.stdout.strip()
    return candidate if _is_sha(candidate) else None


def git_sha() -> str:
    """The current commit SHA (full 40 hex chars), or 'unknown' when no
    git metadata can be found at all — a tarball export, for instance.

    Resolved by reading `.git/HEAD` and the relevant ref file(s) directly,
    so this does not depend on a `git` binary being on the caller's PATH.
    `subprocess` is used only as a fallback for a git layout the file
    reader doesn't recognize. Cached per process — a job never changes
    commits mid-run, and re-reading these files on every row-write would
    be wasteful.
    """
    global _cached_sha
    if _cached_sha is not None:
        return _cached_sha
    sha = _git_sha_from_files(REPO_ROOT)
    if sha is None:
        sha = _git_sha_from_subprocess(REPO_ROOT)
    _cached_sha = sha if sha is not None else "unknown"
    return _cached_sha


def new_run_id(job: str) -> str:
    """A unique, sortable id: `{job}_{utc timestamp}_{short uuid}`."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{job}_{stamp}_{uuid.uuid4().hex[:8]}"
