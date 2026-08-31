# Register (or re-register) the CapitalScan nightly / weekly / monthly
# scheduled tasks on Windows from the templates in scripts\tasks\.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 -Remove
#
# Idempotent: an existing task of the same name is replaced. The only
# machine-specific value, the repo root, is derived from this script's
# location and substituted for {{REPO}} in each template, so nothing here
# has to be edited per machine.

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$tasksDir = Join-Path $PSScriptRoot "tasks"

$jobs = @(
    @{ Name = "CapitalScan nightly"; Xml = "nightly.xml" }
    @{ Name = "CapitalScan weekly";  Xml = "weekly.xml"  }
    @{ Name = "CapitalScan monthly"; Xml = "monthly.xml" }
)

foreach ($j in $jobs) {
    $existing = Get-ScheduledTask -TaskName $j.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $j.Name -Confirm:$false
        Write-Host "removed  $($j.Name)"
    }
    if ($Remove) { continue }

    $xml = Get-Content -Raw (Join-Path $tasksDir $j.Xml)
    $xml = $xml.Replace("{{REPO}}", $RepoRoot)
    Register-ScheduledTask -TaskName $j.Name -Xml $xml -Force | Out-Null
    $info = Get-ScheduledTaskInfo -TaskName $j.Name
    Write-Host ("installed $($j.Name)  -> next run {0}" -f $info.NextRunTime)
}

if (-not $Remove) {
    Write-Host ""
    Write-Host "verify:  Get-ScheduledTask -TaskName 'CapitalScan *' | Select TaskName,State"
    Write-Host "run now: Start-ScheduledTask -TaskName 'CapitalScan nightly'"
}
