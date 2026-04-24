# Register Windows Task Scheduler for TW stock morning briefing
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows_task.ps1
#
# To delete the task later:
#   schtasks /Delete /TN "TWStockMorningBrief" /F

$TaskName  = "TWStockMorningBrief"
$ProjRoot  = "C:\Users\USER\Desktop\INVEST"
$BatFile   = "$ProjRoot\scripts\run_morning_briefing.bat"
$LogFile   = "$ProjRoot\logs\scheduler.log"
$StartTime = "08:30"

# Ensure output directories exist
New-Item -ItemType Directory -Force -Path "$ProjRoot\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$ProjRoot\data\state" | Out-Null
New-Item -ItemType Directory -Force -Path "$ProjRoot\data\paper_trades" | Out-Null

# Drop stale task if exists
schtasks /Delete /TN $TaskName /F 2>$null

# Point Task Scheduler at the .bat wrapper. The bat file handles cd + redirection
# (schtasks /TR does NOT go through a shell, so `>>` inside /TR becomes literal text).
$Command = "`"$BatFile`""

# Create weekly schedule Mon-Fri at 08:30
schtasks /Create `
    /SC WEEKLY `
    /D MON,TUE,WED,THU,FRI `
    /TN $TaskName `
    /TR $Command `
    /ST $StartTime `
    /RL HIGHEST `
    /F

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[OK] Scheduled task created: $TaskName" -ForegroundColor Green
    Write-Host "     Runs Mon-Fri at $StartTime"
    Write-Host "     Python : $Python"
    Write-Host "     Script : $Script"
    Write-Host "     Log    : $LogFile"
    Write-Host ""
    Write-Host "Verify:" -ForegroundColor Cyan
    Write-Host "   schtasks /Query /TN $TaskName /FO LIST"
    Write-Host ""
    Write-Host "Test run immediately:" -ForegroundColor Cyan
    Write-Host "   schtasks /Run /TN $TaskName"
    Write-Host ""
    Write-Host "Reminders:" -ForegroundColor Yellow
    Write-Host "   1. PC must be powered on at $StartTime (or configure BIOS Wake Timer)"
    Write-Host "   2. config\.env must contain ANTHROPIC_API_KEY and FINMIND_TOKEN"
    Write-Host "   3. data\assets.json must be populated (copy from assets.json.example)"
} else {
    Write-Host "[ERROR] Task creation failed. Re-run PowerShell as Administrator." -ForegroundColor Red
}
