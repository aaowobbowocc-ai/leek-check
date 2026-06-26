@echo off
REM Daily 14:30 — Shioaji Paper Trade Engine (4 strategies: pair/RYY/dealer/CRASH)
REM (after TW market close + paper_ledger settlement)
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\shioaji_paper_engine.py >> logs\scheduler.log 2>&1
