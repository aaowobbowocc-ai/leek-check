@echo off
REM 定期抓 yfinance 國際指數 → upload Supabase
REM Task Scheduler 排每 15/30 分鐘跑一次
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0\.."
python scripts\update_market_snapshot.py >> data\logs\market_snapshot.log 2>&1
