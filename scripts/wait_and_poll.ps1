param(
    [int]$PollIntervalSeconds = 300
)

# **This writes the serving store, exactly like the Pi (ADR 158).** It used
# to run `cscan poll` bare -- writing research and pushing to serving per
# tick -- which is a different code path with different failure modes: no
# staleness guard, no sequence guard, and a research->serving push that
# breaks on an orphaned run row from a crashed-then-restarted session (hit
# 2026-08-31). Passing `--serving` makes a manual run here byte-for-byte the
# same operation the Pi's `wait_and_poll.sh` performs, so a workstation
# fallback for a day the Pi skipped behaves identically -- including
# refusing for the same reasons.
#
# The two must never run at once: both write serving directly and would
# double-write. The Pi's systemd unit is a `oneshot` that has finished (or
# failed) for the day by ~13:00, so a later manual run here is safe.
$ServingHost = "192.168.1.30"
$ServingDb   = "capitalscan_serving"

# Start 15 minutes after the bell (user's decision, 2026-08-24). The open
# is still 06:30 PT; this is when we begin polling it.
#
# **This caps `coverage_pct` at ~96% permanently.** `poll.py` computes
# `ticks_expected = SESSION_SECONDS / interval`, a fixed 78 ticks for a
# 06:30-13:00 session, independent of when the poller actually starts. From
# 06:45 the most that can complete is 75. That is honest -- there really is
# a 15-minute gap -- but ADR 084 makes `poller_sessions.coverage_pct` the
# record a Phase 6 analysis reads to tell "no coverage" from "no signals",
# so a permanent 4% shortfall is a number that has to be read knowing this.
$marketOpen = [TimeSpan]"06:45:00"  # 9:45 AM ET = 6:45 AM PT
$marketClose = [TimeSpan]"13:00:00" # 4:00 PM ET = 1:00 PM PT

function Get-TimeLocal {
    return (Get-Date).TimeOfDay
}

function Is-Confluence {
    param([string]$SignalType)

    # Confluence signals are marked as "confluence_high" or "confluence_low"
    if ($SignalType -match "^confluence_") {
        return $true
    }
    return $false
}

# **Refuse a non-trading day, before the wait (mirrors the Pi script).**
# `cscan poll` checks only the time of day, not the date, so an unguarded
# weekend or holiday run would open a `poller_sessions` row and fire signals
# off the previous session's stale quotes into the store the site serves.
# `trading_days` is the same calendar `cscan poll` resolves the last trading
# day from. Queried on serving because that is the store this polls.
$env:PGPASSWORD = "capscan"
$today = Get-Date -Format 'yyyy-MM-dd'
$isTradingDay = & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h $ServingHost -U capscan -d $ServingDb -tA -c "SELECT 1 FROM trading_days WHERE d = '$today'"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] $(Get-Date -Format 'HH:mm:ss') - cannot reach serving ($ServingHost/$ServingDb) to check the calendar. Not polling."
    exit 1
}
if ("$isTradingDay".Trim() -ne "1") {
    Write-Host "[SKIP] $(Get-Date -Format 'HH:mm:ss') - $today is not a trading day. Nothing to poll; exiting cleanly."
    exit 0
}

