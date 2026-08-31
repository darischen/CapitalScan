# Back-compat shim. The real wrapper is run_job.ps1; this keeps any
# scheduled task or muscle memory pointing at run_nightly.ps1 working.
& (Join-Path $PSScriptRoot 'run_job.ps1') nightly
exit $LASTEXITCODE
