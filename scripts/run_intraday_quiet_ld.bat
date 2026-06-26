@echo off
REM 13:00-13:25 量縮跌停盤中掃描 → Discord push
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m scripts.intraday_quiet_limitdown_scanner --watch >> logs\scheduler.log 2>&1
