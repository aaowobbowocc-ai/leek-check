@echo off
REM Daily 17:30 — TWSE T86 法人 + BWIBBU PER + MOPS 月營收 (10 號後)
REM 取代 FinMind subscription (5/20 到期)
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m scripts.daily_data_update >> logs\scheduler.log 2>&1

REM ETF 除權息公告 — TWSE 預告表 (primary, 5/20 後仍可用)
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\fetch_twse_exdiv.py >> logs\scheduler.log 2>&1

REM ETF 除權息公告 — FinMind (fallback for amount details, 5/20 後會 fail silently)
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\refresh_dividend_announce.py >> logs\scheduler.log 2>&1
