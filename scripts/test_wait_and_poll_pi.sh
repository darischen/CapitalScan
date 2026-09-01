#!/usr/bin/env bash
# Exercises `scripts/pi/wait_and_poll.sh`'s preflight guards inside a
# throwaway container, without touching the Pi, serving, or research.
#
# **The Linux counterpart of `scripts/test_wait_and_poll.ps1`**, and it needs
# a different trick. The `.ps1` reads its target out of
# `DATABASE_URL_SERVING`, so a scratch `.env.local` redirects it. The `.sh`
# has no configuration at all: it hardcodes `capitalscan_serving` and reaches
# it with `sudo -u postgres` peer auth. So isolation means a container that
# really has a `postgres` user and a database of that name -- which is what
# `postgres:17` already is.
#
# The fake repo root still does the rest: the script does
# `cd "$(dirname "$0")/../.."`, so dropping it at `/repo/scripts/pi/` makes
# `/repo` its working directory and every `reports/poller/` path lands in
# scratch.
#
# **`TZ` is how the clock branches are made deterministic.** The script
# compares `date` against a fixed 06:45/13:00 window, so running it under a
# timezone where "now" is 02:00 exercises the countdown, and one where it is
# 22:00 exercises the already-closed path. No clock is changed and no sleep
# is needed.
#
# **What this cannot cover.** The polling loop needs live quotes, and the
# staleness and sequence guards live inside `cscan poll --serving` rather
# than in this script (they have unit tests, and both fired against
# production on 2026-08-31 and 2026-09-01).
#
# Usage:  bash scripts/test_wait_and_poll_pi.sh
set -uo pipefail

CONTAINER="capitalscan-waitpoll-sh-test"
IMAGE="postgres:17"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

DOCKER="docker"
command -v docker >/dev/null 2>&1 || DOCKER="/c/Program Files/Docker/Docker/resources/bin/docker.exe"
[ -x "$DOCKER" ] || { command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }; }

PASSED=0
FAILED=0

ok()   { echo "  PASS  $1"; PASSED=$((PASSED+1)); }
bad()  { echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        ${2}"; FAILED=$((FAILED+1)); }
check() { if [ "$2" = "yes" ]; then ok "$1"; else bad "$1" "${3:-}"; fi; }

dexec() { "$DOCKER" exec "$CONTAINER" bash -lc "$1"; }

cleanup() { "$DOCKER" rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "=== throwaway $IMAGE container ==="
cleanup
"$DOCKER" run -d --name "$CONTAINER" \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=capitalscan_serving "$IMAGE" >/dev/null || { echo "could not start"; exit 1; }

echo "waiting for postgres..."
for _ in $(seq 1 40); do
  sleep 1
  if dexec "pg_isready -q" >/dev/null 2>&1; then break; fi
done
dexec "pg_isready -q" >/dev/null 2>&1 || { echo "never became ready"; exit 1; }

# `sudo` is not in the postgres image; the script's guard shells out to it.
echo "installing sudo..."
dexec "apt-get update -qq && apt-get install -y -qq sudo >/dev/null 2>&1" || true
dexec "command -v sudo" >/dev/null 2>&1 || { echo "sudo install failed"; exit 1; }

# The one table the guard reads, and a stub `cscan` so the START path has
# something to exec if a scenario reaches it. Deliberately not the real
# schema: if the guard needs more than this, it is reading something its
# comment does not admit to.
dexec "sudo -u postgres psql -d capitalscan_serving -X -q -c 'CREATE TABLE trading_days (d date PRIMARY KEY);'" >/dev/null

dexec "mkdir -p /repo/scripts/pi /repo/reports/poller /repo/.venv/bin"
"$DOCKER" cp "$REPO/scripts/pi/wait_and_poll.sh" "$CONTAINER:/repo/scripts/pi/wait_and_poll.sh" >/dev/null
dexec "chmod +x /repo/scripts/pi/wait_and_poll.sh"
# A stub that exits immediately, so reaching START does not hang the harness
# and cannot reach a network.
dexec "printf '#!/bin/sh\nexit 0\n' > /repo/.venv/bin/cscan && chmod +x /repo/.venv/bin/cscan"
echo "ready."

# CAPSCAN_NO_TERM keeps the lxterminal branch out of the way; it is a
# best-effort GUI convenience the script itself guards.
run_script() {  # $1 = TZ, $2 = timeout seconds
  "$DOCKER" exec -e CAPSCAN_NO_TERM=1 -e "TZ=$1" "$CONTAINER" \
    bash -lc "cd /repo && timeout ${2} bash scripts/pi/wait_and_poll.sh 2>&1; echo \"EXIT=\$?\"" 2>&1
}

# **Timezone offsets are computed, not hardcoded.** The first version picked
# `Etc/GMT+9` and `Etc/GMT-14` by hand against the wall clock at the time,
# and two scenarios silently tested the wrong branch when run later in the
# day. `tz_for_hour` asks the container what time it is and derives the
# offset that puts local time where the scenario needs it.
tz_for_hour() {  # $1 = desired local hour (0-23)
  local want=$1
  local utc_h
  utc_h=$(dexec "date -u +%-H" | tr -d '\r')
  utc_h=${utc_h:-0}
  local off=$(( (want - utc_h + 24) % 24 ))
  # POSIX `Etc/GMT-N` is UTC+N. Offsets run -14..+12, so express anything
  # past +14 as the negative side of the dial.
  if [ "$off" -le 14 ]; then echo "Etc/GMT-${off}"; else echo "Etc/GMT+$(( 24 - off ))"; fi
}

seed_calendar() {
  # **A window of dates, so the timezone only moves the clock.** Shifting the
  # offset can roll the local date forward or back, and with a single seeded
  # date that turns a clock test into a calendar test -- which is exactly how
  # the first run produced a `[SKIP]` where it expected `[STOP]`.
  dexec "sudo -u postgres psql -d capitalscan_serving -X -q -c \"INSERT INTO trading_days SELECT generate_series(current_date - 2, current_date + 2, '1 day')::date ON CONFLICT DO NOTHING;\"" >/dev/null
}

echo
echo "--- a closed day must skip cleanly ---"
dexec "sudo -u postgres psql -d capitalscan_serving -X -q -c 'DELETE FROM trading_days;'" >/dev/null
OUT=$(run_script "UTC" 20)
case "$OUT" in *"[SKIP]"*) check "says [SKIP] on a non-trading day" yes ;; *) check "says [SKIP] on a non-trading day" no "$OUT" ;; esac
case "$OUT" in *"EXIT=0"*) check "exits 0 on a non-trading day" yes ;; *) check "exits 0 on a non-trading day" no "$OUT" ;; esac
case "$OUT" in *"[START]"*) check "does not launch the poller" no "$OUT" ;; *) check "does not launch the poller" yes ;; esac

