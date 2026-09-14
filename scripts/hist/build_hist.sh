#!/usr/bin/env bash
# Build an extended-history research store in an ISOLATED database.
#
# Everything here writes to capitalscan_hist. Production (capitalscan) is
# never opened. Rollback is one command:
#
#     dropdb capitalscan_hist
#
# Two independent layers of isolation, deliberately:
#   1. a different database        (capitalscan_hist)
#   2. a different config_hash     (66006bab7fb552bc vs 0523841076f47293)
# so even a mistake that crossed databases could not conflate the results.
#
# The only variable changed against production is the START of history:
# event_start 2010-01-01 -> 2000-01-01. train_end and validate_end are
# untouched, so validate stays exactly 2022-01-03..2023-12-29 and the
# coverage numbers are directly comparable to RESULTS 2026-09-03.

set -uo pipefail

REPO="C:/Users/daris/Desktop/School/CapitalScan"
LOG_DIR="$(dirname "$0")"
cd "$REPO" || exit 1

export DATABASE_URL_RESEARCH="postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
export CAPSCAN_ALEMBIC_URL="$DATABASE_URL_RESEARCH"
export CAPSCAN_SPLITS='{"ingest_start":"1999-01-01","event_start":"2000-01-01"}'

# --- safety: refuse to run unless we are pointed at the isolated store ----
case "$DATABASE_URL_RESEARCH" in
  *capitalscan_hist*) ;;
  *) echo "REFUSING: DATABASE_URL_RESEARCH is not capitalscan_hist"; exit 2 ;;
esac
# `cscan sync` would write the SERVING store. Nothing here may reach it.
unset DATABASE_URL_SERVING

step() {
  local name="$1"; shift
  local t0=$SECONDS
  echo "=== [$(date +%H:%M:%S)] START $name ==="
  "$@" 2>&1 | tail -25
  local code=${PIPESTATUS[0]}
  echo "=== [$(date +%H:%M:%S)] END $name exit=$code elapsed=$((SECONDS - t0))s ==="
  if [ "$code" -ne 0 ]; then
    echo "!!! $name FAILED, stopping"
    exit "$code"
  fi
}

echo "target db : capitalscan_hist"
echo "config    : $CAPSCAN_SPLITS"
uv run python -c "
from capitalscan.jobs.config import resolve_config, config_hash
print('resolved  :', config_hash(resolve_config()))"

# 1. Calendar back to 1999 (mcal, no network beyond the package).
step calendar uv run cscan calendar --through 2027

# 2. Index and VIX context. 10200 calendar days ~= 27.9 years.
step market uv run cscan market --lookback 10200

# 3. Daily bars for the whole universe, back to 1999.
step bars uv run cscan bars --daily --lookback 10200

# 4. Earnings (days_to_earnings). Pre-2010 coverage is expected to be thin;
#    the feature is mean-imputed with an indicator, so thin is survivable.
step earnings uv run cscan earnings

# 5. Indicators over the extended window. --workers 8: it defaults to 1.
step indicators uv run cscan indicators --workers 8

# 6. Events.
step events uv run cscan events

echo "=== BUILD HIST DONE ==="
