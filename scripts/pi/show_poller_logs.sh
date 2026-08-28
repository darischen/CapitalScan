#!/usr/bin/env bash
# Open a terminal showing the poller's most recent session, on desktop login.
#
# **Why this exists separately from the poller itself.** The timer fires at
# 00:00, when nobody is connected, so xrdp's display does not exist yet and
# the wrapper's own window lands on the console where it cannot be seen. The
# log is always written; this is what makes it visible whenever someone
# actually connects.
#
# Tails rather than cats, so a session still in progress keeps updating in
# the window, and `-n +1` shows the whole day from the start rather than the
# last few lines.
LOGDIR="$HOME/CapitalScan/reports/poller"
latest=$(ls -1t "$LOGDIR"/poller_20*.log 2>/dev/null | head -1)
[ -z "$latest" ] && exit 0

# Do not stack a second window on the same file across reconnects.
pgrep -af "tail -n \+1 -f $latest" >/dev/null 2>&1 && exit 0

exec lxterminal --title="CapitalScan poller — $(basename "$latest" .log | sed 's/^poller_//')" \
     -e bash -c "tail -n +1 -f '$latest'"