# Main loop: keep checking until market opens, run poller, export results
while ($true) {
    $currentTime = Get-TimeLocal

    # Check if market has closed
    if ($currentTime -gt $marketClose) {
        Write-Host "[STOP] Market closed at 1:00 PM PT. Stopping."
        exit 0
    }

    # Before market open: wait
    if ($currentTime -lt $marketOpen.Add([TimeSpan]::FromSeconds(30))) {
        $timeUntilOpen = $marketOpen.Add([TimeSpan]::FromSeconds(30)) - $currentTime
        $minutesUntilOpen = [math]::Floor($timeUntilOpen.TotalMinutes)
        $secondsUntilOpen = [math]::Floor($timeUntilOpen.TotalSeconds % 60)
        Write-Host "[WAIT] $(Get-Date -Format 'HH:mm:ss') - Waiting for poll start (6:45 AM PT, 15m after the 6:30 open). Starts in ~${minutesUntilOpen}m ${secondsUntilOpen}s"
        Start-Sleep -Seconds 10
        continue
    }

    # Check if we already ran the poller today
    $todaysCsv = Get-ChildItem -Path "C:\Users\daris\Desktop\School\CapitalScan\reports\poller" -Filter "poller_session_$(Get-Date -Format 'yyyy_MM_dd')_*.csv" -ErrorAction SilentlyContinue

    if ($todaysCsv) {
        Write-Host "[OK] $(Get-Date -Format 'HH:mm:ss') - Poller already ran today. File: $($todaysCsv.Name)"
        exit 0
    }

    # Launch the poller
    Write-Host "[START] $(Get-Date -Format 'HH:mm:ss') - Launching poller. Will run until 1:00 PM PT"
    # `reports/poller/`, split from `reports/scan/` on 2026-08-18.
    # PowerShell does not create a parent directory for a path it is only
    # about to write to, and Export-Csv fails on a missing directory rather
    # than creating one -- so a fresh clone would lose the entire session
    # log to a path that does not exist yet.
    $pollerDir = "C:\Users\daris\Desktop\School\CapitalScan\reports\poller"
    if (-not (Test-Path $pollerDir)) { New-Item -ItemType Directory -Force $pollerDir | Out-Null }
    $csvPath = "C:\Users\daris\Desktop\School\CapitalScan\reports\poller\poller_session_$(Get-Date -Format 'yyyy_MM_dd_HHmmss').csv"

    # Start poller in background. `--serving` writes the serving store
    # directly, the same as the Pi -- no research write, no per-tick push.
    $pollerProcess = Start-Process -FilePath "uv" -ArgumentList "run", "cscan", "poll", "--interval", $PollIntervalSeconds, "--serving" -PassThru -NoNewWindow
    Write-Host "[MONITOR] Poller started (PID: $($pollerProcess.Id)). Monitoring for confluence signals..."

    $env:PGPASSWORD = "capscan"
    # **Reset to 0 on every start, and the query below filters on
    # `signal_date = CURRENT_DATE` rather than on "since this script
    # started". So launching at noon REPLAYS the whole day** -- every signal
    # already stored for today scrolls past and is written to the CSV,
    # ordered by `fired_at`.
    #
    # That looks like the poller inventing history and is not. This script
    # is two things at once: it launches the real poller (`cscan poll`,
    # above) AND polls the database every 3s to display and export. The
    # replay is the *display* half reading rows an earlier run wrote.
    # Nothing re-fires -- `cscan poll` debounces against today's fired set
    # in Postgres (DESIGN 4.8), and `channels_sent` is read from the stored
    # row, so no notification is re-sent.
    #
    # It is deliberate rather than tolerated: the CSV is the session record,
    # and deleting it to restart must reproduce a COMPLETE day rather than
    # only the part after the restart.
    $lastEventId = 0
    $confluenceCount = 0

    # CSV header
    # Header must match the SELECT below field-for-field. It did not until
    # 2026-08-06: it declared 15 columns (including bb_lower and bb_upper,
    # which are not columns on `events` at all) while the query selected and
    # the writer emitted 12. Every value after `side` therefore landed under
    # the wrong heading, shifted three places left — ANET's "bb_lower" of
    # 88.05 in the 2026-08-06 session was its k_full, which is why an
    # overbought confluence_high looked like it had a low band reading.
    #
    # bb_lower/bb_upper are dropped rather than joined in from `indicators`:
    # that table is read at t-1 for signal purposes (invariant 3), so
    # showing its t-dated row next to an event would put two different
    # timestamps in one line. `touch_level` is the band level the signal
    # actually fired against, it lives on `events`, and it is the honest
    # answer to the question bb_lower/bb_upper were there to ask.
    # `fired_at_pt`, not `fired_at`: the column below is converted out of UTC
    # by the query, so the name has to say which clock it is on. Every other
    # timestamp this script prints comes from `Get-Date` on a machine set to
    # Pacific, and an unlabelled UTC column sitting next to those read as the
    # same clock three hours off — 12:42 in the 2026-08-10 session was 05:42
    # PT, before the market had opened.
    # `day_open`, `reversal`, and `open_gap_atr` added 2026-08-18 (ADR 117).
    # The reversal rule is bb_upper <= price < open; the CSV now carries all
    # three numbers so a near miss can be judged after the session instead
    # of only at the moment the alert scrolled past.
    $csvHeader = "fired_at_pt,ticker,signal_type,entry_price,side,touch_level,k_full,d_full,k_fast,atr_14,vix_close,spx_ret_1d,channels_sent,day_open,reversal,open_gap_atr"
    $csvHeader | Out-File -FilePath $csvPath -Encoding UTF8

    # Monitor for new events
    while (-not $pollerProcess.HasExited) {
        Start-Sleep -Seconds 3

        try {
            # Query for new events since last check.
            #
            # One conversion. `fired_at` is a true instant now (ADR 127):
            # `_now_et` returns an aware datetime, and
            # `scripts/backfill_poller_timestamps.py` corrected the 1,752
            # rows written before the fix.
            #
            # It was a three-step chain -- strip a false UTC label,
            # reinterpret as ET, convert to Pacific -- compensating for
            # naive ET values stored in a `timestamptz`. That is why this
            # CSV read correctly while the screener, which formats in ET,
            # showed a 09:30 open as 05:30.
            #
            # The chain and the backfill were coupled: either alone moves
            # this CSV four hours. They landed together.
            #
            # Named zone, not a fixed offset, so DST comes from Postgres
            # tzdata. ORDER BY stays on the raw `timestamptz`.
            # `signal_date = '$today'` with the session's own date, never
            # CURRENT_DATE (ADR 119): the store's server timezone is not
            # guaranteed to be the session's, and relying on the window
            # falling inside one server-day is how that bug keeps returning.
            $query = "SELECT s.event_id, (s.fired_at AT TIME ZONE 'America/Los_Angeles'), e.ticker, e.signal_type, e.entry_price::text, e.side, e.touch_level::text, e.k_full::text, e.d_full::text, e.k_fast::text, e.atr_14::text, e.vix_close::text, e.spx_ret_1d::text, s.channels_sent, s.state_json->>'day_open', s.state_json->'bear_reversal'->>'confirmed', s.state_json->'bear_reversal'->>'open_gap_atr' FROM events e JOIN signal_reports s ON e.id = s.event_id WHERE e.signal_date = '$today' AND e.id > $lastEventId ORDER BY s.fired_at ASC;"

            $newEvents = & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h $ServingHost -U capscan -d $ServingDb -t -c $query

            if ($newEvents) {
                $newEvents | ForEach-Object {
                    if ($_ -match '\|') {
                        $parts = $_ -split '\|' | ForEach-Object { $_.Trim() }

                        if ($parts.Count -ge 17) {
                            $eventId = $parts[0]
                            $firedAt = $parts[1]
                            $ticker = $parts[2]
                            $signalType = $parts[3]

                            if ([int]$eventId -gt $lastEventId) {
                                $lastEventId = [int]$eventId

                                if (Is-Confluence -SignalType $signalType) {
                                    $confluenceCount++
                                    $entryPrice = $parts[4]
                                    $side = $parts[5]
                                    $touchLevel = $parts[6]
                                    $kFull = $parts[7]
                                    $dFull = $parts[8]
                                    $kFast = $parts[9]
                                    $atr = $parts[10]
                                    $vix = $parts[11]
                                    $spxRet = $parts[12]
                                    $channels = $parts[13]
                                    $dayOpen = $parts[14]
                                    $revConfirmed = $parts[15]
                                    $openGapAtr = $parts[16]

                                    # ADR 117: every confluence still shows.
                                    # What changed is that a short-side one
                                    # says where it sits relative to the
                                    # reversal rule instead of saying nothing
                                    # when it misses.
                                    $revLabel = "n/a"
                                    $revColor = "Cyan"
                                    if ($signalType -eq "confluence_high") {
                                        if ($revConfirmed -eq "true") {
                                            $revLabel = "REVERSAL"
                                            $revColor = "Yellow"
                                        } elseif ($openGapAtr) {
                                            # Signed, so the number carries the
                                            # verdict on its own: negative is
                                            # below the open and therefore a
                                            # reversal, positive is not.
                                            $gap = [math]::Round([double]$openGapAtr, 2)
                                            $revLabel = "no reversal (+$gap ATR vs open)"
                                            if ($gap -lt 0) { $revLabel = "no reversal ($gap ATR vs open)" }
                                            $revColor = "DarkYellow"
                                        } else {
                                            $revLabel = "no reversal (open unavailable)"
                                            $revColor = "DarkYellow"
                                        }
                                    }

                                    # Print to terminal
                                    Write-Host "[CONFLUENCE #$confluenceCount] $firedAt" -ForegroundColor Green
                                    Write-Host "  $ticker $signalType | Price: $entryPrice | K: $kFull D: $dFull | VIX: $vix" -ForegroundColor Cyan
                                    if ($signalType -eq "confluence_high") {
                                        Write-Host "  band $touchLevel | open $dayOpen | $revLabel" -ForegroundColor $revColor
                                    }

                                    # Write to CSV
                                    "$firedAt,$ticker,$signalType,$entryPrice,$side,$touchLevel,$kFull,$dFull,$kFast,$atr,$vix,$spxRet,$channels,$dayOpen,$revConfirmed,$openGapAtr" | Out-File -FilePath $csvPath -Encoding UTF8 -Append
                                }
                            }
                        }
                    }
                }
            }
        } catch {
            Write-Host "[QUERY ERROR] $(Get-Date -Format 'HH:mm:ss') - $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    Write-Host ""
    Write-Host "[OK] $(Get-Date -Format 'HH:mm:ss') - Poller finished"
    Write-Host "[RESULTS] Total confluence signals: $confluenceCount"
    Write-Host "[CSV] Results saved to: $csvPath"

    exit 0
}
