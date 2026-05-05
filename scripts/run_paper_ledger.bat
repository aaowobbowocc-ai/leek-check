@echo off
REM Daily 14:00 — Unified Paper Ledger + Daily Signal Scanner + Revenue YoY Tracker
REM (after TW market close, 13:30 + 30min buffer for data settlement)
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8

REM 1. Unified paper ledger (pair / 法人連買 tracking)
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\unified_paper_ledger.py >> logs\scheduler.log 2>&1

REM 2. Daily signal scanner (writes scanner_hits.csv + Discord push)
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\daily_signal_scanner.py --discord >> logs\scheduler.log 2>&1

REM 3. Revenue YoY paper tracker (reads scanner_hits.csv, opens/closes paper positions)
REM    必須在 scanner 之後跑（依賴 scanner_hits.csv 當日寫入）
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\revenue_yoy_paper_tracker.py >> logs\scheduler.log 2>&1

REM 4. Shioaji paper trade engine (4 strategies: pair / RYY / dealer / CRASH)
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\shioaji_paper_engine.py >> logs\scheduler.log 2>&1
