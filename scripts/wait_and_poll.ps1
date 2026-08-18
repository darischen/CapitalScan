param(
    [int]$PollIntervalSeconds = 300
)

$marketOpen = [TimeSpan]"06:30:00"  # 9:30 AM ET = 6:30 AM PT
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
        Write-Host "[WAIT] $(Get-Date -Format 'HH:mm:ss') - Waiting for market open (6:30 AM PT). Opens in ~${minutesUntilOpen}m ${secondsUntilOpen}s"
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

    # Start poller in background
    $pollerProcess = Start-Process -FilePath "uv" -ArgumentList "run", "cscan", "poll", "--interval", $PollIntervalSeconds -PassThru -NoNewWindow
    Write-Host "[MONITOR] Poller started (PID: $($pollerProcess.Id)). Monitoring for confluence signals..."

    $env:PGPASSWORD = "capscan"
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
    $csvHeader = "fired_at_pt,ticker,signal_type,entry_price,side,touch_level,k_full,d_full,k_fast,atr_14,vix_close,spx_ret_1d,channels_sent"
    $csvHeader | Out-File -FilePath $csvPath -Encoding UTF8

    # Monitor for new events
    while (-not $pollerProcess.HasExited) {
        Start-Sleep -Seconds 3

        try {
            # Query for new events since last check.
            #
            # The three-step conversion is not redundant, and it works around a
            # defect rather than expressing a preference.
            # `jobs/poll.py::_now_et` returns a **naive** datetime holding ET
            # wall-clock time. `signal_reports.fired_at` is `timestamptz` and the
            # session runs on `Etc/UTC` (SHOW timezone), so Postgres reads that
            # naive ET value as UTC and stores it with a `+00` offset it never
            # had. Every row is ET wearing a UTC label.
            #
            # So: strip the false label (AT TIME ZONE 'UTC' -> naive ET), attach
            # the true one (AT TIME ZONE 'America/New_York'), then convert to
            # Pacific. A single `AT TIME ZONE 'America/Los_Angeles'` trusts the
            # label and lands three hours early -- 09:42 PT rendered as 05:42,
            # before the market opened, which is how this was noticed.
            #
            # Named zones, not fixed offsets, so DST comes from Postgres tzdata.
            # ORDER BY stays on the raw `timestamptz`, which sorts identically.
            # Delete this chain if `_now_et` is fixed to return an aware
            # datetime -- it becomes wrong the moment the stored data is right.
            $query = "SELECT s.event_id, ((s.fired_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York' AT TIME ZONE 'America/Los_Angeles'), e.ticker, e.signal_type, e.entry_price::text, e.side, e.touch_level::text, e.k_full::text, e.d_full::text, e.k_fast::text, e.atr_14::text, e.vix_close::text, e.spx_ret_1d::text, s.channels_sent FROM events e JOIN signal_reports s ON e.id = s.event_id WHERE DATE(e.signal_date) = CURRENT_DATE AND e.id > $lastEventId ORDER BY s.fired_at ASC;"

            $newEvents = & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h localhost -U capscan -d capitalscan -t -c $query

            if ($newEvents) {
                $newEvents | ForEach-Object {
                    if ($_ -match '\|') {
                        $parts = $_ -split '\|' | ForEach-Object { $_.Trim() }

                        if ($parts.Count -ge 14) {
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

                                    # Print to terminal
                                    Write-Host "[CONFLUENCE #$confluenceCount] $firedAt" -ForegroundColor Green
                                    Write-Host "  $ticker $signalType | Price: $entryPrice | K: $kFull D: $dFull | VIX: $vix" -ForegroundColor Cyan

                                    # Write to CSV
                                    "$firedAt,$ticker,$signalType,$entryPrice,$side,$touchLevel,$kFull,$dFull,$kFast,$atr,$vix,$spxRet,$channels" | Out-File -FilePath $csvPath -Encoding UTF8 -Append
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
