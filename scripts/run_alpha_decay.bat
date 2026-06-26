@echo off
REM Weekly Friday 14:30 — Alpha Decay Monitor with Discord push.
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\alpha_decay_monitor.py --discord >> logs\scheduler.log 2>&1
