@echo off
REM Wrapper batch for Windows Task Scheduler.
REM Ensures the Python process runs in the project directory so relative
REM paths (config/, data/, logs/) resolve correctly, and redirects all
REM output (stdout + stderr) to scheduler.log.

cd /d C:\Users\USER\Desktop\INVEST
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\morning_briefing.py >> logs\scheduler.log 2>&1
