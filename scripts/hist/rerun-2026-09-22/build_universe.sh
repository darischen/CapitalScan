#!/usr/bin/env bash
# Stage 3: universe evaluations, then events, in the isolated store.
#
# **Why `crit_mcap` is dropped here, and why that is not a fudge.**
# `shares_outstanding` starts 2008-12-31 -- the real SEC XBRL mandate floor,
# not a fetcher setting (`yahoo_shares_full` carries 181,662 rows with a
# NULL `period_end`: current shares, no history). `crit_mcap` is in
# `required_criteria`, so with no market cap NOTHING pre-2009 can ever be
# `in_trade`, and the extension would add zero trainable events.
#
# Dropping it makes the comparison INTERNAL: both arms below use this same
# config, so "does more bear history help" is answered on its own terms.
# The absolute numbers are NOT comparable to production's, and nothing here
# may be quoted as if they were.
#
# It is also less radical than it sounds: `tickers` is already curated to
# large caps, so dropping `crit_mcap` does not admit small caps -- it admits
# the same names in years when their cap was lower.
#
# **event_start is 2002-01-01, not 2000-01-01.** `rel_return_lookback_days`
# is 756 (3 years) and bars begin 1998-09-30, so a universe evaluation
# before late 2001 has no lookback to read. 2002 still captures the tail of
# the dot-com decline (SPY -22.1% in 2002) and all of 2007-09.
#
# Rollback: dropdb capitalscan_hist

set -uo pipefail
cd "C:/Users/daris/Desktop/School/CapitalScan" || exit 1

export DATABASE_URL_RESEARCH="postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
export CAPSCAN_SPLITS='{"ingest_start":"1999-01-01","event_start":"2002-01-01"}'
export CAPSCAN_UNIVERSE='{"required_criteria":["crit_above_sma200","crit_sma200_slope","crit_rel_return"]}'

case "$DATABASE_URL_RESEARCH" in
  *capitalscan_hist*) ;;
  *) echo "REFUSING: not pointed at capitalscan_hist"; exit 2 ;;
esac
unset DATABASE_URL_SERVING

uv run python -c "
from capitalscan.jobs.config import resolve_config, config_hash
c = resolve_config()
print('config_hash :', config_hash(c))
print('criteria    :', c.universe.required_criteria)
print('event_start :', c.splits.event_start)"

# --- universe, quarter by quarter ---------------------------------------
# 2001Q4 so the first evaluated quarter has its 756-day lookback, through
# the last complete quarter.
fails=0
for y in $(seq 2001 2026); do
  for q in 1 2 3 4; do
    [ "$y" = 2001 ] && [ "$q" -lt 4 ] && continue
    [ "$y" = 2026 ] && [ "$q" -gt 2 ] && continue
    qq="${y}Q${q}"
    out=$(uv run cscan universe --quarter "$qq" 2>&1 | tail -1)
    code=$?
    if [ "$code" -ne 0 ]; then
      fails=$((fails + 1))
      echo "  universe $qq FAILED: $out"
      [ "$fails" -gt 4 ] && { echo "!!! too many universe failures, stopping"; exit 1; }
    else
      echo "  universe $qq ok: $out"
    fi
  done
done
echo "=== [$(date +%H:%M:%S)] universe done, $fails failure(s) ==="

echo "=== UNIVERSE STAGE DONE ==="
