#!/usr/bin/env bash
# Wait for stage 3 (events) then score both arms.
SP="$(dirname "$0")"
TAB="$SP/../tab/.venv/Scripts/python.exe"
for i in $(seq 1 720); do
  grep -q "STAGE 3 DONE" "$SP/build3.log" 2>/dev/null && break
  grep -q "FAILED\|Traceback" "$SP/build3.log" 2>/dev/null && { echo "stage 3 failed; not scoring"; tail -8 "$SP/build3.log"; exit 1; }
  sleep 30
done
echo "=== stage 3 complete, scoring arms at $(date +%H:%M:%S) ==="
cd "$SP" && "$TAB" -u score_arms.py
