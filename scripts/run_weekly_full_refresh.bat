@echo off
REM Weekly Saturday 03:00 — Full universe refresh (1962 tickers, 30+ min).
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\refresh_quotes.py --full >> logs\scheduler.log 2>&1
