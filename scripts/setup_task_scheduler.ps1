# Windows Task Scheduler setup — INVEST 自動化排程
# ----------------------------------------------------------------
# 註冊 5 個排程：
#   1. INVEST_MorningBriefing    — 平日 08:00 晨報 + Discord push
#   2. INVEST_PaperLedger        — 平日 14:00 paper ledger + signal scanner
#   3. INVEST_AlphaDecay         — 每週五 14:30 alpha decay 監控
#   4. INVEST_MonthlyHealth      — 每月 1 號 14:30 月度健康檢查
#   5. INVEST_WeeklyFullRefresh  — 每週六 03:00 全市場 1962 檔重抓
# ----------------------------------------------------------------
# 使用方式（PowerShell 開「以系統管理員身份執行」）：
#   cd C:\Users\USER\Desktop\INVEST
#   powershell -ExecutionPolicy Bypass -File scripts\setup_task_scheduler.ps1
# ----------------------------------------------------------------

$ErrorActionPreference = "Stop"
$ROOT = "C:\Users\USER\Desktop\INVEST"

function Register-InvestTask {
    param(
        [string]$Name,
        [string]$BatPath,
        [Microsoft.Management.Infrastructure.CimInstance]$Trigger,
        [string]$Description
    )

    $action = New-ScheduledTaskAction -Execute "$ROOT\$BatPath"
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    # 移除舊任務（若存在）
    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "  removed existing task: $Name" -ForegroundColor Yellow
    }

    Register-ScheduledTask `
        -TaskName $Name `
        -Action $action `
        -Trigger $Trigger `
        -Settings $settings `
        -Description $Description `
        -RunLevel Limited | Out-Null

    Write-Host "  + registered: $Name" -ForegroundColor Green
}

Write-Host "=== INVEST Task Scheduler 設定 ===" -ForegroundColor Cyan

# 1. Morning Briefing — 平日 08:00
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:00
Register-InvestTask -Name "INVEST_MorningBriefing" `
    -BatPath "scripts\run_morning_briefing.bat" `
    -Trigger $t1 `
    -Description "INVEST 晨報 + Discord push（08:00 平日）"

# 2. Paper Ledger — 平日 14:00（市場收盤後）
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 14:00
Register-InvestTask -Name "INVEST_PaperLedger" `
    -BatPath "scripts\run_paper_ledger.bat" `
    -Trigger $t2 `
    -Description "INVEST Unified Paper Ledger（14:00 收盤後）"

# 3. Alpha Decay — 每週五 14:30
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 14:30
Register-InvestTask -Name "INVEST_AlphaDecay" `
    -BatPath "scripts\run_alpha_decay.bat" `
    -Trigger $t3 `
    -Description "INVEST Alpha Decay 監控（週五 14:30）"

# 4. Monthly Health — 每月 1 號 14:30
$t4 = New-ScheduledTaskTrigger -Once -At (Get-Date "14:30") -RepetitionInterval (New-TimeSpan -Days 30)
Register-InvestTask -Name "INVEST_MonthlyHealth" `
    -BatPath "scripts\run_monthly_health.bat" `
    -Trigger $t4 `
    -Description "INVEST 月度健康檢查（每月 1 號 14:30）"

# 5. Weekly Full Refresh — 每週六 03:00 重抓全市場 1962 檔（給 scanner 用）
$t5 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 03:00
Register-InvestTask -Name "INVEST_WeeklyFullRefresh" `
    -BatPath "scripts\run_weekly_full_refresh.bat" `
    -Trigger $t5 `
    -Description "INVEST 週末全市場行情重抓（給 daily scanner / vol anomaly 用）"

Write-Host ""
Write-Host "=== 已註冊任務 ===" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object { $_.TaskName -like "INVEST_*" } | Format-Table TaskName, State, @{Label="NextRun"; Expression={(Get-ScheduledTaskInfo $_).NextRunTime}}

Write-Host ""
Write-Host "✅ 完成。可在「工作排程器」(taskschd.msc) 的 Task Scheduler Library 找到。" -ForegroundColor Green
Write-Host "   手動測試：Start-ScheduledTask -TaskName INVEST_MorningBriefing"
