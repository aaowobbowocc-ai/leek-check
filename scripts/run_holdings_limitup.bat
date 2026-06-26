@echo off
REM 08:45 — 持股漲停隔日反轉早警 (T+1 開盤前 push Discord)
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m scripts.holdings_limitup_alert >> logs\scheduler.log 2>&1