seed_calendar

echo
echo "--- before the open it must count down, not skip ---"
OUT=$(run_script "$(tz_for_hour 2)" 12)
case "$OUT" in *"[WAIT]"*) check "counts down to the open" yes ;; *) check "counts down to the open" no "$OUT" ;; esac
case "$OUT" in *"[SKIP]"*) check "does not skip a real session" no "$OUT" ;; *) check "does not skip a real session" yes ;; esac

echo
echo "--- after the close it must stop, not poll ---"
OUT=$(run_script "$(tz_for_hour 22)" 20)
case "$OUT" in *"[STOP]"*) check "stops when the market has already closed" yes ;; *) check "stops when the market has already closed" no "$OUT" ;; esac
case "$OUT" in *"EXIT=0"*) check "exits 0 after the close" yes ;; *) check "exits 0 after the close" no "$OUT" ;; esac

echo
echo "--- an unreachable database must NOT look like a closed market ---"
# **The sharp one, and it needs the database to be genuinely down.** The
# first version called `pg_ctl stop` without checking, the server stayed up,
# and all three assertions passed vacuously against a scenario that never
# happened. Verified with `pg_isready` before the run.
#
# Local time is set inside the 06:45-13:00 window so a guard that *passes*
# would go on to `[START]`. That makes the outcomes distinguishable:
# `[START]` means it polled blind, `[SKIP]` + exit 0 means it called a dead
# database a holiday, anything else means it refused.
# **Login is denied rather than the server stopped, and the reason matters.**
# In `postgres:17` the server is PID 1, so stopping it kills the container
# and every later step with it. `ALTER ROLE postgres NOLOGIN` produces the
# same thing the script actually experiences -- a `psql` that cannot
# connect and prints nothing to stdout -- while the container survives.
#
# Irreversible for this run (there is no second superuser to undo it), which
# is fine: this is the last scenario and the container is destroyed after.
#
# **Verified by a real connection, not `pg_isready`.** `pg_isready` checks
# that the port answers, not that anyone can log in, so it would report
# success here and hand back another vacuous pass -- which is the mistake
# the previous version of this block made in a different form.
dexec "sudo -u postgres psql -d capitalscan_serving -X -q -c 'ALTER ROLE postgres NOLOGIN;'" >/dev/null 2>&1
if dexec "sudo -u postgres psql -d capitalscan_serving -X -tA -c 'SELECT 1'" >/dev/null 2>&1; then
  bad "the harness could not make the database unreachable, so this scenario is untested"       "psql still connects"
else
  ok "the database is genuinely unreachable for this scenario"
  OUT=$(run_script "$(tz_for_hour 9)" 20)
  case "$OUT" in
    *"[SKIP]"*) check "does not report a closed market when the database is unreachable" no         "reported [SKIP] with the database unreachable -- it cannot tell a dead database from a holiday: ${OUT}" ;;
    *)          check "does not report a closed market when the database is unreachable" yes ;;
  esac
  case "$OUT" in
    *"EXIT=0"*) check "does not exit 0 when it cannot read the calendar" no         "exited 0 with the database unreachable, so systemd records success: ${OUT}" ;;
    *)          check "does not exit 0 when it cannot read the calendar" yes ;;
  esac
  case "$OUT" in *"[START]"*) check "fails closed: no poll on an unknown calendar" no "$OUT" ;; *) check "fails closed: no poll on an unknown calendar" yes ;; esac
fi

echo
echo "=== $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
