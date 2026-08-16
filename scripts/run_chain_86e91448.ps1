# Full regeneration chain for config_hash 86e91448a65aa40b
# (stoch_source = k_fast, require_fast_agreement = True).
#
# Run detached. Every step appends to one log with a timestamped banner, so
# a reader can tell which step is live and how long each took without
# consulting the `runs` table -- which times only the write phase of a
# backtest and does not time the harness at all.
#
# Sequential by construction: `path peak-labels` reads what `path backfill`
# writes, and every `stats` command reads what the backtest writes. A step
# that exits non-zero stops the chain rather than feeding a partial table
# into the next one.

$ErrorActionPreference = "Continue"
$repo = "C:\Users\daris\Desktop\School\CapitalScan"
$cscan = "$repo\.venv\Scripts\cscan.exe"
$log = "$repo\reports\chain_86e91448a65aa40b.log"

Set-Location $repo

function Invoke-Step {
    param([string]$Name, [string[]]$CliArgs)

    $started = Get-Date
    Add-Content $log ""
    Add-Content $log "================================================================"
    Add-Content $log "STEP  : $Name"
    Add-Content $log "ARGS  : $($CliArgs -join ' ')"
    Add-Content $log "START : $($started.ToString('yyyy-MM-dd HH:mm:ss'))"
    Add-Content $log "================================================================"

    & $cscan @CliArgs 2>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE

    $ended = Get-Date
    $dur = $ended - $started
    Add-Content $log "END   : $($ended.ToString('yyyy-MM-dd HH:mm:ss'))  elapsed=$($dur.ToString())  exit=$code"

    if ($code -ne 0) {
        Add-Content $log "CHAIN ABORTED: '$Name' exited $code. Later steps read what this one writes."
        exit $code
    }
}

Add-Content $log "################################################################"
Add-Content $log "CHAIN START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  target_hash=86e91448a65aa40b"
Add-Content $log "################################################################"

# ORDER MATTERS, and getting it wrong cost most of a day on 2026-08-15.
#
# `cscan events` is deliberately NOT run first. `run_backtest` writes the
# events itself -- 627,668 rows across all four entry kinds, full history --
# whereas `cscan events` writes 157,168 detection-only rows with one entry
# kind and no returns. The backtest's population is a strict superset, so
# running events first costs 2h20m and is then overwritten.
#
# Worse, `path` keys on `event_id` with an FK to `events`, and the backtest
# mints fresh ids. On 2026-08-15 `path backfill` ran before the backtest,
# spent 2h46m, and every row it wrote was orphaned three minutes later.
# `path peak-labels` then reported rows_updated=0 because it resolved to a
# hash whose events did not exist yet.
#
# Backtest first, then path, then peak-labels, then stats.
Invoke-Step "backtest"         @("backtest", "--workers", "8")
Invoke-Step "path backfill"    @("path", "backfill", "--workers", "8", "--quiet")
Invoke-Step "path peak-labels" @("path", "peak-labels")
Invoke-Step "stats rho"          @("stats", "rho")
Invoke-Step "stats cells"        @("stats", "cells")
Invoke-Step "stats benchmarks"   @("stats", "benchmarks")
Invoke-Step "stats self-validate" @("stats", "self-validate")

Add-Content $log ""
Add-Content $log "################################################################"
Add-Content $log "CHAIN COMPLETE $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Content $log "################################################################"
