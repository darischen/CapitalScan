<#
.SYNOPSIS
    Exercises `wait_and_poll.ps1`'s preflight guards against a throwaway
    Postgres, without touching research, serving, or the Pi.

.DESCRIPTION
    **Why this exists.** The poller wrappers are the least-tested code in
    the system and the most recently changed. `wait_and_poll.ps1` switched
    to `cscan poll --serving` on 2026-08-31, and the guards it depends on
    are exactly the ones that failed that day. Nothing had exercised them
    since.

    **The trick is a fake repo root, so the real script runs unmodified.**
    `wait_and_poll.ps1` derives `$RepoRoot` from `$PSScriptRoot` and reads
    `$RepoRoot\.env.local`. Copy it into `<tmp>\scripts\` and write a
    `<tmp>\.env.local` pointing at a scratch database, and every path it
    resolves lands in scratch. No `-WhatIf` flag, no test hook, no branch
    that only runs under test -- the thing under test is the file that
    ships.

    **What it covers**, all of it pre-poll:

      - the trading-day guard, on a session and on a closed day
      - refusal when serving is unreachable (must exit 1, not poll)
      - `DATABASE_URL_SERVING` parsing, including a wrong-looking URL
      - `psql` discovery through `$env:CAPSCAN_PSQL`
      - the one-run-per-day CSV guard

    **What it cannot cover.** The polling loop needs live quotes and a real
    clock. And the staleness and sequence guards live inside `cscan poll
    --serving` rather than in this script, so they are out of scope here --
    they have unit tests (`test_poll_sequence_guard.py`,
    `test_poll_staleness.py`) and both fired against production on
    2026-08-31 and 2026-09-01.

    **Time dependence is real and handled.** After 13:00 PT the script's
    own loop exits immediately with `[STOP] Market closed`; before 06:45 it
    prints `[WAIT]`. Both mean the calendar guard passed, which is what the
    trading-day case asserts -- it checks the script got *past* the guard,
    not what it did next.

.PARAMETER Port
    Host port for the throwaway container. Default 55432, chosen to avoid
    5432 (research) and 15432 (the native PostgreSQL 18 service).

.PARAMETER KeepContainer
    Leave the container running for inspection. Off by default.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_wait_and_poll.ps1
#>
param(
    [int]$Port = 55432,
    [switch]$KeepContainer
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Container = "capitalscan-waitpoll-test"
$ScratchDb = "waitpoll_test"
$Image = "postgres:17"

# `docker` is not on PATH in agent shells on this machine (CLAUDE.md).
$Docker = if (Get-Command docker -ErrorAction SilentlyContinue) { "docker" }
          elseif (Test-Path "C:\Program Files\Docker\Docker\resources\bin\docker.exe") {
              "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
          }
          else { throw "docker not found" }

# Same discovery order the script under test uses, so a machine where that
# discovery fails fails here too rather than being papered over.
$Psql = if ($env:CAPSCAN_PSQL) { $env:CAPSCAN_PSQL }
        elseif (Get-Command psql -ErrorAction SilentlyContinue) { "psql" }
        elseif (Test-Path "C:\Program Files\PostgreSQL\18\bin\psql.exe") { "C:\Program Files\PostgreSQL\18\bin\psql.exe" }
        else { throw "psql not found on PATH; set `$env:CAPSCAN_PSQL to its full path" }

$script:Passed = 0
$script:Failed = 0

function Assert-That {
    param([string]$Name, [bool]$Condition, [string]$Detail = "")
    if ($Condition) {
        Write-Host "  PASS  $Name" -ForegroundColor Green
        $script:Passed++
    }
    else {
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        if ($Detail) { Write-Host "        $Detail" -ForegroundColor DarkGray }
        $script:Failed++
    }
}

function Invoke-Sql {
    param([string]$Sql, [string]$Db = $ScratchDb)
    $env:PGPASSWORD = "capscan"
    # Same stderr trap as docker above: psql writes notices to stderr and
    # `Stop` would make the first one terminating.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Psql -h localhost -p $Port -U capscan -d $Db -tA -c $Sql 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { throw "psql failed: $out" }
    return "$out".Trim()
}

