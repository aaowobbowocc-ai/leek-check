# 開啟 Streamlit 8501 port — 僅限 Private network（家裡 WiFi）
# 不開 Public（咖啡廳 / 公共 WiFi）
#
# 執行（需以系統管理員身份）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup_firewall_streamlit.ps1

$ruleName = "INVEST_Streamlit_8501"

# 移除舊規則
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Remove-NetFirewallRule -DisplayName $ruleName -Confirm:$false
    Write-Host "  removed old rule" -ForegroundColor Yellow
}

# 新增 inbound 規則：只允許 Private 與 Domain network
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Description "INVEST Web Dashboard (Streamlit). LAN only." `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 8501 `
    -Action Allow `
    -Profile Private,Domain `
    -Enabled True | Out-Null

Write-Host "  + 已開放 8501 (Private + Domain only, NOT Public)" -ForegroundColor Green
Write-Host ""
Write-Host "現在手機可在同 WiFi 連:" -ForegroundColor Cyan
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.PrefixOrigin -eq "Dhcp" -or $_.PrefixOrigin -eq "Manual"
} | Where-Object {
    $_.IPAddress -notlike "169.*" -and $_.IPAddress -notlike "127.*"
} | ForEach-Object {
    Write-Host "    http://$($_.IPAddress):8501" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "驗證: Test-NetConnection 192.168.8.113 -Port 8501"
