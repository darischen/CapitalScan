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
  # **Highest display first, which means the xrdp session before the
  # physical seat.** Both answer on this Pi -- `:0` is the console and `:10`
  # is xrdp -- and a window opened on `:0` is invisible to anyone connecting
  # over RDP, which is the only way this machine is ever looked at.
  for d in $(ls /tmp/.X11-unix/ 2>/dev/null | sed 's/^X/:/' | sort -t: -k2 -rn); do
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

# **The session CSV, same name and columns as `wait_and_poll.ps1`.**
# `reports/poller/poller_session_YYYY_MM_DD_HHMMSS.csv`, so the Pi's output
# drops into the same directory and tooling as the Windows script's.
#
# Written with a UTF-8 BOM because the .ps1's files carry one and a reader
# that sniffs encoding should not see the two as different formats.
CSV="reports/poller/poller_session_$(date +%Y_%m_%d_%H%M%S).csv"
# Escapes, not the literal characters. Writing the BOM as text encodes
# it a second time -- U+00EF U+00BB U+00BF becomes the six bytes
# c3 af c2 bb c2 bf, and Excel then shows a visible BOM in the first
# header cell. The .ps1's files start with the three bytes ef bb bf,
# and matching them is the whole point of this block.
printf '\xef\xbb\xbf' > "$CSV"
echo 'fired_at_pt,ticker,signal_type,entry_price,side,touch_level,k_full,d_full,k_fast,atr_14,vix_close,spx_ret_1d,channels_sent,day_open,reversal,open_gap_atr' >> "$CSV"
say CSV "Writing $CSV"

say START "Launching poller (--serving). Will run until ${CLOSE} PT"
.venv/bin/cscan poll --interval "$INTERVAL" --serving &
POLLER=$!
say MONITOR "Poller started (PID: $POLLER). Monitoring for confluence signals..."

# **Report confluence fires as they land.** Reads the serving store, which is
# local here and is where this poller writes. `id > last` rather than a time
# window: ids are monotonic, so nothing is missed or repeated if a query is
# slow.
PSQL=(sudo -u postgres psql -d capitalscan_serving -X -q -t -A -F'|')
SESSION_DATE=$(date +%F)
last=0; n=0
while kill -0 "$POLLER" 2>/dev/null; do
  # **Every signal of the session goes to the CSV; only confluence is
  # narrated.** The .ps1 does the same, and the distinction matters -- the
  # terminal is for watching, the CSV is the record.
  #
  # `signal_date = :d` with the session's own date, never CURRENT_DATE
  # (ADR 119): the database runs UTC, and while the poll window happens to
  # fall inside one UTC day, relying on that is how this bug keeps
  # returning.
  rows=$("${PSQL[@]}" -c "
    SELECT s.id,
           s.fired_at AT TIME ZONE 'America/Los_Angeles', e.ticker,
           e.signal_type, e.entry_price::text, e.side, e.touch_level::text,
           e.k_full::text, e.d_full::text, e.k_fast::text, e.atr_14::text,
           e.vix_close::text, e.spx_ret_1d::text,
           array_to_string(s.channels_sent, ' '),
           s.state_json->>'day_open',
           s.state_json->'bear_reversal'->>'confirmed',
           s.state_json->'bear_reversal'->>'open_gap_atr'
      FROM events e JOIN signal_reports s ON e.id = s.event_id
     WHERE e.signal_date = '$SESSION_DATE' AND s.id > $last
     ORDER BY s.id;" 2>/dev/null)
  if [ -n "$rows" ]; then
    while IFS='|' read -r id fired tick sig px side lvl k d kf atr vix spx ch dopen rev gap; do
      [ -z "$id" ] && continue
      last=$id
      echo "$fired,$tick,$sig,$px,$side,$lvl,$k,$d,$kf,$atr,$vix,$spx,{$ch},$dopen,$rev,$gap" >> "$CSV"
      case "$sig" in
        confluence_*)
          n=$((n+1))
          echo "[CONFLUENCE #$n] $fired"
          echo "  $tick $sig | Price: $px | K: $k D: $d | VIX: $vix"
          ;;
      esac
    done <<< "$rows"
  fi
  sleep 20
done

wait "$POLLER"; rc=$?
say STOP "Session ended (exit $rc). $n confluence signal(s), $(( $(wc -l < "$CSV") - 1 )) row(s) in $CSV"
