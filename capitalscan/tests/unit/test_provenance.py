"""Tests for `provenance.git_sha` (invariant 6 regression, Session 9).

The defect: `git_sha` shelled out to `git rev-parse HEAD` and mapped
`FileNotFoundError` (git missing from PATH, as it is under this machine's
PowerShell) to the same `'unknown'` fallback used for "not a git checkout
at all." Every job launched from PowerShell silently stamped
`git_sha = 'unknown'` on every row it wrote, even though the repository's
commit metadata was sitting on disk the whole time.

The fix reads `.git/HEAD` and the ref files directly, so it no longer
depends on `git` being resolvable on the caller's PATH. The subprocess is
kept only as a last-resort fallback for git layouts the file-based reader
does not recognize; it is never the primary path.

All fixtures build a fake `.git` layout under `tmp_path` — none of these
tests depend on this repository's own `.git` state, which changes as we
commit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from capitalscan.jobs import provenance

DETACHED_SHA = "a" * 40
LOOSE_SHA = "b" * 40
PACKED_SHA = "c" * 40
COMMON_SHA = "d" * 40


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """`git_sha` caches per process; each test needs a clean slate."""
    monkeypatch.setattr(provenance, "_cached_sha", None)
    yield
    monkeypatch.setattr(provenance, "_cached_sha", None)


def _no_subprocess(monkeypatch):
    """Force the subprocess fallback to fail, so tests only exercise the
    file-based resolution path (and prove it doesn't need `git` at all)."""

    def _boom(*args, **kwargs):
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(provenance.subprocess, "run", _boom)


def test_detached_head_raw_sha(tmp_path, monkeypatch):
    """`.git/HEAD` holding a raw 40-char SHA (detached HEAD state)."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(DETACHED_SHA + "\n", encoding="utf-8")

    monkeypatch.setattr(provenance, "REPO_ROOT", tmp_path)
    _no_subprocess(monkeypatch)

    assert provenance.git_sha() == DETACHED_SHA


def test_loose_ref(tmp_path, monkeypatch):
    """`HEAD` -> `ref: refs/heads/<branch>` -> a loose ref file."""
    git_dir = tmp_path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text(
        LOOSE_SHA + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(provenance, "REPO_ROOT", tmp_path)
    _no_subprocess(monkeypatch)

    assert provenance.git_sha() == LOOSE_SHA


def test_packed_ref_no_loose_file(tmp_path, monkeypatch):
    """A freshly-cloned repo: the branch ref exists only in `packed-refs`,
    with no loose file under `refs/heads/`. A naive "read the loose ref
    file" implementation misses this case entirely."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{PACKED_SHA} refs/heads/main\n"
        f"{'e' * 40} refs/heads/other-branch\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(provenance, "REPO_ROOT", tmp_path)
    _no_subprocess(monkeypatch)

    assert provenance.git_sha() == PACKED_SHA


def test_worktree_dot_git_file(tmp_path, monkeypatch):
    """A linked worktree: `.git` is a *file* containing `gitdir: <path>`,
    pointing at `<main>/.git/worktrees/<name>`. That directory has its own
    per-worktree `HEAD`, but branch refs live in the common dir (found via
    `commondir`), not in the worktree-specific gitdir."""
    main_git_dir = tmp_path / "main-repo" / ".git"
    worktree_root = tmp_path / "linked-worktree"
    wt_gitdir = main_git_dir / "worktrees" / "wt1"
    wt_gitdir.mkdir(parents=True)
    (main_git_dir / "refs" / "heads").mkdir(parents=True)

    # Worktree-specific HEAD points at a branch whose ref lives in the
    # common (main) .git dir, reached via the "commondir" file.
    (wt_gitdir / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (wt_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (main_git_dir / "refs" / "heads" / "feature").write_text(
        COMMON_SHA + "\n", encoding="utf-8"
    )

    worktree_root.mkdir(parents=True)
    (worktree_root / ".git").write_text(
        f"gitdir: {wt_gitdir}\n", encoding="utf-8"
    )

    monkeypatch.setattr(provenance, "REPO_ROOT", worktree_root)
    _no_subprocess(monkeypatch)

    assert provenance.git_sha() == COMMON_SHA


def test_no_git_metadata_yields_unknown(tmp_path, monkeypatch):
    """A tarball export with no `.git` at all, and no `git` binary
    reachable either: the only honest answer is `'unknown'`."""
    monkeypatch.setattr(provenance, "REPO_ROOT", tmp_path)
    _no_subprocess(monkeypatch)

    assert provenance.git_sha() == "unknown"


def test_cached_after_first_call(tmp_path, monkeypatch):
    """The result is cached per process — a second call must not re-read
    the filesystem (or care that it changed)."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(DETACHED_SHA + "\n", encoding="utf-8")

    monkeypatch.setattr(provenance, "REPO_ROOT", tmp_path)
    _no_subprocess(monkeypatch)

    assert provenance.git_sha() == DETACHED_SHA

    # Mutate HEAD after the first call; the cached value must not change.
    (git_dir / "HEAD").write_text(("f" * 40) + "\n", encoding="utf-8")
    assert provenance.git_sha() == DETACHED_SHA


def _find_git_binary() -> str | None:
    """Locate a `git` executable independently of the current PATH, so this
    test's own oracle doesn't depend on the very PATH problem the defect is
    about (on this machine, `git` is on Git Bash's PATH but not
    PowerShell's, which is where `uv run pytest` executes)."""
    on_path = shutil.which("git")
    if on_path is not None:
        return on_path
    for candidate in (
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files\Git\mingw64\bin\git.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def test_real_repo_resolves_to_real_sha():
    """Regression guard for the actual defect: against this repository's
    real `.git`, `git_sha()` must resolve a full 40-hex-char SHA, not
    `'unknown'`.

    This is the test that fails against the pre-fix implementation when
    `git` is off PATH (the exact situation on this machine's PowerShell,
    where `uv run pytest` runs): the old code had no file-based path at
    all, so a missing `git` binary on PATH made every call fall through to
    `'unknown'`, silently, on every job run.

    Where a `git` binary can be located by any means (not necessarily on
    PATH), the resolved SHA is also checked against `git rev-parse HEAD`
    run against that binary directly, for an independent cross-check.
    """
    sha = provenance.git_sha()

    assert sha != "unknown"
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)

    git_binary = _find_git_binary()
    if git_binary is None:
        pytest.skip("no git binary reachable by any means on this machine")
    expected = subprocess.run(
        [git_binary, "rev-parse", "HEAD"],
        cwd=provenance.REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert sha == expected