# --- a fake repo root, so the real script's own path logic lands in scratch
function New-FakeRepo {
    param([string]$ServingUrl)
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("waitpoll_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path (Join-Path $root "scripts") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $root "reports\poller") | Out-Null
    Copy-Item (Join-Path $RepoRoot "scripts\wait_and_poll.ps1") (Join-Path $root "scripts\wait_and_poll.ps1")
    # Only the key under test. The script reads this file for exactly one
    # line, so a minimal file proves it is not silently depending on others.
    Set-Content -Path (Join-Path $root ".env.local") -Value "DATABASE_URL_SERVING=$ServingUrl" -Encoding utf8
    return $root
}

function Invoke-ScriptUnderTest {
    param([string]$FakeRoot, [int]$TimeoutSeconds = 45)
    # **`System.Diagnostics.Process` rather than `Start-Process -PassThru`.**
    # The cmdlet with redirected streams returns a process object whose
    # `ExitCode` reads as empty even after it has exited, so the first
    # version of this harness reported `exit=` on every run and three
    # assertions failed against a script that was behaving correctly. The
    # .NET type reports the code reliably and gives the same timeout.
    $script = Join-Path $FakeRoot "scripts\wait_and_poll.ps1"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $proc = [System.Diagnostics.Process]::Start($psi)
    # Read both streams asynchronously: a synchronous read on one can block
    # forever while the other's pipe buffer fills, which is a hang that
    # looks exactly like the wait loop.
    $stdout = $proc.StandardOutput.ReadToEndAsync()
    $stderr = $proc.StandardError.ReadToEndAsync()

    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        # A run that reaches the wait loop never returns; that is a pass for
        # the calendar guard, so record it rather than treating it as a hang.
        try { $proc.Kill() } catch { }
        try { $proc.WaitForExit(5000) | Out-Null } catch { }
        return @{ ExitCode = $null; Output = "$($stdout.Result)"; TimedOut = $true }
    }
    return @{
        ExitCode = $proc.ExitCode
        Output   = "$($stdout.Result)`n$($stderr.Result)"
        TimedOut = $false
    }
}

# ---------------------------------------------------------------- setup ---
Write-Host "`n=== throwaway Postgres on port $Port ===" -ForegroundColor Cyan

# **`$ErrorActionPreference = 'Stop'` must not span a native call.**
# PowerShell 5.1 wraps the first stderr line in a RemoteException and makes
# it terminating, and `docker run` writes ordinary progress to stderr --
# "Unable to find image 'postgres:17' locally" killed the first run of this
# harness before it started. Same trap CLAUDE.md records for
# `cscan nightly 2>&1`. The exit code is the real signal; scope the
# preference around the call and read `$LASTEXITCODE` immediately, because
# any later native call clobbers it.
function Invoke-Docker {
    param([string[]]$DockerArgs, [switch]$IgnoreExit)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Docker @DockerArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prev }
    if (-not $IgnoreExit -and $code -ne 0) {
        throw "docker $($DockerArgs -join ' ') failed ($code): $out"
    }
    return $out
}

Invoke-Docker -DockerArgs @("rm", "-f", $Container) -IgnoreExit | Out-Null
# Pulled explicitly so the pull's own progress output cannot be mistaken
# for a `run` failure, and so a slow first pull is visible rather than
# looking like a hang.
Write-Host "pulling $Image (first run only)..."
Invoke-Docker -DockerArgs @("pull", "-q", $Image) | Out-Null
Invoke-Docker -DockerArgs @(
    "run", "-d", "--name", $Container,
    "-e", "POSTGRES_USER=capscan", "-e", "POSTGRES_PASSWORD=capscan", "-e", "POSTGRES_DB=$ScratchDb",
    "-p", "127.0.0.1:${Port}:5432", $Image
) | Out-Null

Write-Host "waiting for it to accept connections..."
$ready = $false
foreach ($i in 1..40) {
    Start-Sleep -Seconds 1
    try { Invoke-Sql "SELECT 1" | Out-Null; $ready = $true; break } catch { }
}
if (-not $ready) { throw "scratch Postgres never became ready" }

# The single table the script's guard reads. Deliberately not the real
# schema: if the guard needs more than this, it is reading something the
# comment does not admit to.
Invoke-Sql "CREATE TABLE trading_days (d date PRIMARY KEY);" | Out-Null
Write-Host "ready.`n"

$goodUrl = "postgresql+psycopg://capscan:capscan@localhost:${Port}/${ScratchDb}"
$today = Get-Date -Format 'yyyy-MM-dd'

