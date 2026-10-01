#!/usr/bin/env bash
# Long-hold exit arms (BACKLOG item 10 follow-up, 2026-09-30). Workstation only:
# writes two new generations into the local research copy, never `wivie`.
# Run from the repo root. `--chunk-size` must stay 25 across restarts.
set -u
run_arm() {
  echo "=== arm $1: $2 === $(date)"
  CAPSCAN_EXITS="$2" .venv/Scripts/cscan.exe backtest --phase compute --workers 8 --chunk-size 25 --quiet || return 1
  CAPSCAN_EXITS="$2" .venv/Scripts/cscan.exe backtest --phase finalize --quiet || return 1
  echo "=== arm $1 done === $(date)"
}
run_arm hold10_stop3_notarget '{"max_hold_days":10,"stop_atr_k":3.0,"target_pct":1.0}' &&
run_arm hold10 '{"max_hold_days":10}'
echo "=== ARMS EXIT $? === $(date)"
