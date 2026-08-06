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
        Write-Host "[WAIT] $(Get-Date -Format 'HH:mm:ss') - Waiting for market open (6:30 AM PT / 9:30 AM ET). Opens in ~${minutesUntilOpen}m ${secondsUntilOpen}s"
        Start-Sleep -Seconds 10
        continue
    }

    # Check if we already ran the poller today
    $todaysCsv = Get-ChildItem -Path "C:\Users\daris\Desktop\School\CapitalScan\reports" -Filter "poller_session_$(Get-Date -Format 'yyyy_MM_dd')_*.csv" -ErrorAction SilentlyContinue

    if ($todaysCsv) {
        Write-Host "[OK] $(Get-Date -Format 'HH:mm:ss') - Poller already ran today. File: $($todaysCsv.Name)"
        exit 0
    }

    # Launch the poller
    Write-Host "[START] $(Get-Date -Format 'HH:mm:ss') - Launching poller. Will run until 4:00 PM ET"
    $csvPath = "C:\Users\daris\Desktop\School\CapitalScan\reports\poller_session_$(Get-Date -Format 'yyyy_MM_dd_HHmmss').csv"

    # Start poller in background
    $pollerProcess = Start-Process -FilePath "uv" -ArgumentList "run", "cscan", "poll", "--interval", $PollIntervalSeconds -PassThru -NoNewWindow
    Write-Host "[MONITOR] Poller started (PID: $($pollerProcess.Id)). Monitoring for confluence signals..."

    $env:PGPASSWORD = "capscan"
    $lastEventId = 0
    $confluenceCount = 0

    # CSV header
    $csvHeader = "fired_at,ticker,signal_type,entry_price,side,bb_lower,bb_upper,touch_level,k_full,d_full,k_fast,atr_14,vix_close,spx_ret_1d,channels_sent"
    $csvHeader | Out-File -FilePath $csvPath -Encoding UTF8

    # Monitor for new events
    while (-not $pollerProcess.HasExited) {
        Start-Sleep -Seconds 3

        try {
            # Query for new events since last check
            $query = "SELECT s.event_id, s.fired_at, e.ticker, e.signal_type, e.entry_price::text, e.side, e.k_full::text, e.d_full::text, e.k_fast::text, e.atr_14::text, e.vix_close::text, e.spx_ret_1d::text, s.channels_sent FROM events e JOIN signal_reports s ON e.id = s.event_id WHERE DATE(e.signal_date) = CURRENT_DATE AND e.id > $lastEventId ORDER BY s.fired_at ASC;"

            $newEvents = & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h localhost -U capscan -d capitalscan -t -c $query

            if ($newEvents) {
                $newEvents | ForEach-Object {
                    if ($_ -match '\|') {
                        $parts = $_ -split '\|' | ForEach-Object { $_.Trim() }

                        if ($parts.Count -ge 13) {
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
                                    $kFull = $parts[6]
                                    $dFull = $parts[7]
                                    $kFast = $parts[8]
                                    $atr = $parts[9]
                                    $vix = $parts[10]
                                    $spxRet = $parts[11]
                                    $channels = $parts[12]

                                    # Print to terminal
                                    Write-Host "[CONFLUENCE #$confluenceCount] $firedAt" -ForegroundColor Green
                                    Write-Host "  $ticker $signalType | Price: $entryPrice | K: $kFull D: $dFull | VIX: $vix" -ForegroundColor Cyan

                                    # Write to CSV
                                    "$firedAt,$ticker,$signalType,$entryPrice,$side,$kFull,$dFull,$kFast,$atr,$vix,$spxRet,$channels" | Out-File -FilePath $csvPath -Encoding UTF8 -Append
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
