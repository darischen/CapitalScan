#!/usr/bin/env bash
# Wait for stage 2 (indicators) to finish, then run stage 3 (universe+events).
SP="$(dirname "$0")"
for i in $(seq 1 720); do
  if grep -q "BUILD HIST DONE\|FAILED" "$SP/build2.log" 2>/dev/null; then break; fi
  sleep 30
done
if grep -q "FAILED" "$SP/build2.log" 2>/dev/null; then
  echo "stage 2 failed; not starting stage 3"; tail -5 "$SP/build2.log"; exit 1
fi
echo "=== stage 2 complete, starting stage 3 at $(date +%H:%M:%S) ==="
bash "$SP/build_hist3.sh"
