# 建立 Windows 工作排程 — 每週一至週五 08:30 執行晨報
# 執行方式（以系統管理員身份）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows_task.ps1
#
# 若要刪除排程：
#   schtasks /Delete /TN "TWStockMorningBrief" /F

$TaskName  = "TWStockMorningBrief"
$ProjRoot  = "C:\Users\USER\Desktop\INVEST"
$Python    = "C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe"
$Script    = "$ProjRoot\scripts\morning_briefing.py"
$LogFile   = "$ProjRoot\logs\scheduler.log"
$StartTime = "08:30"

# 建立 logs 目錄（若不存在）
New-Item -ItemType Directory -Force -Path "$ProjRoot\logs" | Out-Null

# 建立 data\state 目錄（concept drift log）
New-Item -ItemType Directory -Force -Path "$ProjRoot\data\state" | Out-Null

# 刪除舊排程（若存在）
schtasks /Delete /TN $TaskName /F 2>$null

# 建立新排程
# /SC WEEKLY /D MON,TUE,WED,THU,FRI — 每週一至五
# /ST 08:30 — 每日 08:30
# >> 把 stdout / stderr 都寫入 scheduler.log
$Command = "`"$Python`" `"$Script`" >> `"$LogFile`" 2>&1"

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
    Write-Host "✅ 排程建立成功：$TaskName" -ForegroundColor Green
    Write-Host "   執行時間：週一至週五 $StartTime"
    Write-Host "   Python  ：$Python"
    Write-Host "   腳本    ：$Script"
    Write-Host "   日誌    ：$LogFile"
    Write-Host ""
    Write-Host "驗證指令：" -ForegroundColor Cyan
    Write-Host "   schtasks /Query /TN $TaskName /FO LIST"
    Write-Host ""
    Write-Host "手動測試（立即跑一次）：" -ForegroundColor Cyan
    Write-Host "   schtasks /Run /TN $TaskName"
    Write-Host ""
    Write-Host "注意事項：" -ForegroundColor Yellow
    Write-Host "   1. 電腦需在 08:30 開機（可到 BIOS 設定 Wake Timer）"
    Write-Host "   2. config\.env 需填入 ANTHROPIC_API_KEY / FINMIND_TOKEN"
    Write-Host "   3. data\assets.json 需從 assets.json.example 複製並填寫"
} else {
    Write-Host "❌ 排程建立失敗，請以系統管理員身份重新執行" -ForegroundColor Red
}
