# Rerun of the 2010-vs-2002 training-window test, 2026-09-21/22

A record of what ran, kept in the repo because the original `capitalscan_hist`
toolchain was lost for over a week once by living in a temp directory.
**Not portable and not meant to be.** The shell scripts carry the
workstation's absolute repo path and the scorer points at the local
`capitalscan_hist` database, exactly as they ran. Results and reasoning are
in `docs/RESULTS.md`, 2026-09-22.

| file | what it is |
|---|---|
| `chain.sh` | runs the four stages in order, stopping on the first failure |
| `build_universe.sh` | `scripts/hist/build_hist3.sh` minus its single-process events step |
| `build_events_chunked.sh` | unchanged from `scripts/hist/`, with a fresh chunk ledger |
| `build_labels.sh` | `scripts/hist/build_labels.sh` plus a `finalize` step after compute |
| `score_arms_v2.py` | the arm scorer, rewritten to split by family and to add the 2022 above-200-day cell |
| `arms_results_v2.csv` | every head's coverage, per arm, year and regime |
| `*.log` | each stage's output |

**Two changes were made to `capitalscan_hist` by hand to get this to run**,
both recorded here because they persist in that store:

1. **Migrated to head** (`e2c7a94b3d15` → `a7c2e9f4b105`, 15 migrations, serving
   skipped). The store was built before the `trough_ret_*` columns existed,
   so labelling failed on them.
2. **`GJS` removed from the trade universe** (`in_trade = false`, config
   `e53e0ebd9a4e5be6`). It is a structured product with no sector, so
   ADR 147 refuses to train on it. Production never admits it because it has
   no market cap; this store drops `crit_mcap`, which let it in.

Also left behind: `zz_events_run_id`, an index on `events(run_id)` added
while chasing a slow events stage. It changed nothing (→ BACKLOG, the
`cscan events` item) and is harmless to keep.

Measured stage times: universe 27 min, events ~7h20m (25 chunks, one core of
per-bar pandas overhead), labels 51 min, scoring 40 min.
