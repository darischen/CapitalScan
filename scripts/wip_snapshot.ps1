<#
.SYNOPSIS
Push a WIP snapshot to the throwaway wip branch with an explicit allowlist.

.DESCRIPTION
Commits changes to specified paths only (never git add -A) and force-pushes
to a throwaway wip branch. Never merged, automatically replaced on next run.

This is ADR 082 enforcement: explicit paths prevent accidental commits of
secrets, large binaries, and cache artifacts.
#>

# Explicit allowlist — add directories here only when needed
$ALLOWED_PATHS = @(
    "capitalscan/core/",
    "capitalscan/jobs/",
    "capitalscan/research/",
    "capitalscan/web/src/",
    "capitalscan/handlers/",
    "capitalscan/tests/",
    "capitalscan/docs/",
    ".github/"
)

# Navigate to repo root
$RepoRoot = git rev-parse --show-toplevel
Set-Location $RepoRoot

# Stage only allowed paths
Write-Host "Staging allowed paths..." -ForegroundColor Green
foreach ($path in $ALLOWED_PATHS) {
    if (Test-Path $path) {
        git add $path
    }
}

# Check if there are staged changes
$StagedCount = (git diff --cached --name-only | Measure-Object -Line).Lines
if ($StagedCount -eq 0) {
    Write-Host "No changes in allowed paths. Exiting." -ForegroundColor Yellow
    exit 0
}

# Create commit message
$Timestamp = Get-Date -Format "o"
$CommitMsg = "wip $Timestamp"

# Commit
Write-Host "Committing to wip branch..." -ForegroundColor Green
git commit -m $CommitMsg

# Force push to wip branch (throwaway, never merged)
Write-Host "Force-pushing to wip branch..." -ForegroundColor Green
git push -f origin HEAD:wip

Write-Host "✓ WIP snapshot complete" -ForegroundColor Green
