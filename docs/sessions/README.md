# Session plans

One page per session, in order. Read `../BUILD.md` for what to build next
and `../RESULTS.md` for what happened when it ran; this file only says where
each session's plan lives.

**The naming is inconsistent and stays that way.** `session10.md`,
`session11-statistical-foundations.md`, and the `session-9-backtest/`
directory follow three conventions. Renaming them would break every citation
in `BUILD.md`, `RESULTS.md`, and `DECISIONS.md` for no gain — this index is
the cheap fix (session 15 §7). New files use
`session<N>-<short-slug>.md`.

| # | Phase | File | Outcome |
|---|---|---|---|
| 7 | 1 | `SESSION_7_NOTES.md`, `SESSION_7_RUNBOOK.md`, `SESSION_7_STATUS.md` | Full backfill |
| 9 | 3 | `session-9-backtest/` (50 files) | Backtest engine. **Phase 3 gate passed** |
| 10 | 3→4 | `session10.md` | Forward path store and derived labels |
| 11 | 4 | `session11-statistical-foundations.md` | Wilson CI, BH correction, baselines, self-validation gate |
| 12 | 4 | `session12-cell-grid.md` | Cell grid, `cell_key` parity, `cell_stats` writer |
| 13 | 4 | `session13-benchmark-arms.md` | Eight arms, the 200-replication null |
| 14 | 4 | `session14-phase4-close.md` | Artifacts, drawdown slice, ADR 092's matcher. **Phase 4 gate closed** |
| 15 | 5 | `session15-handlers-and-tools.md` | Handlers, seven tools, response validator, ADR 095's `v_positions` rebuild |
| 16 | 5 | `session16-18-phase5.md` | MCP server |
| 17 | 5 | `session16-18-phase5.md` | Screener and ticker page |
| 18 | 5 | `session16-18-phase5.md` | Research page, chat, Phase 5 close |

Sessions 0-8 predate the per-session plan convention. `HANDOFF.md` and
`SESSION_9_STANDING_ORDERS.md` are cross-session documents rather than
plans.

## What sessions 16-18 should know before starting

`session16-18-phase5.md` says its own task-level detail for 17 and 18 is a
starting position, and that a planner should re-read the shipped
`handlers/types.py` first. Three specifics from session 15 that move that
plan:

- **`get_stats` takes no `ticker`.** DESIGN §10.1 lists one; `cell_stats`
  has no ticker dimension (ADR 102, ADR 104), so the argument could only be
  accepted and ignored. Per-ticker history is `get_events(ticker=...)`.
- **The screener's statistical fields live in `ScreenRow.stats`**, a
  `CellStats | Suppressed | None` union, not as columns (ADR 114). The
  handler does not query `cell_stats` at all unless asked.
- **`v_ticker_state` takes 26.5 s to materialize** on the developer
  database, and `v_positions` and the ticker page both read it. Measure
  before building an interactive page on top of it.
