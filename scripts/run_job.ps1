# One wrapper for the three scheduled research jobs on Windows.
#
#   scripts\run_job.ps1 nightly
#   scripts\run_job.ps1 weekly
#   scripts\run_job.ps1 monthly
#
# The Linux equivalent is scripts/run_job.sh -- the two must stay in step.
# Everything machine-specific is derived, not written in: the repo root
# comes from this script's own location, the interpreter and CLI come from
# the repo's .venv, and the config-hash guard reads serving_config rather
# than a literal.
#
# **Why the guard.** `cscan <job>` resolves config. An ablation arm left set
# in core/config.py, or a stray scripts/config.toml a killed sweep left
# behind, would make the job write events under that arm's hash and sweep
# the wrong generation -- silently. The guard refuses unless the resolved
# hash matches what `serving_config` says is live (ADR 115). A stray
# config.toml is cleared automatically when no sweep is running; a real
# core/config.py change is not touched and stops the job.
#
# **Why 13:15 for nightly.** The poller stops at 13:00 and nightly's first
# step, `pull_live_records`, is the only path that brings the Pi's
# signal_reports / poller_sessions / poll runs back to research (ADR 158).
# Sooner after the close is a smaller gap. Nightly has a seven-day
# lookback, so a missed night self-heals; missing them indefinitely does
# not, because research stops accumulating what ADR 084 has Phase 6 reading.

param(
    [Parameter(Mandatory)]
    [ValidateSet('nightly', 'weekly', 'monthly')]
    [string]$Job
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$cscan = Join-Path $RepoRoot ".venv\Scripts\cscan.exe"
if (-not (Test-Path $py)) { Write-Error "no venv at $py -- run 'uv sync' in $RepoRoot"; exit 3 }

# One log per day under reports/<job>/. The directory is created if missing
# so a fresh clone does not lose the run to a path that is not there yet.
$logDir = Join-Path $RepoRoot "reports\$Job"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
$log = Join-Path $logDir "${Job}_$(Get-Date -Format 'yyyy_MM_dd').log"

# UTF-8, not PowerShell 5.1's default. Tee-Object has no -Encoding in 5.1
# and writes UTF-16LE, which unix tools read as gibberish -- and this log
# is read precisely when something has gone wrong.
function Write-Log {
    param([Parameter(ValueFromPipeline = $true)] $Message)
    process {
        $line = "$Message"
        Write-Host $line
        Add-Content -Path $log -Value $line -Encoding utf8
    }
}

if (Test-Path $log) { Remove-Item $log }
"=== $Job start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Write-Log

# --- config-hash guard --------------------------------------------------
$resolved = "$(& $py -c 'from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))')".Trim()

# serving_config is the authority (ADR 115): it is what the site reads and
# what `cscan db sync-config` writes. Falls back to the resolved hash when
# serving is unreachable, so a sleeping Pi is a no-op rather than a false
# refusal.
$expected = "$(& $py -c 'from capitalscan.jobs import sync as s; from sqlalchemy import text; c=s.serving_engine().connect(); print(c.execute(text(\"SELECT config_hash FROM serving_config\")).scalar_one())' 2>$null)".Trim()
if (-not $expected) {
    "could not read serving_config; skipping the config-hash guard" | Write-Log
    $expected = $resolved
}

if ($resolved -ne $expected) {
    # A stray scripts/config.toml is the likely cause and is safe to clear:
    # scripts/exit_sweep.py writes one per arm and deletes it in a finally,
    # but a hard kill skips that. It is gitignored scratch, never
    # hand-authored. Only when no sweep is running -- a live config.toml
    # belongs to an arm in flight and removing it mid-run is silent
    # corruption, strictly worse than a refused job.
    $sweeping = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*exit_sweep*' }).Count
    $stray = Join-Path $RepoRoot 'config.toml'
    if ($sweeping -gt 0) {
        "config.toml present and a sweep is running ($sweeping process(es)); leaving it alone" | Write-Log
    }
    elseif (Test-Path $stray) {
        "found a stray config.toml (sweep arm left behind); removing it and re-resolving" | Write-Log
        Remove-Item $stray
        $resolved = "$(& $py -c 'from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))')".Trim()
    }
}

if ($resolved -ne $expected) {
    "REFUSING: config resolves to $resolved, not the serving generation $expected, and no config.toml explains it. An ablation arm is probably set in core/config.py." | Write-Log
    exit 1
}
"config ok: $resolved" | Write-Log

# --- run --------------------------------------------------------------
# 2>&1 on a native exe is terminating under ErrorActionPreference=Stop:
# PowerShell 5.1 wraps every stderr line in a NativeCommandError, and the
# first one -- ordinary progress output -- becomes a terminating error.
# Scoped to the call so real cmdlet errors elsewhere still stop the script.
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $cscan $Job 2>&1 | Write-Log
$code = $LASTEXITCODE
$ErrorActionPreference = $prev
"=== $Job end $(Get-Date -Format 'HH:mm:ss') exit=$code ===" | Write-Log

# Propagate the exit code, or Task Scheduler records success for a failed
# job. $ErrorActionPreference does not touch a native non-zero exit.
# Captured on the line right after the call -- any native command between
# the two overwrites $LASTEXITCODE.
if ($null -eq $code) { $code = 0 }
exit $code
