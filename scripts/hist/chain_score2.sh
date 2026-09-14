#!/usr/bin/env bash
SP="$(dirname "$0")"
TAB="$SP/../tab/.venv/Scripts/python.exe"
for i in $(seq 1 240); do
  grep -q "EVENTS CHUNKED DONE" "$SP/events_chunked.log" 2>/dev/null && break
  sleep 20
done
echo "=== events+finalize complete, scoring at $(date +%H:%M:%S) ==="
cd "$SP" && "$TAB" -u score_arms.py
