# One-shot repair scripts, already applied

Moved here 2026-09-14 for organization — all four already ran successfully
against real data and are kept as the documented record of what was broken
and how it was fixed (same reasoning ADR 034/096 apply everywhere else in
this project: every published fix traces to the script that made it).
None of these need running again.

| script | fixed | confirmed |
|---|---|---|
| `adr146_clear_scale_errors.sql` | 33 stored x1,000 share-scale filings | `shares_scale_errors_pre_adr146` holds exactly 33 rows |
| `backfill_poller_timestamps.py` | naive-ET timestamps stored 4-5h early | DECISIONS.md: 1,752 rows corrected (876 `signal_reports`, 876 `quotes_live`) |
| `repair_prediction_event_ids.py` | `predictions.event_id` pointing at research ids on serving | DECISIONS.md ADR 191: 498 duplicates collapsed, 10,438 ids rewritten, 0 dangling / 0 wrong |
| `backfill_json_reprs.py` | nested dict/list values stored as Python reprs instead of JSON | DECISIONS.md, parses each repr with `ast.literal_eval` |

Some older dated entries in `docs/DECISIONS.md`, `docs/RESULTS.md`, and
`docs/sessions/` still cite the old `scripts/<name>.py` path from before
this move — that's a point-in-time record of what was true when they were
written, not a broken pointer to chase down.
