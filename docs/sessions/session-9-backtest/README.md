# Session 9 working notes

Moved here from `.superpowers/sdd/2026-08-01-session-9-backtest/` on 2026-08-18.

**These are working notes, not documentation.** Diagnosis transcripts, gate
measurements, and per-task reports written while Session 9 built the backtest
engine. They are kept because `BUILD.md` and `RESULTS.md` cite them as the
source for published numbers, and because nothing regenerates them — unlike
`reports/phase4/`, which `cscan stats artifacts` can rebuild from the database.

## Cited from the permanent docs

| File | Cited by | For |
|---|---|---|
| `phase3-gate-measurement.md` | `BUILD.md`, `RESULTS.md` | Full Phase 3 gate detail, including the queries behind the event-rate check and the harness-gate debugging history |
| `results-sweep-report.md` | `RESULTS.md` | The 18-config sweep table, omitted from `RESULTS.md` for length |
| `hourly-residual-diagnosis.md` | `RESULTS.md` | The ANET split-window verification (ex_date 2024-12-04, 41 rejected sessions) |
| `progress.md` | `RESULTS.md` | Task-by-task history, 1,203 lines |

Those four are load-bearing. The rest is context for them.

## What was dropped in the move

28 `review-<sha>..<sha>.diff` files and `whole-branch-source.diff`. All were
untracked — `.superpowers/sdd/.gitignore` was `*`, so the 50 markdown files had
been force-added and the diffs never were. Every commit they reference is still
in history, so any of them regenerates with:

```
git diff 03242ae..5613eef
```

## Reading order

`progress.md` is the spine. `phase3-gate-measurement.md` is the one to read if
you are checking a published Phase 3 number. The `*-report.md` files are each
one task or one fix, named for what they cover.
