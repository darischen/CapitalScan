#!/usr/bin/env bash
# The Pi's poller wrapper: wait for the open, poll to the close, and narrate
# both to a terminal window and a per-day log.
#
# The Linux counterpart of `scripts/wait_and_poll.ps1`, with one deliberate
# difference: **it writes the serving store, not research** (ADR 158). Every
# poller *event* row is provisional -- nightly sweeps them and recomputes the
# authoritative version from bars -- so research never needed them. Removing
# it from the live write path is what frees the workstation during market
# hours.
#
# `cscan poll --serving` refuses to start against a stale target. Writing
# research, the poller read the store every other job writes, so it could not
# be behind; writing serving it reads a *copy*, and a copy that missed a sync
# still answers `universe` and `indicators` with last week's data rather than
# failing.
#
# Started by `capitalscan-poller.timer` at 00:00 local. The wait loop below is
# why the timer fires at midnight rather than 06:45: the unit is then already
# up and its log already open hours before the session, so a failure to start
# is visible in the morning instead of at the bell.
set -uo pipefail

INTERVAL="${1:-300}"
OPEN="06:45"   # 15 minutes after the 06:30 PT bell (user's decision,
               # 2026-08-24). This caps `coverage_pct` at ~96% permanently:
               # `ticks_expected` is a fixed 78 for a 06:30-13:00 session
               # regardless of when polling starts. Honest, but it means a
               # 4% shortfall is structural and not a coverage problem.
CLOSE="13:00"

cd "$(dirname "$0")/../.." || exit 1
DAY=$(date +%Y_%m_%d)
LOG="reports/poller/poller_${DAY}.log"
mkdir -p reports/poller

# Everything from here goes to both the terminal and the day's log.
exec > >(tee -a "$LOG") 2>&1

# **Open a window for this session, if a desktop is there to open it in.**
# Best effort by design: the poll must not depend on a GUI. xrdp allocates
# displays as sessions come and go, so the display is discovered rather than
# assumed, and a headless boot simply logs instead.
if [ -z "${CAPSCAN_NO_TERM:-}" ] && command -v lxterminal >/dev/null 2>&1; then
  for d in $(ls /tmp/.X11-unix/ 2>/dev/null | sed 's/^X/:/'); do
    if DISPLAY="$d" timeout 5 xset q >/dev/null 2>&1; then
      DISPLAY="$d" setsid lxterminal \
        --title="CapitalScan poller ${DAY}" \
        -e bash -c "tail -n +1 -f '$PWD/$LOG'" >/dev/null 2>&1 &
      break
    fi
  done
fi

say() { echo "[$1] $(date '+%H:%M:%S') - ${*:2}"; }

now_s=$(date +%s); open_s=$(date -d "$OPEN" +%s); close_s=$(date -d "$CLOSE" +%s)

if (( now_s > close_s )); then
  say STOP "Market already closed for today. Nothing to do."
  exit 0
fi

# Countdown, matching the Windows script's cadence so the two read the same.
while (( $(date +%s) < open_s )); do
  left=$(( open_s - $(date +%s) ))
  say WAIT "Waiting for poll start (${OPEN} PT, 15m after the 06:30 open). Starts in ~$(( left / 60 ))m $(( left % 60 ))s"
  sleep $(( left > 10 ? 10 : left ))
done

say START "Launching poller (--serving). Will run until ${CLOSE} PT"
.venv/bin/cscan poll --interval "$INTERVAL" --serving &
POLLER=$!
say MONITOR "Poller started (PID: $POLLER). Monitoring for confluence signals..."

# **Report confluence fires as they land.** Reads the serving store, which is
# local here and is where this poller writes. `id > last` rather than a time
# window: ids are monotonic, so nothing is missed or repeated if a query is
# slow.
PSQL=(sudo -u postgres psql -d capitalscan_serving -X -q -t -A -F'|')
last=0; n=0
while kill -0 "$POLLER" 2>/dev/null; do
  rows=$("${PSQL[@]}" -c "
    SELECT s.id, s.fired_at AT TIME ZONE 'America/Los_Angeles', e.ticker,
           e.signal_type, e.entry_price::text, e.k_full::text, e.d_full::text,
           e.vix_close::text
      FROM events e JOIN signal_reports s ON e.id = s.event_id
     WHERE e.signal_type LIKE 'confluence_%' AND s.id > $last
     ORDER BY s.id;" 2>/dev/null)
  if [ -n "$rows" ]; then
    while IFS='|' read -r id fired tick sig px k d vix; do
      [ -z "$id" ] && continue
      n=$((n+1)); last=$id
      echo "[CONFLUENCE #$n] $fired"
      echo "  $tick $sig | Price: $px | K: $k D: $d | VIX: $vix"
    done <<< "$rows"
  fi
  sleep 20
done

wait "$POLLER"; rc=$?
say STOP "Session ended (exit $rc). $n confluence signal(s) reported."
