@echo off
REM 每 10 分鐘 ping Render 讓 backend 常駐不睡
REM Free tier 15 min 沒人打就 spin down
REM Task Scheduler 排這支每 10 分鐘
chcp 65001 >nul
curl -s -m 15 "https://leek-check-api.onrender.com/healthz" > "%TEMP%\render-warmup.log" 2>&1
