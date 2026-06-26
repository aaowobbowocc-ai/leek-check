@echo off
REM 09:00 啟動,持續輪詢直到 13:30 自動進入睡眠
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m scripts.price_alert_monitor >> logs\scheduler.log 2>&1
