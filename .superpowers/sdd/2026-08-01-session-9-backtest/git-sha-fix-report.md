# git_sha() invariant-6 fix report

## Defect

`provenance.git_sha()` shelled out to `git rev-parse HEAD` and mapped
`FileNotFoundError` (git binary not resolvable on the caller's PATH) to the
same `'unknown'` fallback used for "not a git checkout at all." On this
machine, git is installed under Git for Windows but is only on Git Bash's
PATH, not PowerShell's (`Get-Command git` fails in PowerShell; `uv run
pytest` and every `cscan` job run from PowerShell). Every job launched that
way silently stamped `git_sha = 'unknown'` on every row it wrote, breaking
the `events.run_id -> runs.git_sha` provenance chain (invariant 6) — worst
for `backtest`, which was 100% affected (5/5 runs).

## Resolution order implemented

1. **File-based resolution (`_git_sha_from_files`)** — the primary and, in
   practice, only path exercised:
   - `_resolve_gitdir`: if `<repo_root>/.git` is a directory, that's the
     metadata dir. If it's a *file* (worktree or submodule), parse
     `gitdir: <path>` and resolve it relative to `repo_root`.
   - Read `<gitdir>/HEAD`. If its content is a raw 40-hex SHA, that's a
     detached HEAD — return it directly.
   - Otherwise it's `ref: refs/heads/<branch>`. Resolve the *common* dir
     (`_common_dir`): in a plain checkout this is `gitdir` itself; in a
     linked worktree, `gitdir` holds a per-worktree `HEAD` but a
     `commondir` file inside it points back at the main `.git`, where
     branch refs actually live.
   - `_read_ref_sha`: try the loose ref file
     (`<common_dir>/refs/heads/<branch>`) first; if absent, fall back to
     scanning `<common_dir>/packed-refs` for a `<sha> <ref>` line. This is
     the case a naive implementation misses — any freshly-cloned repo has
     its refs packed with no loose file.
2. **Subprocess fallback (`_git_sha_from_subprocess`)** — only reached if
   step 1 returns `None` (a `.git` layout the reader doesn't recognize,
   e.g. `HEAD` pointing at something other than a plain ref or raw SHA).
   Wrapped in try/except for `CalledProcessError`, `FileNotFoundError`, and
   `OSError`.
3. If both return `None`, the result is `'unknown'` — now meaning "no git
   metadata found by any means," not "the subprocess call failed."

Result is validated with `_is_sha` (40 hex chars) before being trusted, in
both paths, so a truncated read or corrupt ref file can't produce a
plausible-looking wrong answer.

## Subprocess: kept, but demoted to fallback-of-last-resort

Kept for `.git` layouts the file reader doesn't cover (e.g. a `HEAD` that
isn't a plain ref or raw SHA — not something I could construct from the
task's spec but plausible in exotic git states). It is never the primary
path and does not fix anything on this machine, since it depends on the
exact `git`-off-PATH condition that is the whole defect. Reading the
metadata files directly is both more robust (doesn't depend on PATH) and
faster (no process spawn), so it does essentially all the work now.

## TDD evidence

**RED** — full new suite against the pre-fix implementation:

```
uv run pytest capitalscan/tests/unit/test_provenance.py -v
```

```
test_detached_head_raw_sha FAILED         (assert 'unknown' == 'aaaa...')
test_loose_ref FAILED                      (assert 'unknown' == 'bbbb...')
test_packed_ref_no_loose_file FAILED       (assert 'unknown' == 'cccc...')
test_worktree_dot_git_file FAILED          (assert 'unknown' == 'dddd...')
test_no_git_metadata_yields_unknown PASSED (trivially — old code always returns 'unknown')
test_cached_after_first_call FAILED
test_real_repo_resolves_to_real_sha FAILED
  -> AssertionError: assert 'unknown' != 'unknown'
```

The last one is the regression guard: run in isolation against the
pre-fix code, `provenance.git_sha()` against the real repo's real `.git`
returned `'unknown'` — the exact defect, reproduced directly (git really is
off this shell's PATH; confirmed separately: `Get-Command git` in
PowerShell raises `CommandNotFoundException`).

**GREEN** — after implementing the fix:

```
uv run pytest capitalscan/tests/unit/test_provenance.py -v
```

```
7 passed in 0.23s
```

**Full safe suite**, per the session constraints (never bare `pytest`):

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property
```

```
763 passed in 31.82s
```

## Real repo SHA

```
uv run python -c "from capitalscan.jobs import provenance; print(provenance.git_sha())"
1084b66e86f516a554f60d4599e0c86291876b3c
```

Matches HEAD as given in the task (`1084b66`) and matches Git Bash's
`git rev-parse HEAD` output (`1084b66e86f516a554f60d4599e0c86291876b3c`),
confirmed via the Bash tool where `git` is reachable.

## Edge cases covered (`capitalscan/tests/unit/test_provenance.py`)

- Detached HEAD: `.git/HEAD` holds a raw 40-char SHA.
- Normal branch: `ref:` pointing at a loose `refs/heads/<branch>` file.
- **Packed ref, no loose file**: branch ref only present in
  `packed-refs`, nothing under `refs/heads/` — the freshly-cloned-repo
  case a naive resolver misses.
- Worktree: `.git` is a *file* with `gitdir: <path>`; the pointed-at dir
  has its own `HEAD` plus a `commondir` file redirecting to the main
  `.git` for the actual ref.
- No git metadata at all (`tmp_path` with nothing in it) → `'unknown'`,
  with the subprocess fallback also forced to fail, proving the `'unknown'`
  is a genuine absence, not a masked PATH problem.
- Per-process caching: mutating `.git/HEAD` after the first call doesn't
  change the second call's answer.
- Real-repo regression guard, cross-checked against `git rev-parse HEAD`
  run via a git binary located independently of `shutil.which` fallback
  paths (so the test's own oracle doesn't share the PATH problem it's
  testing for).

All fixtures build `.git` layouts under `tmp_path`, per the task's
instruction not to depend on this repository's own `.git` state.

## Concerns

- None blocking. One judgment call: I added `commondir` handling for the
  worktree case, which the task's bullet list didn't spell out explicitly
  but is necessary for a *correct* worktree resolution (branch refs live
  in the main `.git`, not the per-worktree gitdir) — a worktree fixture
  that skipped `commondir` would only coincidentally pass.
- Did not backfill `runs.git_sha` for historical `'unknown'` rows, per
  "not in scope."
