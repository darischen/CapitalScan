# The `capitalscan_hist` build toolchain

Recovered 2026-09-14. These scripts built `capitalscan_hist` (see
`docs/BACKLOG.md`, item 2b) on 2026-09-03/04. They were never committed to
this repo and `docs/BACKLOG.md` said for over a week that they were gone.
They were not — they were sitting untouched in a past Claude Code session's
own temp working directory the whole time, a location outside the repo
that nothing had reason to check.

Not wired into `cscan` or CI. Run by hand, against `capitalscan_hist`
specifically — `build_events_chunked.sh`'s own header has the rollback
(`dropdb capitalscan_hist`) and the reasoning for chunking by ticker, not
by date. The `.log`/`.csv`/`.txt` files here are the original run's output,
kept for reference rather than re-run.
