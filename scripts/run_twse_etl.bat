@echo off
REM TWSE daily ETL — 平日 14:30 (盤後 30 min)
REM 用法: Task Scheduler 平日 14:30
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0\.."
python -m backend.jobs.twse_daily_etl
