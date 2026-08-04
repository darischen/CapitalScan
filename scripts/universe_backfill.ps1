<#
.SYNOPSIS
    Backfill the `universe` table across historical quarters.

.DESCRIPTION
    `cscan universe` evaluates one quarter per invocation, and `universe` held
    only a single evaluated quarter (as_of 2026-06-30). That made ADR 014's
    trade filter inert for the whole study period: `core.universe.in_trade`
    fails open when no evaluation exists on or before the signal date, so every
    ticker passed before 2026-06-30 and only 39 passed after — a cliff, not a
    filter.

    Runs quarters SEQUENTIALLY on purpose. Each `run_universe` still issues
    thousands of queries after the memoization fix (commit 5bf5aec), and
    parallel quarters would contend on the connection pool while writing
    overlapping `runs` rows.

    Do NOT run this while a backtest is running. Not a locking problem —
    Postgres MVCC handles that — but a determinism one: parallel backtest
    workers resolving eligibility against a `universe` table that changes
    mid-run produce different output for the same config, violating ADR 060.

.PARAMETER StartFrom
    Resume at this quarter (e.g. "2014Q3") instead of the beginning. Quarters
    before it are skipped. Use this after an interrupted run.

.PARAMETER EndAt
    Stop after this quarter. Defaults to the last completed quarter.

.EXAMPLE
    .\scripts\universe_backfill.ps1

.EXAMPLE
    .\scripts\universe_backfill.ps1 -StartFrom 2019Q2
#>
[CmdletBinding()]
param(
    [ValidatePattern('^\d{4}Q[1-4]$')]
    [string] $StartFrom,

    [ValidatePattern('^\d{4}Q[1-4]$')]
    [string] $EndAt
)

$ErrorActionPreference = 'Stop'

# 2010Q1 is ADR 035's event-study start. The upper bound stops at the last
# quarter that has actually ended: `run_universe` raises FutureQuarterError on
# anything later, because ADR 014's filter is causal and a quarter cannot be
# evaluated on data from inside itself.
$firstYear = 2010
$today     = Get-Date
$lastYear  = $today.Year
$lastQtr   = [math]::Ceiling($today.Month / 3) - 1
if ($lastQtr -lt 1) { $lastYear -= 1; $lastQtr = 4 }

$quarters = foreach ($y in $firstYear..$lastYear) {
    foreach ($q in 1..4) {
        if ($y -eq $lastYear -and $q -gt $lastQtr) { continue }
        "${y}Q${q}"
    }
}

if ($StartFrom) {
    $idx = [array]::IndexOf($quarters, $StartFrom)
    if ($idx -lt 0) { throw "StartFrom '$StartFrom' is outside the range $($quarters[0])..$($quarters[-1])" }
    $quarters = $quarters[$idx..($quarters.Count - 1)]
}
if ($EndAt) {
    $idx = [array]::IndexOf($quarters, $EndAt)
    if ($idx -lt 0) { throw "EndAt '$EndAt' is outside the range $($quarters[0])..$($quarters[-1])" }
    $quarters = $quarters[0..$idx]
}

$total  = $quarters.Count
$log    = Join-Path (Get-Location) 'universe_backfill.log'
$failed = @()
$i      = 0
$run    = [Diagnostics.Stopwatch]::StartNew()

Write-Host ""
Write-Host ("universe backfill: {0} quarters, {1} .. {2}" -f $total, $quarters[0], $quarters[-1])
Write-Host ("job output -> {0}" -f $log)
Write-Host ""

foreach ($qtr in $quarters) {
    $i++
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Write-Host ("[{0,2}/{1}] {2} ... " -f $i, $total, $qtr) -NoNewline

    "=== $qtr ===" | Out-File -Append -Encoding utf8 $log
    # $ErrorActionPreference='Stop' does not apply to native exit codes, so a
    # failing quarter is caught by $LASTEXITCODE rather than an exception. One
    # bad quarter must not kill a 90-minute run.
    & uv run cscan universe --quarter $qtr | Out-File -Append -Encoding utf8 $log
    $exit = $LASTEXITCODE
    $sw.Stop()

    if ($exit -eq 0) {
        Write-Host ("ok {0,6:N1}s   (elapsed {1:hh\:mm\:ss})" -f $sw.Elapsed.TotalSeconds, $run.Elapsed)
    } else {
        Write-Host ("FAILED exit {0}   (see {1})" -f $exit, $log) -ForegroundColor Red
        $failed += $qtr
    }
}

$run.Stop()
Write-Host ""
Write-Host ("done: {0}/{1} succeeded in {2:hh\:mm\:ss}" -f ($total - $failed.Count), $total, $run.Elapsed)

if ($failed.Count -gt 0) {
    Write-Host ("failed: {0}" -f ($failed -join ', ')) -ForegroundColor Red
    Write-Host ("retry with: .\scripts\universe_backfill.ps1 -StartFrom {0}" -f $failed[0]) -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "verify with:" -ForegroundColor Cyan
Write-Host '  SELECT as_of, count(*) rows, count(*) FILTER (WHERE in_trade) in_trade'
Write-Host '  FROM universe GROUP BY as_of ORDER BY as_of;'
