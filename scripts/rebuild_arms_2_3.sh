#!/usr/bin/env bash
# Ablation arms 2 and 3 (docs/REBUILD_ARMS.md). Run after 13:00 PT, when
# the poller has stopped -- two writers on `events` is the rule with no
# exceptions.
#
# **Each arm still needs its own universe pass.** `universe` is keyed on
# `config_hash` since d4a17c93f60b, so a different config has no rows at
# all until one runs. What that migration bought is that arms no longer
# OVERWRITE each other, so there is no restore pass and no required order
# -- not that the pass disappears.
#
# ~2h40m per arm: universe 20 + compute ~95 + finalize 4 + harness 36 +
# stats 6.
set -uo pipefail
cd "$(dirname "$0")/.."
log() { echo "[$(date -u -d '-7 hours' '+%H:%M:%S') PT] $*"; }

if [ -z "${1:-}" ]; then
  cat <<'USAGE'
usage: rebuild_arms_2_3.sh <arm>

  arm2   sma200_slope_min = -0.01     admits a flat base (+37 tickers at -1%)
  arm3   drop crit_rel_return         +64 names incl. AAPL; trade 184 -> 248

Edit core/config.py for the arm you want FIRST -- the config hash is
computed from the whole Config, and this script only reads it back.

One consequence to decide with arm 3: the `history` watch route requires
crit_rel_return to be None. Dropping it from required_criteria leaves that
route intact; REPLACING it with a plain history check makes a new ticker
return False and the route stops firing.
USAGE
  exit 2
fi

CH=$(uv run python -c "
from capitalscan.jobs.config import config_hash, resolve_config
print(config_hash(resolve_config()))" | tr -d '\r')
log "config hash for this arm: $CH"
if [ "$CH" = "a38d3ca6b58295e8" ]; then
  log "REFUSING: that is arm 1's hash. Edit core/config.py first."
  exit 1
fi

run() { log "=== $* ==="; uv run cscan "$@" 2>&1 | tail -6
        rc=${PIPESTATUS[0]}; log "--- exit $rc ---"
        [ "$rc" -ne 0 ] && { log "ABORTING at: $*"; exit "$rc"; }; }

log "=== universe, 66 quarters (~20 min) ==="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/universe_backfill.ps1 2>&1 | tail -5

run backtest --workers 8 --chunk-size 24 --phase compute
run backtest --workers 8 --phase finalize
run backtest --workers 8 --phase harness
run stats rho        --config-hash "$CH"
run stats cells      --config-hash "$CH" --split-key train
run stats cells      --config-hash "$CH" --split-key validate
run stats benchmarks --config-hash "$CH" --split-key train    --workers 8
run stats benchmarks --config-hash "$CH" --split-key validate --workers 8

log "=== this arm's answer ==="
PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan -t -A -c "
SELECT 'surviving FDR (validate q<=0.10): '||count(*) FROM cell_stats
 WHERE config_hash='$CH' AND split_key='validate' AND q_value<=0.10"
PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan -t -A -c "
SELECT 'min q (validate): '||round(min(q_value),4) FROM cell_stats
 WHERE config_hash='$CH' AND split_key='validate'"
log "=== ARM COMPLETE - record it in RESULTS.md ==="
