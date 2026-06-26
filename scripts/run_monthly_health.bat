@echo off
REM Monthly 1st 14:30 — Monthly Health Check.
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\monthly_health_check.py >> logs\scheduler.log 2>&1
