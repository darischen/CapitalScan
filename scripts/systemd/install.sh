#!/usr/bin/env bash
# Install the CapitalScan nightly / weekly / monthly systemd timers on a
# Linux research machine.
#
#   scripts/systemd/install.sh            # install + enable + start timers
#   scripts/systemd/install.sh --remove   # disable + delete them
#
# {{REPO}} and {{USER}} in each unit file are filled in from this script's
# location and $SUDO_USER (or $USER), so nothing is edited per machine.
# Needs root for the writes to /etc/systemd/system.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
UNIT_DIR=/etc/systemd/system
UNITS=(capitalscan-nightly capitalscan-weekly capitalscan-monthly)
REMOVE=0
[ "${1:-}" = "--remove" ] && REMOVE=1

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo $0 ${1:-}" >&2
  exit 1
fi

for u in "${UNITS[@]}"; do
  if [ "$REMOVE" -eq 1 ]; then
    systemctl disable --now "${u}.timer" 2>/dev/null || true
    rm -f "${UNIT_DIR}/${u}.service" "${UNIT_DIR}/${u}.timer"
    echo "removed  ${u}"
    continue
  fi
  for kind in service timer; do
    sed -e "s|{{REPO}}|${REPO}|g" -e "s|{{USER}}|${RUN_USER}|g" \
      "$(dirname "$0")/${u}.${kind}" > "${UNIT_DIR}/${u}.${kind}"
  done
  echo "installed ${u}  (User=${RUN_USER}, WorkingDirectory=${REPO})"
done

systemctl daemon-reload

if [ "$REMOVE" -eq 1 ]; then
  echo "done. timers removed."
  exit 0
fi

for u in "${UNITS[@]}"; do
  systemctl enable --now "${u}.timer"
done

echo
echo "verify:  systemctl list-timers 'capitalscan-*'"
echo "run now: sudo systemctl start capitalscan-nightly.service && journalctl -fu capitalscan-nightly"
