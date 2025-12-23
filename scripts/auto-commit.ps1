# auto-commit.ps1 - Automatically commit and push changes after each cycle
# Usage: .\scripts\auto-commit.ps1 -Message "Your commit message"

param(
    [string]$Message = "Auto-commit: Cycle update",
    [switch]$Push = $false
)

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "=== Auto-Commit Script ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"

# Check for changes
$status = git status --porcelain
if (-not $status) {
    Write-Host "No changes to commit." -ForegroundColor Yellow
    exit 0
}

Write-Host "`nChanges detected:" -ForegroundColor Green
git status --short

# Stage all changes
Write-Host "`nStaging changes..."
git add .

# Get timestamp for commit message
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Create commit
$fullMessage = "$Message`n`nTimestamp: $timestamp`n`n🤖 Auto-committed by Conductor Bridge"

Write-Host "`nCommitting..."
git commit -m $fullMessage

if ($Push) {
    Write-Host "`nPushing to remote..."
    git push
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Push successful!" -ForegroundColor Green
    } else {
        Write-Host "Push failed. You may need to set up the remote first." -ForegroundColor Red
    }
}

Write-Host "`nDone!" -ForegroundColor Green
