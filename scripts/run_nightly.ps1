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
"=== nightly start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Tee-Object -FilePath $log

# **Refuse to run under an ablation arm's config.** `cscan nightly` resolves
# config, so an arm left set would have nightly writing events under that
# arm's hash and sweeping the wrong generation. Cheap to check, and the
# failure is otherwise silent.
$hash = & .\.venv\Scripts\python.exe -c "from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))"
$hash = $hash.Trim()
if ($hash -ne "a38d3ca6b58295e8") {
    "REFUSING: config resolves to $hash, not the serving generation a38d3ca6b58295e8. An ablation arm is probably still set in core/config.py." | Tee-Object -FilePath $log -Append
    exit 1
}
"config ok: $hash" | Tee-Object -FilePath $log -Append

& .\.venv\Scripts\cscan.exe nightly 2>&1 | Tee-Object -FilePath $log -Append
"=== nightly end $(Get-Date -Format 'HH:mm:ss') exit=$LASTEXITCODE ===" | Tee-Object -FilePath $log -Append

# **Propagate the exit code, or Task Scheduler lies.**
# `$ErrorActionPreference = "Stop"` governs PowerShell cmdlet errors and has
# no effect on a native executable returning non-zero. Without this line the
# script always ends 0, so `(Get-ScheduledTaskInfo).LastTaskResult` reports
# success for a nightly that failed, and the log -- which nobody reads while
# things look fine -- is the only record.
#
# Captured before it can be clobbered: `Tee-Object` above is a cmdlet, but
# any native call added later between there and here would overwrite
# $LASTEXITCODE.
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code
