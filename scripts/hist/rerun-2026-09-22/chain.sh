#!/usr/bin/env bash
# Rebuild capitalscan_hist's events and labels under today's config, then score
# the 2010-vs-2002 arms. Throwaway. Each stage must succeed before the next.
set -uo pipefail
R="$(cd "$(dirname "$0")" && pwd)"
run() { local name="$1"; shift; local t0=$SECONDS
  echo "=== [$(date '+%a %H:%M:%S')] START $name ==="
  "$@" > "$R/$name.log" 2>&1; local code=$?
  echo "=== [$(date '+%a %H:%M:%S')] END $name exit=$code elapsed=$(( (SECONDS-t0)/60 ))m ==="
  tail -4 "$R/$name.log"
  [ "$code" -ne 0 ] && { echo "!!! $name FAILED, chain stopped"; exit 1; }
  return 0; }
run universe bash "$R/build_universe.sh"
grep -q "universe done, 0 failure" "$R/universe.log" || { echo "!!! universe reported failures"; exit 1; }
run events bash "$R/build_events_chunked.sh"
grep -q "FAILED" "$R/events.log" && { echo "!!! an events chunk failed"; exit 1; }
run labels bash "$R/build_labels.sh"
cd "C:/Users/daris/Desktop/School/CapitalScan" && run score uv run python "$R/score_arms_v2.py"
echo "=== CHAIN DONE [$(date '+%a %H:%M:%S')] ==="
