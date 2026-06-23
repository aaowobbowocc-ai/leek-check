# Leek Check - one-click install Debug APK to Android phone
# Usage:
#   1. Phone settings: enable Developer mode + USB debugging
#   2. USB connect phone, trust this computer
#   3. PowerShell: .\install_to_phone.ps1

$ErrorActionPreference = "Continue"

$ADB = "C:\Users\USER\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$APK = "C:\Users\USER\Desktop\INVEST\mobile\android\app\build\outputs\apk\debug\app-debug.apk"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Leek Check - Install Debug APK" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $APK)) {
    Write-Host "ERROR: APK not found at $APK" -ForegroundColor Red
    Write-Host "Run gradle assembleDebug first" -ForegroundColor Gray
    exit 1
}

Write-Host "[1/3] APK found:" -ForegroundColor Yellow
$apkSize = (Get-Item $APK).Length / 1MB
Write-Host "  $APK ($([math]::Round($apkSize, 2)) MB)" -ForegroundColor Gray
Write-Host ""

Write-Host "[2/3] Detecting Android devices..." -ForegroundColor Yellow
$devices = & $ADB devices | Select-Object -Skip 1 | Where-Object { $_ -match "device$" }
$deviceCount = ($devices | Measure-Object).Count

if ($deviceCount -eq 0) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host " No device connected" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Phone setup:" -ForegroundColor Yellow
    Write-Host "  1. Settings -> About phone -> Tap Build number 7 times" -ForegroundColor Gray
    Write-Host "  2. Settings -> Developer options -> USB debugging ON" -ForegroundColor Gray
    Write-Host "  3. USB connect to PC, on phone tap 'Trust this computer'" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Then rerun this script." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "ALTERNATIVE - manual install:" -ForegroundColor Yellow
    Write-Host "  1. Upload APK to Google Drive / Telegram / Discord:" -ForegroundColor Gray
    Write-Host "     $APK" -ForegroundColor Gray
    Write-Host "  2. On phone download + open" -ForegroundColor Gray
    Write-Host "  3. Allow install from unknown sources if prompted" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

Write-Host "  Found $deviceCount device(s):" -ForegroundColor Green
$devices | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
Write-Host ""

Write-Host "[3/3] Installing APK..." -ForegroundColor Yellow
& $ADB install -r $APK
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: install failed" -ForegroundColor Red
    Write-Host "Try uninstalling first if you had older version:" -ForegroundColor Gray
    Write-Host "  $ADB uninstall tw.leekcheck.app" -ForegroundColor Gray
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Done! Find 'Leek Check' icon on your phone home screen" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Auto-launching App..." -ForegroundColor Yellow
& $ADB shell monkey -p tw.leekcheck.app -c android.intent.category.LAUNCHER 1 | Out-Null