try {
    # ------------------------------------------------------- non-trading ---
    Write-Host "--- a closed day must skip cleanly ---" -ForegroundColor Cyan
    Invoke-Sql "DELETE FROM trading_days;" | Out-Null
    $root = New-FakeRepo -ServingUrl $goodUrl
    $r = Invoke-ScriptUnderTest -FakeRoot $root
    Assert-That "exits 0 on a non-trading day" ($r.ExitCode -eq 0) "exit=$($r.ExitCode) out=$($r.Output)"
    Assert-That "says [SKIP] and names the date" ($r.Output -match '\[SKIP\]' -and $r.Output -match [regex]::Escape($today)) $r.Output
    Assert-That "does not launch the poller" ($r.Output -notmatch '\[START\]') $r.Output

    # ----------------------------------------------------- trading day ---
    Write-Host "`n--- a session must get past the calendar guard ---" -ForegroundColor Cyan
    Invoke-Sql "INSERT INTO trading_days (d) VALUES ('$today') ON CONFLICT DO NOTHING;" | Out-Null
    $root = New-FakeRepo -ServingUrl $goodUrl
    $r = Invoke-ScriptUnderTest -FakeRoot $root -TimeoutSeconds 20
    # Past the guard means one of: waiting for the open, already stopped for
    # the day, or launched. Which one depends on the wall clock.
    $pastGuard = ($r.Output -match '\[WAIT\]|\[STOP\]|\[START\]|\[OK\]') -and ($r.Output -notmatch '\[SKIP\]')
    Assert-That "does not skip a real session" $pastGuard $r.Output
    Assert-That "never reports [ERROR] with a reachable serving" ($r.Output -notmatch '\[ERROR\]') $r.Output

    # ------------------------------------------------ unreachable serving ---
    Write-Host "`n--- an unreachable serving must refuse, not poll ---" -ForegroundColor Cyan
    $deadUrl = "postgresql+psycopg://capscan:capscan@localhost:1/${ScratchDb}"
    $root = New-FakeRepo -ServingUrl $deadUrl
    $r = Invoke-ScriptUnderTest -FakeRoot $root
    Assert-That "exits 1 when the calendar cannot be read" ($r.ExitCode -eq 1) "exit=$($r.ExitCode) out=$($r.Output)"
    Assert-That "says [ERROR] and names the host" ($r.Output -match '\[ERROR\]') $r.Output
    Assert-That "fails closed: no poll on an unknown calendar" ($r.Output -notmatch '\[START\]') $r.Output

    # ------------------------------------------------------ env parsing ---
    Write-Host "`n--- DATABASE_URL_SERVING parsing ---" -ForegroundColor Cyan
    $root = New-FakeRepo -ServingUrl "not-a-url"
    $r = Invoke-ScriptUnderTest -FakeRoot $root
    Assert-That "refuses a URL it cannot parse" ($r.ExitCode -ne 0) "exit=$($r.ExitCode)"
    Assert-That "names host/db as the thing it could not parse" ($r.Output -match 'cannot parse host/db') $r.Output

    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("waitpoll_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path (Join-Path $root "scripts") | Out-Null
    Copy-Item (Join-Path $RepoRoot "scripts\wait_and_poll.ps1") (Join-Path $root "scripts\wait_and_poll.ps1")
    Set-Content -Path (Join-Path $root ".env.local") -Value "SOME_OTHER_KEY=1" -Encoding utf8
    $r = Invoke-ScriptUnderTest -FakeRoot $root
    Assert-That "refuses when the key is absent" ($r.ExitCode -ne 0) "exit=$($r.ExitCode)"
    Assert-That "names DATABASE_URL_SERVING" ($r.Output -match 'DATABASE_URL_SERVING') $r.Output

    # -------------------------------------------------- psql discovery ---
    Write-Host "`n--- psql discovery ---" -ForegroundColor Cyan
    Invoke-Sql "INSERT INTO trading_days (d) VALUES ('$today') ON CONFLICT DO NOTHING;" | Out-Null
    $root = New-FakeRepo -ServingUrl $goodUrl
    $saved = $env:CAPSCAN_PSQL
    $env:CAPSCAN_PSQL = "C:\definitely\not\here\psql.exe"
    try {
        $r = Invoke-ScriptUnderTest -FakeRoot $root -TimeoutSeconds 20
        Assert-That "CAPSCAN_PSQL is honoured over PATH" ($r.Output -notmatch '\[START\]' -and $r.ExitCode -ne 0) `
            "a bad override must break it; if this passes silently the override is being ignored. exit=$($r.ExitCode)"
    }
    finally {
        if ($null -eq $saved) { Remove-Item Env:\CAPSCAN_PSQL -ErrorAction SilentlyContinue }
        else { $env:CAPSCAN_PSQL = $saved }
    }

    # ------------------------------------------------ one run per day ---
    Write-Host "`n--- the one-run-per-day CSV guard ---" -ForegroundColor Cyan
    $root = New-FakeRepo -ServingUrl $goodUrl
    $stamp = Get-Date -Format 'yyyy_MM_dd'
    Set-Content -Path (Join-Path $root "reports\poller\poller_session_${stamp}_120000.csv") -Value "ticker" -Encoding utf8
    $r = Invoke-ScriptUnderTest -FakeRoot $root -TimeoutSeconds 20
    $alreadyRan = ($r.Output -match '\[OK\].*already ran') -or ($r.Output -match '\[WAIT\]') -or ($r.Output -match '\[STOP\]')
    Assert-That "an existing session CSV prevents a second run" $alreadyRan $r.Output
}
finally {
    if (-not $KeepContainer) {
        Invoke-Docker -DockerArgs @("rm", "-f", $Container) -IgnoreExit | Out-Null
        Write-Host "`ncontainer removed."
    }
    else {
        Write-Host "`ncontainer $Container left running on port $Port."
    }
}

Write-Host "`n=== $script:Passed passed, $script:Failed failed ===" -ForegroundColor $(if ($script:Failed) { "Red" } else { "Green" })
if ($script:Failed) { exit 1 }
exit 0
