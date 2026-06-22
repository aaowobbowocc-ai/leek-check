# Leek Check - Daily Strategy Precompute + Auto Push
# Usage:
#   Right-click -> Run with PowerShell
#   PowerShell: .\scripts\daily_precompute.ps1
#   Task Scheduler: powershell.exe -ExecutionPolicy Bypass -File "...\daily_precompute.ps1" -Auto

param(
    [switch]$Auto
)

# Note: $ErrorActionPreference = "Stop" would break git (git uses stderr for normal progress).
# We manually check $LASTEXITCODE after each command instead.

# cd to repo root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host $text -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

Write-Header "Leek Check - Strategy Precompute"
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

# 1. Run Python script
Write-Host "[1/3] Running scripts/precompute_strategy_results.py ..." -ForegroundColor Yellow
python "scripts\precompute_strategy_results.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python script failed (exit $LASTEXITCODE)" -ForegroundColor Red
    if (-not $Auto) { Read-Host "Press Enter to exit" }
    exit 1
}

# 2. Git add + check diff
Write-Host ""
Write-Host "[2/3] Git stage strategy_results.json ..." -ForegroundColor Yellow
git add data/strategy_results.json

git diff --cached --quiet
$hasChanges = ($LASTEXITCODE -ne 0)

if (-not $hasChanges) {
    Write-Host "   No new changes, skip commit + push" -ForegroundColor Gray
    Write-Header "Done - nothing to push"
    if (-not $Auto) { Read-Host "Press Enter to exit" }
    exit 0
}

Write-Host "   OK: changes detected" -ForegroundColor Green

# 3. Commit + push
Write-Host ""
Write-Host "[3/3] Git commit + push ..." -ForegroundColor Yellow

$commitMsg = "data: daily strategy precompute $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git commit -m $commitMsg
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: git commit failed" -ForegroundColor Red
    if (-not $Auto) { Read-Host "Press Enter to exit" }
    exit 1
}

git push origin master
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: git push failed (network or merge conflict?)" -ForegroundColor Red
    Write-Host "   Run: git pull --rebase + git push" -ForegroundColor Gray
    if (-not $Auto) { Read-Host "Press Enter to exit" }
    exit 1
}

Write-Header "Done! Cloud rebuilding, strategy market updates in ~2 min"

if (-not $Auto) { Read-Host "Press Enter to exit" }
