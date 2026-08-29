# Nightly, for Task Scheduler. Runs after the 13:00 PT close.

#

# **Why 13:15 and not later.** The poller stops at 13:00, and nightly's

# first serving step is `pull_live_records` -- it brings the Pi's

# `signal_reports`, `poller_sessions` and poll `runs` rows back to research

# (ADR 158). Those live only on the Pi until this runs, so the sooner it

# runs after the close, the smaller that window.

#

# It is bounded to seven days of lookback, so a missed night self-heals on

# the next one. Missing several is survivable; missing them indefinitely is

# not, because research would stop accumulating what ADR 084 has Phase 6

# reading.

$ErrorActionPreference = "Stop"

Set-Location "C:\Users\daris\Desktop\School\CapitalScan"



$log = "reports\nightly_$(Get-Date -Format 'yyyy_MM_dd').log"



# **UTF-8, not PowerShell 5.1's default.** `Tee-Object` has no -Encoding in

# 5.1 and writes UTF-16LE, which unix tools read as NUL-separated gibberish

# -- `tail` on this log returned nothing readable, which is precisely when

# you most want to read it.

function Write-Log {

    param([Parameter(ValueFromPipeline = $true)] $Message)

    process {

        $line = "$Message"

        Write-Host $line

        Add-Content -Path $log -Value $line -Encoding utf8

    }

}



if (Test-Path $log) { Remove-Item $log }

"=== nightly start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Write-Log



# **Refuse to run under an ablation arm's config.** `cscan nightly` resolves

# config, so an arm left set would have nightly writing events under that

# arm's hash and sweeping the wrong generation. Cheap to check, and the

# failure is otherwise silent.

$hash = & .\.venv\Scripts\python.exe -c "from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))"

$hash = $hash.Trim()

if ($hash -ne "a38d3ca6b58295e8") {

    # **A stray `config.toml` is the likely cause and is safe to clear.**

    # `scripts/exit_sweep.py` writes one per arm and deletes it in a

    # `finally`, but a hard kill skips that -- observed twice on 2026-08-28.

    # The file is gitignored scratch, never hand-authored, so removing it can

    # only restore the baseline. `core/config.py` is a different matter and

    # is never touched here.

    #
    # **Only when no sweep is running.** A live `config.toml` belongs to an
    # arm in flight, and deleting it mid-run would let the remaining chunks
    # compute under the baseline hash -- writing an arm's events into the
    # serving generation. That is silent corruption and strictly worse than
    # a refused nightly, so a process check gates the removal.
    $sweeping = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                  Where-Object { $_.CommandLine -like '*exit_sweep*' }).Count
    $stray = Join-Path (Get-Location) 'config.toml'

    if ($sweeping -gt 0) {
        "config.toml present and a sweep is running ($sweeping process(es)); leaving it alone" | Write-Log
    }
    elseif (Test-Path $stray) {

        "found a stray config.toml (sweep arm left behind); removing it and re-resolving" | Write-Log

        Remove-Item $stray

        $hash = (& .\.venv\Scripts\python.exe -c "from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))").Trim()

    }

}

if ($hash -ne "a38d3ca6b58295e8") {

    "REFUSING: config resolves to $hash, not the serving generation a38d3ca6b58295e8, and no config.toml explains it. An ablation arm is probably set in core/config.py." | Write-Log

    exit 1

}

"config ok: $hash" | Write-Log



# **`2>&1` on a native exe is terminating under ErrorActionPreference=Stop,

# and it killed the first scheduled nightly (2026-08-28 13:15).** PowerShell

# 5.1 wraps every stderr line from a native command in a NativeCommandError

# ErrorRecord; with the preference at Stop, `cscan`'s first stderr line --

# ordinary progress output, exit code irrelevant -- became a terminating

# RemoteException. The script died before writing one line of cscan output,

# leaving a `bars_daily` row stuck at 'running' and a log holding only the

# two header lines. Now recorded in CLAUDE.md.

#

# Scoped to the call, not set for the file, so real cmdlet errors elsewhere

# still stop the script.

$prev = $ErrorActionPreference

$ErrorActionPreference = 'Continue'

& .\.venv\Scripts\cscan.exe nightly 2>&1 | Write-Log

$code = $LASTEXITCODE

$ErrorActionPreference = $prev

"=== nightly end $(Get-Date -Format 'HH:mm:ss') exit=$code ===" | Write-Log



# **Propagate the exit code, or Task Scheduler lies.**

# `$ErrorActionPreference = "Stop"` governs PowerShell cmdlet errors and has

# no effect on a native executable returning non-zero. Without this line the

# script always ends 0, so `(Get-ScheduledTaskInfo).LastTaskResult` reports

# success for a nightly that failed, and the log -- which nobody reads while

# things look fine -- is the only record.

#

# $code is captured on the line immediately after the call, because any

# native command run between the two would overwrite $LASTEXITCODE before

# it could be read.

if ($null -eq $code) { $code = 0 }

exit $code

